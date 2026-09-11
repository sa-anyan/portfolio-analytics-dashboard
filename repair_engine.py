from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from pattern_learning import learn_portfolio_patterns

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
    "transaction id", "transactionid", "txn id", "txnid", "trade id", "tradeid", "reference", "reference id"
]
ASSET_NAME_NAMES = [
    "asset name", "company name", "security name", "instrument name", "company", "asset", "description"
]
CURRENCY_NAMES = ["currency", "ccy", "denomination"]


@dataclass
class RepairSuggestion:
    source_row: int
    field: str
    original_value: Any
    proposed_value: Any
    repair_type: str
    confidence: float
    basis: str
    column: str | None
    status: str = "SUGGESTED"


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


def _round_sensible(value: float) -> float:
    if not np.isfinite(value):
        return value
    nearest_int = round(value)
    if abs(value - nearest_int) <= max(1e-6, abs(value) * 1e-4):
        return float(nearest_int)
    return float(round(value, 6))


def _learn_consistent_mapping(frame: pd.DataFrame, key_col: str | None, value_col: str | None) -> dict[str, str]:
    if key_col is None or value_col is None:
        return {}
    pairs = frame[[key_col, value_col]].copy()
    pairs = pairs[~pairs[key_col].map(_missing) & ~pairs[value_col].map(_missing)]
    if pairs.empty:
        return {}
    pairs["_key"] = pairs[key_col].map(_norm_text)
    pairs["_value"] = pairs[value_col].astype(str).str.strip()
    mapping: dict[str, str] = {}
    for key, group in pairs.groupby("_key", sort=False):
        values = group["_value"].value_counts()
        if len(values) == 1:
            mapping[key] = str(values.index[0])
    return mapping


def _learn_fee_model(frame: pd.DataFrame, qty_col: str | None, price_col: str | None, fee_col: str | None) -> dict[str, float] | None:
    if qty_col is None or price_col is None or fee_col is None:
        return None
    q = frame[qty_col].map(clean_number).abs()
    p = frame[price_col].map(clean_number).abs()
    f = frame[fee_col].map(clean_number).abs()
    notional = q * p
    mask = notional.gt(0) & f.notna() & f.ge(0)
    if mask.sum() < 8:
        return None
    ratios = (f[mask] / notional[mask]).replace([np.inf, -np.inf], np.nan).dropna()
    if ratios.empty:
        return None
    rate = float(ratios.median())
    # Many retail-style synthetic/broker files use a minimum commission. Learn the
    # smallest repeated positive fee rather than hard-coding 0.99.
    positive = f[mask & f.gt(0)].round(2)
    minimum = float(positive.min()) if not positive.empty else 0.0
    return {"rate": rate, "minimum": minimum}




def _profile_mapping_info(profile_mapping: dict[str, Any], key: str) -> tuple[str | None, float, int]:
    info = (profile_mapping or {}).get(key)
    if info is None:
        return None, 0.0, 0
    if isinstance(info, dict):
        value = info.get("value")
        return (str(value) if value is not None else None, float(info.get("purity", 0.0)), int(info.get("support", info.get("top_support", 0))))
    return str(info), 1.0, 1


def _numeric_close(a: float, b: float, rel: float = 0.002, abs_tol: float = 0.02) -> bool:
    if not (np.isfinite(a) and np.isfinite(b)):
        return False
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b), 1.0))


def _price_plausibility(value: float, ticker: str, numeric_context: dict[str, Any]) -> float:
    """0..1 contextual plausibility. Conservative: no profile means neutral."""
    if not np.isfinite(value) or value <= 0:
        return 0.0
    stats = (numeric_context.get("ticker_price", {}) or {}).get(str(ticker).strip().upper())
    if not stats or int(stats.get("support", 0)) < 3:
        return 0.5
    med = float(stats.get("median", value))
    if med <= 0:
        return 0.5
    ratio = max(value / med, med / value)
    if ratio <= 1.5:
        return 1.0
    if ratio <= 2.5:
        return 0.75
    if ratio <= 5.0:
        return 0.30
    return 0.05


def _quantity_plausibility(value: float, numeric_context: dict[str, Any]) -> float:
    if not np.isfinite(value) or value <= 0:
        return 0.0
    nearest = round(value)
    integer_score = 1.0 if abs(value-nearest) <= max(1e-6, value*1e-4) else 0.35
    counts = numeric_context.get("quantity_values", {}) or {}
    if not counts:
        return 0.5 * integer_score + 0.25
    key = str(float(round(value, 6)))
    if key in counts:
        return 1.0
    # Unseen but sensible integer quantities remain plausible; bizarre scale values do not.
    if integer_score == 1.0 and value <= 10000:
        return 0.55
    return 0.15

