from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from input_parser import (
    CURRENT_PRICE_NAMES,
    FEE_NAMES,
    GROSS_VALUE_NAMES,
    QUANTITY_NAMES,
    SECURITY_NAMES,
    TYPE_NAMES,
    DATE_NAMES,
    _find_column,
    clean_number,
    standardise_ticker,
)

TRANSACTION_ID_NAMES = [
    "transaction id", "transactionid", "txn id", "txnid", "trade id",
    "tradeid", "reference", "reference id",
]
ASSET_NAME_NAMES = [
    "asset name", "company name", "security name", "instrument name",
    "company", "asset", "description",
]
CURRENCY_NAMES = ["currency", "ccy", "denomination"]


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return str(value).strip().lower() in {"", "nan", "none", "<na>", "-", "n/a", "na", "?"}


def _norm_text(value: Any) -> str:
    if _missing(value):
        return ""
    return " ".join(str(value).strip().lower().split())


def _mapping_profile(frame: pd.DataFrame, key_col: str | None, value_col: str | None) -> dict[str, dict[str, Any]]:
    if key_col is None or value_col is None:
        return {}
    pairs = frame[[key_col, value_col]].copy()
    pairs = pairs[~pairs[key_col].map(_missing) & ~pairs[value_col].map(_missing)]
    if pairs.empty:
        return {}

    pairs["_key"] = pairs[key_col].map(_norm_text)
    pairs["_value"] = pairs[value_col].astype(str).str.strip()
    result: dict[str, dict[str, Any]] = {}

    for key, group in pairs.groupby("_key", sort=False):
        counts = group["_value"].value_counts()
        total = int(counts.sum())
        top_value = str(counts.index[0])
        top_count = int(counts.iloc[0])
        result[key] = {
            "value": top_value,
            "support": total,
            "top_support": top_count,
            "purity": float(top_count / total),
        }
    return result


def _dominant_category(series: pd.Series | None) -> dict[str, Any] | None:
    if series is None:
        return None
    values = series[~series.map(_missing)].astype(str).str.strip()
    if values.empty:
        return None
    counts = values.value_counts()
    return {
        "value": str(counts.index[0]),
        "support": int(counts.iloc[0]),
        "total": int(len(values)),
        "share": float(counts.iloc[0] / len(values)),
    }


def _learn_gross_identity(frame: pd.DataFrame, qty_col: str | None, price_col: str | None, gross_col: str | None) -> dict[str, Any] | None:
    if qty_col is None or price_col is None or gross_col is None:
        return None
    qty = frame[qty_col].map(clean_number).abs()
    price = frame[price_col].map(clean_number).abs()
    gross = frame[gross_col].map(clean_number).abs()
    base = qty * price
    mask = base.gt(0) & gross.notna()
    if int(mask.sum()) < 3:
        return None

    ratio = (gross[mask] / base[mask]).replace([np.inf, -np.inf], np.nan).dropna()
    rel_error = ((gross[mask] - base[mask]).abs() / base[mask]).replace([np.inf, -np.inf], np.nan).dropna()
    if ratio.empty:
        return None

    return {
        "support": int(len(ratio)),
        "median_ratio": float(ratio.median()),
        "median_relative_error": float(rel_error.median()) if not rel_error.empty else None,
        "p95_relative_error": float(rel_error.quantile(0.95)) if not rel_error.empty else None,
        "within_0_1pct_share": float((rel_error <= 0.001).mean()) if not rel_error.empty else 0.0,
        "within_1pct_share": float((rel_error <= 0.01).mean()) if not rel_error.empty else 0.0,
    }


