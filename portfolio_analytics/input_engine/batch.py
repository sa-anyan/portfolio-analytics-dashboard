"""Multi-file staging and provenance only; no deduplication or accounting."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd

from .parser import parse_dataframe, read_portfolio_file

ACCOUNT_HEADERS = {"account", "accountid", "accountidentifier", "accountnumber", "accountname",
                   "portfolio", "portfolioid", "portfolioidentifier", "portfolioname"}


def _account(value) -> str | None:
    return None if value is None or pd.isna(value) or not str(value).strip() else str(value).strip()


def _status(batch: dict) -> dict:
    parts = [p for f in batch["files"] for p in f["parts"]]
    batch["summary"] = {"files": len(batch["files"]), "accounts": sorted({p["account_id"] for p in parts if p["account_id"]}),
                        "unresolved_assignments": sum(p["account_id"] is None for p in parts),
                        "blocked_files": sum(f["status"] == "blocked" for f in batch["files"]),
                        "holdings": sum(len(p["parsed"]["normalised_dataset"]["holdings"]) for p in parts),
                        "transactions": sum(len(p["parsed"]["normalised_dataset"]["ledger"]) for p in parts),
                        "cashflows": sum(len(p["parsed"]["normalised_dataset"]["cashflows"]) for p in parts)}
    batch["status"] = "empty" if not batch["files"] else "review_required" if batch["summary"]["blocked_files"] or batch["summary"]["unresolved_assignments"] else "staged"
    return batch


def ingest_files(files: list[tuple[str, bytes]], *, session_id: str | None = None, imported_at: str | None = None) -> dict:
    """Parse each file/account independently. Preserve every economic record.

    SHA-256 is a content fingerprint for later duplicate review, never a reason
    to discard rows. Missing account IDs are not inferred from ticker/filename.
    Starting cash is deliberately zero at staging; funding decisions are later.
    """
    from portfolio_analytics.security.uploads import validate_batch
    validate_batch(files)
    session_id = session_id or str(uuid4())
    imported_at = imported_at or datetime.now(timezone.utc).isoformat()
    batch = {"session_id": session_id, "imported_at": imported_at, "files": [],
             "duplicate_review": "pending", "canonical_acceptance": "not_available_in_2B_1"}
    for ordinal, (filename, data) in enumerate(files):
        source_id = f"{session_id}:{ordinal+1}"
        item = {"source_id": source_id, "filename": filename, "fingerprint": sha256(data).hexdigest(),
                "source_type": Path(filename).suffix.lower().lstrip("."), "imported_at": imported_at,
                "row_count": 0, "byte_count": len(data), "status": "parsed", "issues": [], "parts": [], "raw_records": []}
        batch["files"].append(item)
        try:
            # Account identifiers such as 0007 must not be coerced to numbers.
            # Financial conversion remains owned by the existing normaliser.
            frame = read_portfolio_file(data, filename, dtype=str, keep_default_na=False)
            item["row_count"] = len(frame)
            item["raw_records"] = [{**r, "_provenance": {"source_id": source_id, "source_file": filename,
                "import_session": session_id, "imported_at": imported_at, "source_row": int(i)+2,
                "source_type": item["source_type"]}} for i, r in zip(frame.index, frame.where(pd.notna(frame), None).to_dict("records"))]
            if frame.empty:
                raise ValueError("Portfolio file is empty.")
            columns = [c for c in frame if re.sub(r"[^a-z0-9]", "", str(c).lower()) in ACCOUNT_HEADERS]
            if len(columns) > 1:
                raise ValueError("Multiple account identifier columns found; retain one authoritative account column before staging.")
            hints = frame[columns[0]].map(_account) if columns else pd.Series([None]*len(frame), index=frame.index)
            from portfolio_analytics.security.uploads import MAX_ACCOUNTS
            if hints.nunique() > MAX_ACCOUNTS:
                raise ValueError('Upload limit: at most 50 accounts before grouping.')
            item["account_column"] = str(columns[0]) if columns else None
            # The empty sentinel retains rows lacking an account assignment.
            for number, (_, group) in enumerate(frame.groupby(hints.fillna(""), sort=False)):
                account_id = _account(hints.loc[group.index[0]])
                try:
                    parsed = parse_dataframe(group, filename=filename, source_kind="multi_file_staging")
                except Exception as exc:
                    item["status"] = "blocked"
                    item["issues"].append({"severity": "error", "issue": str(exc), "account_id": account_id,
                                           "source_rows": [int(i)+2 for i in group.index]})
                    continue
                part_id = f"{source_id}:{number+1}"
                provenance = {"source_id": source_id, "source_file": filename, "account_id": account_id,
                              "account_assignment": "file_column" if account_id else "unresolved",
                              "import_session": session_id, "imported_at": imported_at,
                              "source_type": item["source_type"], "format": parsed["classification"]}
                parsed["source"]["provenance"] = provenance.copy()
                for kind, records in parsed["normalised_dataset"].items():
                    for ordinal, record in enumerate(records, 1):
                        record["_provenance"] = {**provenance, "record_id": f"{part_id}:{kind}:{ordinal}"}
                for raw, index in zip(parsed["user_dataset"]["records"], group.index):
                    raw["_provenance"] = {**provenance, "source_row": int(index)+2}
                    item["raw_records"][int(index)]["_provenance"] = raw["_provenance"].copy()
                part = {"part_id": part_id, "account_id": account_id, "original_account_id": account_id,
                        "account_assignment": provenance["account_assignment"], "source_rows": [int(i)+2 for i in group.index], "parsed": parsed}
                item["parts"].append(part)
                item["issues"].extend({**issue, "part_id": part_id} for issue in parsed["issues"])
                if any(i.get("severity") == "error" for i in parsed["issues"]):
                    item["status"] = "blocked"
        except Exception as exc:
            item["status"] = "blocked"
            item["issues"].append({"severity": "error", "issue": str(exc)})
    from portfolio_analytics.security.uploads import MAX_ROWS, MAX_ACCOUNTS
    if sum(f['row_count'] for f in batch['files']) > MAX_ROWS or len({p['account_id'] for f in batch['files'] for p in f['parts']}) > MAX_ACCOUNTS:
        raise ValueError('Draft exceeds the 20,000 row / 50 account limit.')
    return _status(batch)


def apply_account_assignments(batch: dict, assignments: dict[str, str], *, reason: str = "User supplied or corrected the account assignment; no identity was inferred.") -> dict:
    """Explicit corrections, with original hints retained; never merge records."""
    known = {p["part_id"] for f in batch["files"] for p in f["parts"]}
    if set(assignments) - known:
        raise ValueError("Account correction references an unknown import part.")
    result = deepcopy(batch)
    for item in result["files"]:
        for part in item["parts"]:
            if part["part_id"] not in assignments:
                continue
            account_id = _account(assignments[part["part_id"]])
            previous_account = part["account_id"]
            if account_id != previous_account:
                result.setdefault("review_history", []).append({"event": "account_correction", "part_id": part["part_id"],
                    "source_file": item["filename"], "source_id": item["source_id"], "source_fingerprint": item["fingerprint"],
                    "previous_account_id": previous_account, "account_id": account_id, "reason": reason.strip() or "User explicitly corrected the account assignment.",
                    "record_ids": [r["_provenance"].get("record_id", f"{part['part_id']}:{kind}:{ordinal}")
                                   for kind, rows in part["parsed"]["normalised_dataset"].items() for ordinal, r in enumerate(rows, 1)]})
            part.update(account_id=account_id, account_assignment="user_corrected" if account_id else "unresolved")
            provenance = part["parsed"]["source"]["provenance"]
            provenance.update(account_id=account_id, account_assignment=part["account_assignment"])
            for records in list(part["parsed"]["normalised_dataset"].values()) + [part["parsed"]["user_dataset"]["records"]]:
                for record in records:
                    record["_provenance"].update(account_id=account_id, account_assignment=part["account_assignment"])
            for row in part["source_rows"]:
                item["raw_records"][row-2]["_provenance"].update(account_id=account_id, account_assignment=part["account_assignment"])
    result.pop("review_confirmation", None)
    result["duplicate_review"] = "pending"
    return _status(result)


def append_files(batch: dict, files: list[tuple[str, bytes]]) -> dict:
    """Add immutable sources to an existing draft without erasing review history."""
    from portfolio_analytics.security.uploads import MAX_FILES, MAX_ROWS, MAX_BATCH_BYTES, MAX_ACCOUNTS, validate_batch
    validate_batch(files)
    if len(batch['files'])+len(files)>MAX_FILES or sum(f['row_count'] for f in batch['files'])>MAX_ROWS:
        raise ValueError('Staged draft exceeds the file/row limit.')
    result = deepcopy(batch)
    result["files"].extend(ingest_files(files)["files"])
    if sum(f['byte_count'] for f in result['files']) > MAX_BATCH_BYTES or sum(f['row_count'] for f in result['files']) > MAX_ROWS or len({p['account_id'] for f in result['files'] for p in f['parts']}) > MAX_ACCOUNTS:
        raise ValueError('Combined draft exceeds upload limits.')
    result.pop("review_confirmation", None)
    result["duplicate_review"] = "pending"
    return _status(result)
