from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CorruptionRecord:
    row_index: int
    transaction_id: str | None
    field: str
    original_value: Any
    corrupted_value: Any
    corruption_type: str


def _txn_id(df: pd.DataFrame, idx: int) -> str | None:
    for name in ("Transaction_ID", "Transaction ID", "TxnID", "txn_id"):
        if name in df.columns:
            value = df.at[idx, name]
            return None if pd.isna(value) else str(value)
    return None


def _log(df: pd.DataFrame, idx: int, field: str, new_value: Any, kind: str) -> CorruptionRecord:
    return CorruptionRecord(
        row_index=int(idx),
        transaction_id=_txn_id(df, idx),
        field=field,
        original_value=df.at[idx, field],
        corrupted_value=new_value,
        corruption_type=kind,
    )


def remove_values(
    df: pd.DataFrame,
    field: str,
    *,
    fraction: float = 0.05,
    seed: int = 42,
    eligible_rows: Iterable[int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Blank a deterministic random sample from one column and return an audit log."""
    result = df.copy(deep=True)
    if field not in result.columns:
        raise KeyError(field)

    pool = np.array(list(eligible_rows) if eligible_rows is not None else result.index.tolist(), dtype=int)
    if len(pool) == 0:
        return result, pd.DataFrame()

    count = max(1, int(round(len(pool) * fraction)))
    count = min(count, len(pool))
    rng = np.random.default_rng(seed)
    chosen = np.sort(rng.choice(pool, size=count, replace=False))

    records = []
    for idx in chosen:
        records.append(_log(result, int(idx), field, np.nan, f"missing_{field.lower()}"))
        result.at[int(idx), field] = np.nan

    return result, pd.DataFrame([asdict(r) for r in records])


def corrupt_ticker_formats(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply deterministic broker/export ticker noise without changing security identity."""
    result = df.copy(deep=True)
    records = []
    if "Ticker" not in result.columns or result.empty:
        return result, pd.DataFrame()

    variants = [
        lambda x: f" {str(x).lower()} ",
        lambda x: f"{x}.US",
        lambda x: f"{x}-US",
    ]
    for pos, idx in enumerate(result.index[: min(9, len(result))]):
        old = result.at[idx, "Ticker"]
        new = variants[pos % len(variants)](old)
        records.append(_log(result, int(idx), "Ticker", new, "ticker_format_noise"))
        result.at[idx, "Ticker"] = new
    return result, pd.DataFrame([asdict(r) for r in records])


def corrupt_numeric_strings(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn clean numerics into common broker text representations."""
    result = df.copy(deep=True)
    records = []
    for field in ("Price", "Gross_Value", "Fees"):
        if field in result.columns:
            result[field] = result[field].astype(object)
    specs = [
        ("Price", lambda v: f"${float(v):,.2f}"),
        ("Gross_Value", lambda v: f"{float(v):,.2f} USD"),
        ("Fees", lambda v: f"${float(v):.2f}"),
    ]
    for offset, (field, formatter) in enumerate(specs):
        if field not in result.columns:
            continue
        indices = [i for i in result.index if pd.notna(result.at[i, field])][:3]
        for idx in indices[offset:offset + 1] or indices[:1]:
            old = result.at[idx, field]
            new = formatter(old)
            records.append(_log(result, int(idx), field, new, "numeric_text_noise"))
            result.at[idx, field] = new
    return result, pd.DataFrame([asdict(r) for r in records])


def corrupt_action_aliases(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = df.copy(deep=True)
    records = []
    if "Type" not in result.columns:
        return result, pd.DataFrame()
    aliases = {"BUY": "bought", "SELL": "sold"}
    for idx in result.index[: min(12, len(result))]:
        old = str(result.at[idx, "Type"]).strip().upper()
        if old in aliases:
            new = aliases[old]
            records.append(_log(result, int(idx), "Type", new, "action_alias"))
            result.at[idx, "Type"] = new
    return result, pd.DataFrame([asdict(r) for r in records])


def corrupt_date_formats(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = df.copy(deep=True)
    records = []
    if "Date" not in result.columns:
        return result, pd.DataFrame()
    formats = ["%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%d-%m-%Y"]
    for pos, idx in enumerate(result.index[: min(8, len(result))]):
        dt = pd.Timestamp(result.at[idx, "Date"])
        new = dt.strftime(formats[pos % len(formats)])
        records.append(_log(result, int(idx), "Date", new, "date_format_noise"))
        result.at[idx, "Date"] = new
    return result, pd.DataFrame([asdict(r) for r in records])


def duplicate_transaction_ids(df: pd.DataFrame, *, seed: int = 7, groups: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Force several distinct rows to share an existing transaction ID."""
    result = df.copy(deep=True)
    field = "Transaction_ID" if "Transaction_ID" in result.columns else None
    if field is None or len(result) < groups * 2:
        return result, pd.DataFrame()
    rng = np.random.default_rng(seed)
    chosen = rng.choice(result.index.to_numpy(), size=groups * 2, replace=False)
    records = []
    for a, b in chosen.reshape(-1, 2):
        new = result.at[a, field]
        records.append(_log(result, int(b), field, new, "duplicate_transaction_id"))
        result.at[b, field] = new
    return result, pd.DataFrame([asdict(r) for r in records])


def rename_headers(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    mapping = {
        "Ticker": "tickerest_2",
        "Quantity": "net_shares_held",
        "Date": "broker_transaction_date",
        "Type": "transaction_direction",
        "Price": "last_price_used",
        "Fees": "broker_commission_usd",
        "Transaction_ID": "txn_id_reference",
    }
    mapping = {k: v for k, v in mapping.items() if k in df.columns}
    return df.rename(columns=mapping), mapping


def generate_corrupted_dataset(df: pd.DataFrame, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce one reproducible mixed-noise dataset and a full corruption log."""
    current = df.copy(deep=True)
    logs = []

    current, log = corrupt_ticker_formats(current); logs.append(log)
    current, log = corrupt_numeric_strings(current); logs.append(log)
    current, log = corrupt_action_aliases(current); logs.append(log)
    current, log = corrupt_date_formats(current); logs.append(log)

    # Missing values are chosen only where the remaining fields allow deterministic repair.
    if {"Quantity", "Price", "Gross_Value"}.issubset(current.columns):
        eligible = current.index[current["Price"].notna() & current["Gross_Value"].notna()].tolist()
        current, log = remove_values(current, "Quantity", fraction=0.02, seed=seed, eligible_rows=eligible); logs.append(log)
        eligible = current.index[current["Quantity"].notna() & current["Gross_Value"].notna()].tolist()
        current, log = remove_values(current, "Price", fraction=0.02, seed=seed + 1, eligible_rows=eligible); logs.append(log)
        eligible = current.index[current["Quantity"].notna() & current["Price"].notna()].tolist()
        current, log = remove_values(current, "Gross_Value", fraction=0.02, seed=seed + 2, eligible_rows=eligible); logs.append(log)

    current, log = duplicate_transaction_ids(current, seed=seed + 3, groups=2); logs.append(log)
    combined = pd.concat([x for x in logs if x is not None and not x.empty], ignore_index=True) if logs else pd.DataFrame()
    return current, combined