def diagnose_repairs(
    frame: pd.DataFrame,
    reference_profile: dict[str, Any] | None = None,
    *,
    learn_from_current: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Find repairable missing fields without changing the source dataframe.

    RECONSTRUCTED = mathematical identity inside the row.
    INFERRED      = strong context learned from other rows in the same file.
    ESTIMATED     = learned statistical convention (for example a fee schedule).

    Dates, actions and transaction IDs are intentionally never fabricated.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(), {"suggestions": 0}

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

    # Learn auditable patterns from the usable rows in this file. An optional
    # known-good reference profile can supplement missing evidence, but the
    # uploaded file always takes precedence when it has enough support.
    learned_profile = (
        learn_portfolio_patterns(source, profile_name="current_upload")
        if learn_from_current
        else {"profile_name": "current_learning_disabled", "mappings": {}, "patterns": {}}
    )
    reference_profile = reference_profile or {}

    learned_mappings = learned_profile.get("mappings", {})
    reference_mappings = reference_profile.get("mappings", {})

    def _mapping_values(profile_mapping: dict[str, Any], min_purity: float = 0.98) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, info in (profile_mapping or {}).items():
            if isinstance(info, dict):
                purity = float(info.get("purity", 0.0))
                value = info.get("value")
                if value is not None and purity >= min_purity:
                    result[str(key)] = str(value)
            elif info is not None:
                # Backwards-compatible simple mapping.
                result[str(key)] = str(info)
        return result

    asset_to_ticker = _mapping_values(reference_mappings.get("asset_to_ticker", {}))
    asset_to_ticker.update(_mapping_values(learned_mappings.get("asset_to_ticker", {})))
    ticker_to_asset = _mapping_values(reference_mappings.get("ticker_to_asset", {}))
    ticker_to_asset.update(_mapping_values(learned_mappings.get("ticker_to_asset", {})))

    learned_patterns = learned_profile.get("patterns", {})
    reference_patterns = reference_profile.get("patterns", {})

    currency_info = learned_patterns.get("dominant_currency") or reference_patterns.get("dominant_currency")
    dominant_currency = None
    dominant_currency_share = 0.0
    if isinstance(currency_info, dict):
        dominant_currency_share = float(currency_info.get("share", 0.0))
        if dominant_currency_share >= 0.95:
            dominant_currency = currency_info.get("value")

    fee_model = learned_patterns.get("fee_model") or reference_patterns.get("fee_model")
    numeric_context = learned_patterns.get("numeric_context") or reference_patterns.get("numeric_context") or {}

    suggestions: list[RepairSuggestion] = []
    anomaly_questions: list[dict[str, Any]] = []

    for idx, row in source.iterrows():
        source_row = idx + 2
        ticker = row.get(ticker_col) if ticker_col else None
        asset = row.get(asset_col) if asset_col else None
        qty = clean_number(row.get(qty_col)) if qty_col else np.nan
        price = clean_number(row.get(price_col)) if price_col else np.nan
        gross = clean_number(row.get(gross_col)) if gross_col else np.nan
        fee = clean_number(row.get(fee_col)) if fee_col else np.nan

        if ticker_col is not None and _missing(ticker) and asset_col is not None and not _missing(asset):
            learned = asset_to_ticker.get(_norm_text(asset))
            if learned:
                suggestions.append(RepairSuggestion(
                    source_row, "Ticker", ticker, standardise_ticker(learned), "INFERRED", 0.99,
                    f"The same Asset '{asset}' maps consistently to ticker '{learned}' elsewhere in this file.", ticker_col,
                ))

        if asset_col is not None and _missing(asset) and ticker_col is not None and not _missing(ticker):
            learned_asset = ticker_to_asset.get(_norm_text(ticker))
            if learned_asset:
                suggestions.append(RepairSuggestion(
                    source_row, "Asset", asset, learned_asset, "INFERRED", 0.99,
                    f"Ticker '{ticker}' maps consistently to Asset '{learned_asset}' elsewhere in this file.", asset_col,
                ))

        if qty_col is not None and pd.isna(qty) and pd.notna(price) and price != 0 and pd.notna(gross):
            inferred_qty = _round_sensible(abs(float(gross)) / abs(float(price)))
            suggestions.append(RepairSuggestion(
                source_row, "Quantity", row.get(qty_col), inferred_qty, "RECONSTRUCTED", 0.995,
                f"Quantity reconstructed from |Gross Value| / |Price| = {abs(float(gross)):.6g} / {abs(float(price)):.6g}.", qty_col,
            ))

        if price_col is not None and pd.isna(price) and pd.notna(qty) and qty != 0 and pd.notna(gross):
            inferred_price = float(round(abs(float(gross)) / abs(float(qty)), 6))
            suggestions.append(RepairSuggestion(
                source_row, "Price", row.get(price_col), inferred_price, "RECONSTRUCTED", 0.995,
                f"Price reconstructed from |Gross Value| / |Quantity| = {abs(float(gross)):.6g} / {abs(float(qty)):.6g}.", price_col,
            ))

        if gross_col is not None and pd.isna(gross) and pd.notna(qty) and pd.notna(price):
            inferred_gross = float(round(abs(float(qty)) * abs(float(price)), 6))
            suggestions.append(RepairSuggestion(
                source_row, "Gross Value", row.get(gross_col), inferred_gross, "RECONSTRUCTED", 0.97,
                f"Gross Value reconstructed from |Quantity| × |Price| = {abs(float(qty)):.6g} × {abs(float(price)):.6g}. Exact broker settlement conventions may differ slightly.", gross_col,
            ))

        if fee_col is not None and pd.isna(fee) and fee_model is not None:
            # Use the same notional definition used when the fee model was learned.
            # If Gross Value is present it is the broker/export notional and can
            # differ slightly from Quantity × Price because of rounding or broker
            # conventions. Falling back to Quantity × Price keeps the repair useful
            # when Gross Value itself is missing.
            if pd.notna(gross):
                notional = abs(float(gross))
                notional_basis = "|Gross Value|"
            elif pd.notna(qty) and pd.notna(price):
                notional = abs(float(qty) * float(price))
                notional_basis = "|Quantity| × |Price|"
            else:
                notional = None
                notional_basis = None

            if notional is not None:
                predicted_fee = max(fee_model["minimum"], notional * fee_model["rate"])
                predicted_fee = float(round(predicted_fee, 2))
                fit_share = float(fee_model.get("within_0_05_share", 0.0))
                confidence = min(0.99, max(0.70, 0.80 + 0.19 * fit_share))
                suggestions.append(RepairSuggestion(
                    source_row, "Fees", row.get(fee_col), predicted_fee, "ESTIMATED", confidence,
                    f"Fee estimated from a learned portfolio pattern with {int(fee_model.get('support', 0))} supporting rows "
                    f"using {notional_basis}: max({fee_model['minimum']:.2f}, notional × {fee_model['rate']:.6%}). "
                    f"{100 * fit_share:.1f}% of training rows fit within $0.05.",
                    fee_col,
                ))

        if currency_col is not None and _missing(row.get(currency_col)) and dominant_currency:
            confidence = min(0.995, max(0.95, dominant_currency_share))
            suggestions.append(RepairSuggestion(
                source_row, "Currency", row.get(currency_col), dominant_currency, "INFERRED", confidence,
                f"{dominant_currency} is the dominant learned currency in {100 * dominant_currency_share:.1f}% of populated rows.", currency_col,
            ))

        # ------------------------------------------------------------------
        # Wrong-but-present anomaly repair. These suggestions are deliberately
        # conservative: they require contradictory evidence, a high-purity
        # mapping, or a strong learned portfolio convention. They are never
        # treated as mathematical facts simply because a value looks unusual.
        # ------------------------------------------------------------------
        if ticker_col is not None and asset_col is not None and not _missing(ticker) and not _missing(asset):
            expected_ticker, purity, support = _profile_mapping_info(
                learned_mappings.get("asset_to_ticker", {}) or reference_mappings.get("asset_to_ticker", {}),
                _norm_text(asset),
            )
            if expected_ticker and standardise_ticker(ticker) != standardise_ticker(expected_ticker) and purity >= 0.98 and support >= 2:
                suggestions.append(RepairSuggestion(
                    source_row, "Ticker", ticker, standardise_ticker(expected_ticker), "MAPPING_CORRECTION",
                    min(0.995, 0.90 + 0.095 * purity),
                    f"Asset '{asset}' maps to ticker '{expected_ticker}' in {support} supporting rows with {purity:.1%} purity; current ticker '{ticker}' conflicts with that mapping.",
                    ticker_col,
                ))

            expected_asset, apurity, asupport = _profile_mapping_info(
                learned_mappings.get("ticker_to_asset", {}) or reference_mappings.get("ticker_to_asset", {}),
                _norm_text(ticker),
            )
            if expected_asset and _norm_text(asset) != _norm_text(expected_asset) and apurity >= 0.98 and asupport >= 2:
                suggestions.append(RepairSuggestion(
                    source_row, "Asset", asset, expected_asset, "MAPPING_CORRECTION",
                    min(0.995, 0.90 + 0.095 * apurity),
                    f"Ticker '{ticker}' maps to Asset '{expected_asset}' in {asupport} supporting rows with {apurity:.1%} purity; current asset name conflicts with that mapping.",
                    asset_col,
                ))

        if currency_col is not None and dominant_currency and not _missing(row.get(currency_col)):
            current_currency = str(row.get(currency_col)).strip().upper()
            if current_currency != str(dominant_currency).strip().upper() and dominant_currency_share >= 0.98:
                suggestions.append(RepairSuggestion(
                    source_row, "Currency", row.get(currency_col), dominant_currency, "CONTEXT_CORRECTION",
                    min(0.99, dominant_currency_share),
                    f"Current currency '{current_currency}' conflicts with a {dominant_currency_share:.1%} dominant portfolio currency. Review before accepting in genuinely multi-currency portfolios.",
                    currency_col,
                ))

        # Quantity / Price / Gross contradiction. Only choose a culprit when
        # contextual evidence separates one candidate clearly from the others.
        if qty_col is not None and price_col is not None and gross_col is not None and pd.notna(qty) and pd.notna(price) and pd.notna(gross):
            aq, ap, ag = abs(float(qty)), abs(float(price)), abs(float(gross))
            expected_gross = aq * ap
            if aq > 0 and ap > 0 and ag > 0 and not _numeric_close(expected_gross, ag, rel=0.005, abs_tol=0.05):
                q_hat = ag / ap
                p_hat = ag / aq
                g_hat = expected_gross
                ticker_text = standardise_ticker(ticker) if ticker_col is not None and not _missing(ticker) else ""
                q_now = _quantity_plausibility(aq, numeric_context)
                q_fix = _quantity_plausibility(q_hat, numeric_context)
                p_now = _price_plausibility(ap, ticker_text, numeric_context)
                p_fix = _price_plausibility(p_hat, ticker_text, numeric_context)

                # A single field must become substantially more plausible after
                # repair. Otherwise ambiguity is safer than an automated guess.
                candidates: list[tuple[str, float, float, str, str]] = []
                if q_fix - q_now >= 0.35:
                    candidates.append(("Quantity", q_fix - q_now, _round_sensible(q_hat), qty_col, f"Gross/Price gives {q_hat:.6g}; quantity plausibility improves from {q_now:.2f} to {q_fix:.2f}."))
                if p_fix - p_now >= 0.35:
                    candidates.append(("Price", p_fix - p_now, float(round(p_hat, 6)), price_col, f"Gross/Quantity gives {p_hat:.6g}; ticker-price plausibility improves from {p_now:.2f} to {p_fix:.2f}."))
                # Gross is normally derived from Quantity × Price. Prefer this
                # only when quantity and price are both contextually plausible.
                if q_now >= 0.55 and p_now >= 0.75:
                    candidates.append(("Gross Value", 0.50, float(round(g_hat, 6)), gross_col, "Quantity and Price are both contextually plausible while Gross Value violates Quantity × Price."))

                candidates.sort(key=lambda x: x[1], reverse=True)
                if candidates and (len(candidates) == 1 or candidates[0][1] - candidates[1][1] >= 0.15):
                    field_name, margin, proposed, colname, reason = candidates[0]
                    suggestions.append(RepairSuggestion(
                        source_row, field_name, row.get(colname), proposed, "CONTRADICTION_CORRECTION",
                        min(0.985, 0.82 + 0.15 * min(1.0, margin)),
                        f"Quantity × Price ({expected_gross:.6g}) conflicts with Gross Value ({ag:.6g}). {reason}",
                        colname,
                    ))
                else:
                    anomaly_questions.append({
                        "Source Row": source_row,
                        "Field": "Quantity / Price / Gross Value",
                        "Reason": (
                            f"Quantity × Price ({expected_gross:.6g}) conflicts with Gross Value ({ag:.6g}), "
                            "but the available evidence does not identify one field safely enough to overwrite. Review the row."
                        ),
                    })

        if fee_col is not None and pd.notna(fee) and fee_model is not None:
            if pd.notna(gross):
                fee_notional = abs(float(gross))
            elif pd.notna(qty) and pd.notna(price):
                fee_notional = abs(float(qty) * float(price))
            else:
                fee_notional = None
            if fee_notional and fee_notional > 0:
                expected_fee = float(round(max(fee_model["minimum"], fee_notional * fee_model["rate"]), 2))
                actual_fee = abs(float(fee))
                tolerance = max(0.50, expected_fee * 0.20)
                if abs(actual_fee - expected_fee) > tolerance:
                    fit_share = float(fee_model.get("within_0_50_share", 0.0))
                    if fit_share >= 0.90 and int(fee_model.get("support", 0)) >= 8:
                        suggestions.append(RepairSuggestion(
                            source_row, "Fees", row.get(fee_col), expected_fee, "ESTIMATED_CORRECTION",
                            min(0.97, 0.78 + 0.18 * fit_share),
                            f"Fee {actual_fee:.2f} materially conflicts with the learned fee schedule; expected approximately {expected_fee:.2f} from {int(fee_model.get('support', 0))} supporting rows.",
                            fee_col,
                        ))

    # If two independent checks target the same cell, keep only the highest
    # confidence suggestion so the UI never presents contradictory repairs.
    if suggestions:
        best: dict[tuple[int, str], RepairSuggestion] = {}
        for suggestion in suggestions:
            key = (suggestion.source_row, str(suggestion.column))
            if key not in best or suggestion.confidence > best[key].confidence:
                best[key] = suggestion
        suggestions = list(best.values())

    table = pd.DataFrame([s.__dict__ for s in suggestions])
    if not table.empty:
        table = table.rename(columns={
            "source_row": "Source Row",
            "field": "Field",
            "original_value": "Original Value",
            "proposed_value": "Proposed Value",
            "repair_type": "Repair Type",
            "confidence": "Confidence",
            "basis": "Basis",
            "column": "Source Column",
            "status": "Status",
        })

    missing_questions: list[dict[str, Any]] = list(anomaly_questions)
    audit_warnings: list[dict[str, Any]] = []
    for idx, row in source.iterrows():
        source_row = idx + 2
        if date_col is not None and _missing(row.get(date_col)):
            missing_questions.append({"Source Row": source_row, "Field": "Date", "Reason": "Transaction date is missing and cannot be safely invented."})
        if action_col is not None and _missing(row.get(action_col)):
            missing_questions.append({"Source Row": source_row, "Field": "Action", "Reason": "BUY/SELL direction is missing and cannot be inferred safely from quantity sign."})
        if txn_col is not None and _missing(row.get(txn_col)):
            audit_warnings.append({"Source Row": source_row, "Field": "Transaction ID", "Reason": "Transaction ID is missing. This weakens duplicate detection but does not block accounting, and no ID is fabricated."})

    diagnostics = {
        "suggestions": int(len(table)),
        "reconstructed": int((table["Repair Type"] == "RECONSTRUCTED").sum()) if not table.empty else 0,
        "inferred": int((table["Repair Type"] == "INFERRED").sum()) if not table.empty else 0,
        "estimated": int((table["Repair Type"] == "ESTIMATED").sum()) if not table.empty else 0,
        "manual_questions": pd.DataFrame(missing_questions),
        "audit_warnings": pd.DataFrame(audit_warnings),
        "detected_columns": {
            "ticker": ticker_col, "asset": asset_col, "quantity": qty_col, "price": price_col,
            "gross_value": gross_col, "fees": fee_col, "currency": currency_col,
            "date": date_col, "action": action_col, "transaction_id": txn_col,
        },
        "fee_model": fee_model,
        "dominant_currency": dominant_currency,
        "pattern_profile": learned_profile,
        "reference_profile_used": bool(reference_profile),
    }
    return table, diagnostics


def apply_repairs(
    frame: pd.DataFrame,
    suggestions: pd.DataFrame,
    accepted_indices: list[int] | None = None,
    edited_values: dict[int, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply explicitly accepted repair suggestions and return (repaired, audit)."""
    result = frame.copy().reset_index(drop=True)
    if suggestions is None or suggestions.empty:
        return result, pd.DataFrame()

    accepted = set(accepted_indices if accepted_indices is not None else suggestions.index.tolist())
    edited_values = edited_values or {}
    log_rows = []

    for sidx, suggestion in suggestions.iterrows():
        if sidx not in accepted:
            continue
        source_row = int(suggestion["Source Row"])
        row_index = source_row - 2
        column = suggestion["Source Column"]
        if column not in result.columns or row_index not in result.index:
            continue
        old = result.at[row_index, column]
        new = edited_values.get(sidx, suggestion["Proposed Value"])
        result.at[row_index, column] = new
        log_rows.append({
            "Source Row": source_row,
            "Field": suggestion["Field"],
            "Original Value": old,
            "Applied Value": new,
            "Repair Type": suggestion["Repair Type"],
            "Confidence": suggestion["Confidence"],
            "Basis": suggestion["Basis"],
        })

    return result, pd.DataFrame(log_rows)
