"""Deterministic source review, reversible selections, and a non-financial gate.

Original data is never deleted. Views mark selected exclusions; canonical
acceptance/accounting is deliberately absent from this module.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
import re

import pandas as pd


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if k != "_provenance"}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _hash(value):
    return sha256(json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _index(batch):
    sources, parts, records = {}, {}, {}
    for source in batch["files"]:
        sid = source["source_id"]
        sources[sid] = source
        for part in source["parts"]:
            pid = part["part_id"]
            dates = set()
            for number in part["source_rows"]:
                for key, value in source["raw_records"][number-2].items():
                    if re.sub(r"[^a-z0-9]", "", str(key).lower()) in {"valuationdate", "snapshotdate", "asof", "asofdate"}:
                        date = pd.to_datetime(value, errors="coerce")
                        if pd.notna(date):
                            dates.add(date.date().isoformat())
            parts[pid] = {"source_id": sid, "account_id": part["account_id"], "format": part["parsed"]["classification"], "snapshot_dates": sorted(dates)}
            for kind, rows in part["parsed"]["normalised_dataset"].items():
                for ordinal, row in enumerate(rows, 1):
                    rid = row.get("_provenance", {}).get("record_id", f"{pid}:{kind}:{ordinal}")
                    records[rid] = {"record_id": rid, "source_id": sid, "source_file": source["filename"],
                                    "source_fingerprint": source["fingerprint"], "part_id": pid,
                                    "account_id": part["account_id"], "kind": kind, "data": _clean(row)}
    basis = {sid: _hash({"fingerprint": f["fingerprint"], "parts": {p: d for p, d in parts.items() if d["source_id"] == sid},
                         "records": {r: d for r, d in records.items() if d["source_id"] == sid}}) for sid, f in sources.items()}
    return sources, parts, records, basis


def _detect(sources, parts, records):
    issues = []
    by_part = defaultdict(list)
    for row in records.values():
        by_part[row["part_id"]].append(row)

    def add(code, classification, title, reason, sids, rids=(), pids=(), actions=(), recommendation="", evidence=None):
        sids, rids, pids = sorted(set(sids)), sorted(set(rids)), sorted(set(pids))
        accounts = sorted({parts[p]["account_id"] for p in pids if parts[p]["account_id"]})
        identifier = _hash([code, sids, rids, pids, accounts])[:24]
        issues.append({"id": identifier, "code": code, "classification": classification,
                       "blocking": classification != "legitimate", "title": title, "reason": reason,
                       "source_ids": sids, "record_ids": rids, "part_ids": pids, "accounts": accounts,
                       "actions": list(actions), "recommendation": recommendation, "evidence": evidence or {}})

    for sid, source in sources.items():
        pids = [p for p, d in parts.items() if d["source_id"] == sid]
        rids = [r for r, d in records.items() if d["source_id"] == sid]
        if source["status"] == "blocked":
            add("source_invalid", "conflict", f"{source['filename']}: malformed or unsupported source",
                "Parsing errors must be corrected or this source explicitly excluded.", [sid], rids, pids, ["exclude_sources", "defer"], evidence={"parser_issues": source["issues"]})
    for pid, part in parts.items():
        if not part["account_id"]:
            add("account_missing", "conflict", "Account assignment is unresolved", "Correct the account assignment before resolving identity conflicts.",
                [part["source_id"]], [r["record_id"] for r in by_part[pid]], [pid], ["exclude_sources", "defer"])
    fingerprints, names = defaultdict(list), defaultdict(list)
    for sid, source in sources.items():
        fingerprints[source["fingerprint"]].append(sid)
        names[source["filename"]].append(sid)
    for sids in fingerprints.values():
        if len(sids) < 2:
            continue
        pids = [p for p, d in parts.items() if d["source_id"] in sids]
        assignments = [{d["account_id"] for d in parts.values() if d["source_id"] == sid} for sid in sids]
        disjoint = all(a and None not in a for a in assignments) and all(not a & b for i, a in enumerate(assignments) for b in assignments[i+1:])
        ambiguous_accounts = any(not a or None in a for a in assignments)
        add("exact_file_reupload", "legitimate" if disjoint else "possible_duplicate" if ambiguous_accounts else "confirmed_duplicate",
            "Identical file content across distinct accounts" if disjoint else "Exact file re-upload detected",
            "SHA-256 bytes match. Explicitly distinct account assignments can represent legitimate separate accounts; matching or unresolved accounts require review.",
            sids, [r for r, d in records.items() if d["source_id"] in sids], pids,
            [] if disjoint else ["exclude_sources", "keep_both", "defer"],
            "Exclude the repeated source unless separate economic activity is evidenced; no source is removed automatically.", {"fingerprint": sources[sids[0]]["fingerprint"]})
    for sids in names.values():
        if len({sources[s]["fingerprint"] for s in sids}) > 1:
            add("same_name_changed", "legitimate", "Same filename, different content", "Filename equality is not proof of duplicate data.", sids)
    by_account = defaultdict(list)
    for pid, part in parts.items():
        if part["account_id"] and by_part[pid]:
            by_account[part["account_id"]].append(pid)
    for account, pids in by_account.items():
        snapshots = [p for p in pids if parts[p]["format"] == "holdings"]
        ledgers = [p for p in pids if parts[p]["format"] == "ledger"]
        if len(snapshots) > 1:
            add("snapshot_conflict", "conflict", f"{account}: multiple holdings snapshots",
                "Choose one authoritative snapshot. Multiple snapshots must not be added as if they were separate accounts.",
                [parts[p]["source_id"] for p in snapshots], [r["record_id"] for p in snapshots for r in by_part[p]], snapshots,
                ["choose_snapshot", "exclude_sources", "defer"], "Select the appropriate valuation date; unknown dates are not inferred from filenames or purchase dates.",
                {"snapshots": {p: parts[p]["snapshot_dates"] or ["unknown"] for p in snapshots}})
        for p in snapshots:
            if len(parts[p]["snapshot_dates"]) > 1:
                add("snapshot_dates_ambiguous", "conflict", f"{account}: several valuation dates inside one source",
                    "Split this source into distinct dated snapshots before review; purchase dates are not snapshot dates.",
                    [parts[p]["source_id"]], [r["record_id"] for r in by_part[p]], [p], ["exclude_sources", "defer"], evidence={"dates": parts[p]["snapshot_dates"]})
        if snapshots and ledgers:
            add("snapshot_ledger_overlap", "conflict", f"{account}: snapshot and ledger overlap",
                "These may describe the same positions and cash. Keep only the authoritative input format for this account; reconciling both is not implemented in this milestone.",
                [parts[p]["source_id"] for p in snapshots+ledgers], [r["record_id"] for p in snapshots+ledgers for r in by_part[p]], snapshots+ledgers,
                ["choose_format", "exclude_sources", "defer"], "Choose holdings snapshots or transaction ledgers, preserving other accounts.")
        ranges = {}
        for p in ledgers:
            dates = [str(r["data"]["Date"]) for r in by_part[p] if r["data"].get("Date")]
            if dates:
                ranges[p] = (min(dates), max(dates))
        for i, a in enumerate(ledgers):
            for b in ledgers[i+1:]:
                if a in ranges and b in ranges and max(ranges[a][0], ranges[b][0]) <= min(ranges[a][1], ranges[b][1]):
                    add("ledger_period_overlap", "possible_duplicate", f"{account}: ledger periods overlap",
                        "Date overlap is not proof of duplicate trades. Inspect records; acknowledge distinct remaining activity or exclude a repeated source.",
                        [parts[a]["source_id"], parts[b]["source_id"]], [r["record_id"] for p in [a, b] for r in by_part[p]], [a, b],
                        ["keep_both", "exclude_sources", "defer"], evidence={"date_ranges": {a: ranges[a], b: ranges[b]}})
    identifiers, economic, across_accounts = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in records.values():
        account = row["account_id"]
        if not account:
            continue
        data = dict(row["data"])
        transaction_id = str(data.pop("Transaction ID", "") or "").strip()
        signature = _hash([row["kind"], data])
        across_accounts[signature].append(row)
        if row["kind"] != "holdings":
            if transaction_id:
                identifiers[(account, transaction_id)].append(row)
            economic[(account, signature)].append(row)
    for (account, tid), rows in identifiers.items():
        if len(rows) < 2:
            continue
        identical = len({_hash([r["kind"], r["data"]]) for r in rows}) == 1
        add("transaction_duplicate" if identical else "transaction_id_conflict", "confirmed_duplicate" if identical else "conflict",
            f"{account}: {'repeated transaction identifier' if identical else 'transaction identifier has conflicting details'}",
            "Same account and explicit transaction ID. Identical economic fields are strong duplicate evidence; conflicting fields need a source/version decision. IDs are not assumed globally unique across accounts.",
            [r["source_id"] for r in rows], [r["record_id"] for r in rows], [r["part_id"] for r in rows],
            ["exclude_records", "keep_both", "defer"], "Exclude a repeated record if its identity is confirmed; retaining reused IDs requires a documented reason.", {"transaction_id": tid})
    for (account, _), rows in economic.items():
        ids = [str(r["data"].get("Transaction ID") or "").strip() for r in rows]
        if len(rows) > 1 and (not all(ids) or len(set(ids)) > 1):
            add("transaction_match_ambiguous", "possible_duplicate", f"{account}: matching economic transaction fields",
                "Ticker/date/quantity/price/currency/fees match, but stable identity is missing or different. Separate executions can be legitimate. Nothing is deleted automatically.",
                [r["source_id"] for r in rows], [r["record_id"] for r in rows], [r["part_id"] for r in rows],
                ["keep_both", "exclude_records", "defer"], "Keep both if they are distinct executions; otherwise explicitly identify the duplicate record.")
    # Informational, never duplicate blocking, even if broker transaction IDs match.
    holdings_by_ticker = defaultdict(list)
    for row in records.values():
        if row["kind"] == "holdings":
            holdings_by_ticker[row["data"].get("Ticker")].append(row)
    for rows in list(holdings_by_ticker.values()) + list(across_accounts.values()):
        if len({r["account_id"] for r in rows if r["account_id"]}) > 1:
            add("legitimate_cross_account", "legitimate", "Repeated activity across distinct accounts",
                "Distinct account provenance is preserved. A repeated security or transaction is not a duplicate merely because economic fields match.",
                [r["source_id"] for r in rows], [r["record_id"] for r in rows], [r["part_id"] for r in rows])
    unique = {i["id"]: i for i in issues}
    priority = {"source_invalid": 0, "account_missing": 0, "exact_file_reupload": 1,
                "transaction_duplicate": 2, "transaction_id_conflict": 2, "snapshot_conflict": 2,
                "snapshot_ledger_overlap": 2, "snapshot_dates_ambiguous": 2,
                "transaction_match_ambiguous": 3, "ledger_period_overlap": 4}
    return sorted(unique.values(), key=lambda i: (not i["blocking"], priority.get(i["code"], 5), i["code"], i["id"]))


def review_view(batch: dict) -> dict:
    sources, parts, records, basis = _index(batch)
    excluded_sources, excluded_parts, excluded_records = set(), set(), set()
    active_decisions, invalidated = {}, []
    for identifier, decision in batch.get("review_decisions", {}).items():
        if decision["basis"] != {s: basis.get(s) for s in decision["basis"]}:
            invalidated.append(identifier)
            continue
        active_decisions[identifier] = decision
        excluded_sources.update(decision.get("excluded_source_ids", []))
        excluded_parts.update(decision.get("excluded_part_ids", []))
        excluded_records.update(decision.get("excluded_record_ids", []))
    kept = {r: d for r, d in records.items() if d["source_id"] not in excluded_sources and d["part_id"] not in excluded_parts and r not in excluded_records}
    active_parts = {p: d for p, d in parts.items() if d["source_id"] not in excluded_sources and p not in excluded_parts and any(r["part_id"] == p for r in kept.values())}
    active_sources = {s: d for s, d in sources.items() if s not in excluded_sources and (d["status"] == "blocked" or any(p["source_id"] == s for p in active_parts.values()))}
    issues = _detect(active_sources, active_parts, kept)
    unresolved = []
    for issue in issues:
        decision = active_decisions.get(issue["id"])
        issue["decision"] = decision["action"] if decision else None
        if issue["blocking"] and (not decision or decision["action"] != "keep_both"):
            unresolved.append(issue["id"])
    digest = _hash({"basis": basis, "decisions": active_decisions, "kept": sorted(kept)})
    can_progress = bool(kept) and not unresolved and not invalidated
    return {"issues": issues, "unresolved_ids": unresolved, "invalidated_decisions": invalidated,
            "can_progress": can_progress, "review_confirmed": can_progress and batch.get("review_confirmation") == digest,
            "digest": digest, "records": list(records.values()), "retained_record_ids": sorted(kept),
            "excluded_record_ids": sorted(set(records)-set(kept)), "excluded_source_ids": sorted(excluded_sources),
            "active_decisions": active_decisions, "source_basis": basis}


def decide(batch: dict, identifier: str, action: str, *, reason: str, targets=()) -> dict:
    """Record explicit decisions with original evidence. No destructive mutation."""
    view = review_view(batch)
    issue = next((i for i in view["issues"] if i["id"] == identifier), None)
    if not issue or action not in issue["actions"]:
        raise ValueError("Review issue/action is no longer valid; refresh the exceptions.")
    if not reason.strip():
        raise ValueError("A reason is required for every review decision.")
    targets = list(targets)
    decision = {"issue_id": identifier, "action": action, "reason": reason.strip(), "issue": deepcopy(issue),
                "basis": {s: view["source_basis"][s] for s in issue["source_ids"]},
                "excluded_source_ids": [], "excluded_part_ids": [], "excluded_record_ids": [],
                "provenance": [r for r in view["records"] if r["record_id"] in issue["record_ids"]]}
    # Invalid sources may have raw rows but no normalised financial records.
    # Their exclusion still needs self-contained source and raw-row evidence.
    decision["source_provenance"] = [{"source_id": f["source_id"], "source_file": f["filename"],
        "source_fingerprint": f["fingerprint"], "account_ids": [p["account_id"] for p in f["parts"]] or [None],
        "raw_record_ids": [f"{f['source_id']}:raw:{r['_provenance']['source_row']}" for r in f["raw_records"]]}
        for f in batch["files"] if f["source_id"] in issue["source_ids"]]
    if action == "exclude_sources":
        if not targets or not set(targets) <= set(issue["source_ids"]):
            raise ValueError("Select affected source files to exclude.")
        decision["excluded_source_ids"] = targets
    elif action == "exclude_records":
        if not targets or not set(targets) < set(issue["record_ids"]):
            raise ValueError("Exclude affected records while retaining at least one candidate.")
        decision["excluded_record_ids"] = targets
    elif action == "choose_snapshot":
        if len(targets) != 1 or targets[0] not in issue["part_ids"]:
            raise ValueError("Choose exactly one affected snapshot.")
        decision["excluded_part_ids"] = sorted(set(issue["part_ids"])-set(targets))
    elif action == "choose_format":
        if len(targets) != 1 or targets[0] not in {"holdings", "ledger"}:
            raise ValueError("Choose holdings or ledger as the authoritative format.")
        _, parts, _, _ = _index(batch)
        decision["excluded_part_ids"] = [p for p in issue["part_ids"] if parts[p]["format"] != targets[0]]
    result = deepcopy(batch)
    result.setdefault("review_decisions", {})[identifier] = decision
    result.setdefault("review_history", []).append({"event": "decision", **deepcopy(decision)})
    result.pop("review_confirmation", None)
    result["duplicate_review"] = "in_review"
    return result


def reverse_decision(batch: dict, identifier: str, *, reason: str) -> dict:
    if identifier not in batch.get("review_decisions", {}) or not reason.strip():
        raise ValueError("An existing decision and reversal reason are required.")
    result = deepcopy(batch)
    old = result["review_decisions"].pop(identifier)
    result.setdefault("review_history", []).append({"event": "reversal", "reason": reason.strip(), "previous_decision": old})
    result.pop("review_confirmation", None)
    result["duplicate_review"] = "in_review"
    return result


def confirm_review(batch: dict) -> dict:
    view = review_view(batch)
    if not view["can_progress"]:
        raise ValueError("Resolve or reverse invalidated decisions and blocking conflicts before confirming review.")
    result = deepcopy(batch)
    result["review_confirmation"] = view["digest"]
    result["duplicate_review"] = "confirmed"
    result.setdefault("review_history", []).append({"event": "confirmation", "digest": view["digest"],
        "reason": "User confirmed the resolved import review; financial acceptance remains pending.",
        "retained_record_ids": view["retained_record_ids"], "excluded_record_ids": view["excluded_record_ids"],
        "sources": [{"source_id": f["source_id"], "source_file": f["filename"], "source_fingerprint": f["fingerprint"],
                     "accounts": [p["account_id"] for p in f["parts"]]} for f in batch["files"]]})
    return result