def _learn_fee_pattern(frame: pd.DataFrame, qty_col: str | None, price_col: str | None, gross_col: str | None, fee_col: str | None) -> dict[str, Any] | None:
    if fee_col is None:
        return None

    fee = frame[fee_col].map(clean_number).abs()
    if gross_col is not None:
        notional = frame[gross_col].map(clean_number).abs()
    elif qty_col is not None and price_col is not None:
        notional = frame[qty_col].map(clean_number).abs() * frame[price_col].map(clean_number).abs()
    else:
        return None

    mask = notional.gt(0) & fee.notna() & fee.ge(0)
    if int(mask.sum()) < 8:
        return None

    notionals = notional[mask].astype(float)
    fees = fee[mask].astype(float)
    ratios = (fees / notionals).replace([np.inf, -np.inf], np.nan).dropna()
    if ratios.empty:
        return None

    positive_fees = fees[fees.gt(0)].round(2)
    minimum = float(positive_fees.min()) if not positive_fees.empty else 0.0

    # Learn the percentage fee from non-floor observations using a weighted
    # through-origin fit. A simple median(fee/notional) is slightly biased by
    # cents-level rounding and that tiny rate error becomes material on very
    # large trades. Weighting by notional recovers the underlying broker rate
    # much more accurately while the explicit minimum handles small trades.
    rate_mask = fees.gt(minimum + 0.005) & notionals.gt(0)
    if int(rate_mask.sum()) >= 3:
        x = notionals[rate_mask].to_numpy(dtype=float)
        y = fees[rate_mask].to_numpy(dtype=float)
        denom = float(np.dot(x, x))
        rate = float(np.dot(x, y) / denom) if denom > 0 else float(ratios.median())
    else:
        rate = float(ratios.median())

    predicted = np.maximum(minimum, notionals * rate)
    abs_error = np.abs(predicted - fees)
    rel_error = abs_error / np.maximum(fees, 0.01)

    return {
        "support": int(mask.sum()),
        "rate": rate,
        "minimum": minimum,
        "median_abs_error": float(np.median(abs_error)),
        "p95_abs_error": float(np.quantile(abs_error, 0.95)),
        "within_0_05_share": float(np.mean(abs_error <= 0.05)),
        "within_0_50_share": float(np.mean(abs_error <= 0.50)),
        "median_relative_error": float(np.median(rel_error)),
    }




def _learn_numeric_context(frame: pd.DataFrame, ticker_col: str | None, qty_col: str | None, price_col: str | None) -> dict[str, Any]:
    """Learn conservative numeric context for anomaly triage.

    These statistics are not transaction facts. They are used only to decide
    which member of an inconsistent Quantity/Price/Gross triple looks least
    plausible and therefore whether a correction can be suggested or should
    be escalated.
    """
    result: dict[str, Any] = {"quantity_values": {}, "ticker_price": {}}
    if qty_col is not None:
        qty = frame[qty_col].map(clean_number).abs().dropna()
        qty = qty[qty.gt(0)]
        if not qty.empty:
            rounded = qty.round(6)
            counts = rounded.value_counts()
            result["quantity_values"] = {str(float(k)): int(v) for k, v in counts.items()}
            result["quantity_support"] = int(len(qty))

    if ticker_col is not None and price_col is not None:
        work = pd.DataFrame({
            "ticker": frame[ticker_col].astype(str).str.strip().str.upper(),
            "price": frame[price_col].map(clean_number).abs(),
        })
        work = work[work["ticker"].ne("") & work["price"].notna() & work["price"].gt(0)]
        for ticker, group in work.groupby("ticker", sort=False):
            values = group["price"].astype(float)
            if len(values) < 3:
                continue
            median = float(values.median())
            mad = float((values - median).abs().median())
            result["ticker_price"][ticker] = {
                "support": int(len(values)),
                "median": median,
                "mad": mad,
                "min": float(values.min()),
                "max": float(values.max()),
            }
    return result

def learn_portfolio_patterns(frame: pd.DataFrame, *, profile_name: str = "uploaded_portfolio") -> dict[str, Any]:
    """Learn deterministic and high-support conventions from a known-good ledger.

    This is deliberately not a neural model. It learns auditable relationships and
    distributions that can later be used to suggest repairs with explicit evidence.
    """
    if frame is None or frame.empty:
        return {
            "profile_name": profile_name,
            "rows": 0,
            "columns": {},
            "mappings": {},
            "patterns": {},
        }

    source = frame.copy().reset_index(drop=True)
    ticker_col = _find_column(source, SECURITY_NAMES)
    qty_col = _find_column(source, QUANTITY_NAMES)
    price_col = _find_column(source, CURRENT_PRICE_NAMES)
    gross_col = _find_column(source, GROSS_VALUE_NAMES)
    fee_col = _find_column(source, FEE_NAMES)
    action_col = _find_column(source, TYPE_NAMES)
    date_col = _find_column(source, DATE_NAMES)
    txn_col = _find_column(source, TRANSACTION_ID_NAMES)
    asset_col = _find_column(source, ASSET_NAME_NAMES)
    currency_col = _find_column(source, CURRENCY_NAMES)

    asset_to_ticker = _mapping_profile(source, asset_col, ticker_col)
    ticker_to_asset = _mapping_profile(source, ticker_col, asset_col)
    dominant_currency = _dominant_category(source[currency_col] if currency_col else None)

    action_profile = None
    if action_col is not None:
        actions = source[action_col][~source[action_col].map(_missing)].astype(str).str.upper().str.strip()
        if not actions.empty:
            counts = actions.value_counts()
            action_profile = {
                "support": int(len(actions)),
                "values": {str(k): int(v) for k, v in counts.items()},
            }

    date_profile = None
    if date_col is not None:
        parsed = pd.to_datetime(source[date_col], errors="coerce")
        date_profile = {
            "support": int(parsed.notna().sum()),
            "missing": int(parsed.isna().sum()),
            "parse_success_share": float(parsed.notna().mean()),
            "min": str(parsed.min().date()) if parsed.notna().any() else None,
            "max": str(parsed.max().date()) if parsed.notna().any() else None,
        }

    profile = {
        "profile_name": profile_name,
        "rows": int(len(source)),
        "columns": {
            "ticker": ticker_col,
            "asset": asset_col,
            "quantity": qty_col,
            "price": price_col,
            "gross_value": gross_col,
            "fees": fee_col,
            "currency": currency_col,
            "action": action_col,
            "date": date_col,
            "transaction_id": txn_col,
        },
        "mappings": {
            "asset_to_ticker": asset_to_ticker,
            "ticker_to_asset": ticker_to_asset,
        },
        "patterns": {
            "dominant_currency": dominant_currency,
            "gross_identity": _learn_gross_identity(source, qty_col, price_col, gross_col),
            "fee_model": _learn_fee_pattern(source, qty_col, price_col, gross_col, fee_col),
            "numeric_context": _learn_numeric_context(source, ticker_col, qty_col, price_col),
            "actions": action_profile,
            "dates": date_profile,
        },
    }
    return profile


def save_pattern_profile(profile: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")


def load_pattern_profile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def profile_summary_table(profile: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    patterns = profile.get("patterns", {})
    mappings = profile.get("mappings", {})

    gross = patterns.get("gross_identity") or {}
    if gross:
        rows.append({
            "Pattern": "Quantity × Price ≈ Gross Value",
            "Support": gross.get("support", 0),
            "Confidence / Fit": f"{100 * gross.get('within_0_1pct_share', 0):.1f}% within 0.1%",
            "Learned Rule": f"Gross ≈ Quantity × Price × {gross.get('median_ratio', 1):.8f}",
        })

    fee = patterns.get("fee_model") or {}
    if fee:
        rows.append({
            "Pattern": "Fee schedule",
            "Support": fee.get("support", 0),
            "Confidence / Fit": f"{100 * fee.get('within_0_05_share', 0):.1f}% within $0.05",
            "Learned Rule": f"Fee ≈ max({fee.get('minimum', 0):.2f}, notional × {100 * fee.get('rate', 0):.5f}%)",
        })

    currency = patterns.get("dominant_currency") or {}
    if currency:
        rows.append({
            "Pattern": "Dominant currency",
            "Support": currency.get("support", 0),
            "Confidence / Fit": f"{100 * currency.get('share', 0):.1f}% of populated rows",
            "Learned Rule": str(currency.get("value")),
        })

    rows.append({
        "Pattern": "Asset → Ticker mappings",
        "Support": len(mappings.get("asset_to_ticker", {})),
        "Confidence / Fit": "Per-mapping purity recorded",
        "Learned Rule": "Use repeated exact relationships from known-good rows",
    })
    rows.append({
        "Pattern": "Ticker → Asset mappings",
        "Support": len(mappings.get("ticker_to_asset", {})),
        "Confidence / Fit": "Per-mapping purity recorded",
        "Learned Rule": "Use repeated exact relationships from known-good rows",
    })

    return pd.DataFrame(rows)
