#______________________________________________________________________________
# HUMAN-STYLE AUTO-CLEANING ENGINE FOR MESSY PORTFOLIO TRANSACTION LEDGERS
#______________________________________________________________________________
#
# Purpose
# -------
# Clean real-world portfolio transaction exports conservatively and audibly.
#
# The cleaner separates hard evidence from interpretation:
#   - Hard evidence: transaction ID, date, ticker, action, quantity, price.
#   - Supporting evidence: notes, fees, account labels, price history/trends.
#
# It NEVER silently guesses an ambiguous accounting event. Every source row is
# assigned a status and reason in the audit table.
#
# For the attached messy_transactions.xlsx benchmark, the target price table is
# constructed with these rules:
#   1. If a transaction ID occurs more than once, ALL occurrences are excluded.
#   2. BUY contributes +abs(shares).
#   3. SELL contributes -abs(shares).
#   4. DIV / DIVIDEND contributes the ORIGINAL signed share quantity.
#   5. A position-affecting row must have a valid date, ticker, shares and price.
#   6. last_price_used is the price on the chronologically latest accepted
#      position-affecting row for each canonical ticker.
#
# This module contains NO Streamlit/UI code. UI state belongs in app.py.
#______________________________________________________________________________

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from portfolio_analytics.ingestion.input_parser import (
    CASH_AMOUNT_NAMES,
    CURRENT_PRICE_NAMES,
    DATE_NAMES,
    FEE_NAMES,
    GROSS_VALUE_NAMES,
    QUANTITY_NAMES,
    SECURITY_NAMES,
    TYPE_NAMES,
    _find_column,
    clean_number,
    standardise_ticker,
)


#______________________________________________________________________________
# EXTRA COLUMN NAME OPTIONS
#______________________________________________________________________________

TRANSACTION_ID_NAMES = [
    "transaction id",
    "transactionid",
    "txn id",
    "txnid",
    "trade id",
    "tradeid",
    "reference",
    "reference id",
]

NOTES_NAMES = [
    "notes",
    "note",
    "memo",
    "description",
    "details",
    "comment",
    "comments",
    "narrative",
]

ACCOUNT_NAMES = [
    "account",
    "account name",
    "account type",
    "portfolio",
    "book",
]


#______________________________________________________________________________
# FINANCIAL ACTION ONTOLOGY
#______________________________________________________________________________
#
# The canonical action is kept separate from the accounting interpretation.
# This lets the cleaner recognise broker terminology without pretending that
# every recognised word has the same economic meaning.
#______________________________________________________________________________

ACTION_ALIASES = {
    # Purchases / positive trade direction
    "BUY": "BUY",
    "BOT": "BUY",
    "BOUGHT": "BUY",
    "PURCHASE": "BUY",
    "PURCHASED": "BUY",
    "BTO": "BUY",
    "BUY TO OPEN": "BUY",
    "BUY TO COVER": "BUY",
    "BTC": "BUY",  # common broker shorthand: buy to cover

    # Sales / negative trade direction
    "SELL": "SELL",
    "SOLD": "SELL",
    "SALE": "SELL",
    "SLD": "SELL",
    "STO": "SELL",
    "SELL TO OPEN": "SELL",
    "SELL TO CLOSE": "SELL",

    # Distributions / dividends
    "DIV": "DIVIDEND",
    "DIVI": "DIVIDEND",
    "DIVIDEND": "DIVIDEND",
    "CASH DIVIDEND": "DIVIDEND",
    "STOCK DIVIDEND": "DIVIDEND",
    "DISTRIBUTION": "DIVIDEND",
    "DIST": "DIVIDEND",
    "DRIP": "DIVIDEND",
    "REINVEST": "DIVIDEND",
    "REINVESTMENT": "DIVIDEND",

    # Cash events
    "DEPOSIT": "DEPOSIT",
    "DEP": "DEPOSIT",
    "CASH DEPOSIT": "DEPOSIT",
    "CONTRIBUTION": "DEPOSIT",
    "WITHDRAWAL": "WITHDRAWAL",
    "WITHDRAW": "WITHDRAWAL",
    "WD": "WITHDRAWAL",
    "CASH WITHDRAWAL": "WITHDRAWAL",

    # Transfers / journals. Generic transfer is intentionally ambiguous.
    "TRANSFER": "TRANSFER",
    "XFER": "TRANSFER",
    "TFR": "TRANSFER",
    "TRANSFER IN": "TRANSFER IN",
    "TRANSFER-IN": "TRANSFER IN",
    "TRANSFER OUT": "TRANSFER OUT",
    "TRANSFER-OUT": "TRANSFER OUT",
    "JOURNAL IN": "TRANSFER IN",
    "JOURNAL OUT": "TRANSFER OUT",

    # Corporate actions / other recognised financial events
    "SPLIT": "SPLIT",
    "STOCK SPLIT": "SPLIT",
    "REVERSE SPLIT": "REVERSE SPLIT",
    "MERGER": "MERGER",
    "SPINOFF": "SPINOFF",
    "SPIN-OFF": "SPINOFF",
    "CONVERSION": "CONVERSION",
    "EXCHANGE": "EXCHANGE",
    "TENDER": "TENDER",
    "RIGHTS": "RIGHTS",
    "OPTION EXERCISE": "OPTION EXERCISE",
    "ASSIGNMENT": "ASSIGNMENT",
    "EXPIRY": "EXPIRY",
    "EXPIRATION": "EXPIRY",
    "RETURN OF CAPITAL": "RETURN OF CAPITAL",
    "ROC": "RETURN OF CAPITAL",
    "CAPITAL GAIN": "CAPITAL GAIN",
    "CAPITAL GAIN DISTRIBUTION": "CAPITAL GAIN",
    "INTEREST": "INTEREST",
    "COUPON": "INTEREST",
    "WITHHOLDING TAX": "WITHHOLDING TAX",
    "TAX": "WITHHOLDING TAX",
    "FEE": "FEE",
    "ADJUSTMENT": "ADJUSTMENT",
}

POSITION_ACTIONS = {"BUY", "SELL", "DIVIDEND"}
CASH_ACTIONS = {"DEPOSIT", "WITHDRAWAL", "DIVIDEND"}

# Broker/export suffixes that represent a market decoration, not a new security.
_US_SUFFIX_PATTERN = re.compile(r"(?i)(\.US|-US)$")

# Notes that give context but are not authoritative accounting instructions.
DUPLICATE_NOTE_TERMS = ("duplicate?", "possible duplicate", "duplicate")
UNCERTAIN_NOTE_TERMS = ("??", "see email", "check", "confirm", "unknown")
DRIP_NOTE_TERMS = ("drip", "reinvest", "reinvestment")
REBALANCE_NOTE_TERMS = ("rebalance", "rebalancing")
TAX_LOSS_NOTE_TERMS = ("tax loss", "tax-loss", "harvest")
TRANSFER_NOTE_TERMS = ("transferred", "transfer", "old broker", "journal")


#______________________________________________________________________________
# LOW-LEVEL HELPERS
#______________________________________________________________________________

def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _clean_transaction_id(value: Any) -> str:
    return _text(value).upper()


def _clean_ticker(value: Any) -> str:
    """Return a canonical security ticker without common US broker suffixes."""
    if value is None or pd.isna(value):
        return ""

    ticker = standardise_ticker(value)
    ticker = _US_SUFFIX_PATTERN.sub("", ticker).strip().upper()

    # Apply aliases again after stripping the broker suffix.
    return standardise_ticker(ticker) if ticker else ""


def _clean_action(value: Any, action_overrides: dict[str, str] | None = None) -> str:
    if value is None or pd.isna(value):
        return ""

    text = re.sub(r"\s+", " ", str(value).strip().upper())
    if action_overrides and text in action_overrides:
        mapped = action_overrides[text]
        return "" if mapped == "IGNORE" else mapped
    return ACTION_ALIASES.get(text, text)


def _smart_clean_date(value: Any, date_preference: str = "auto") -> pd.Timestamp:
    """
    Parse broker dates conservatively.

    Supports:
      - real pandas/datetime values
      - Excel serial dates
      - ISO dates
      - US-style slash dates
      - day-first dates
      - written dates such as 'Mar 11, 2026'

    Impossible dates remain NaT. Nothing is invented.
    """
    if value is None or pd.isna(value):
        return pd.NaT

    if isinstance(value, pd.Timestamp):
        return value

    # Excel serial-date convention used by ordinary .xlsx exports.
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        number = float(value)
        if 1 <= number <= 100000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(number, unit="D")

    text = str(value).strip()
    if not text:
        return pd.NaT

    # User preference is used only for genuinely ambiguous numeric dates.
    # Unambiguous dates are still parsed normally.
    if date_preference == "dayfirst":
        order = [True, False]
    elif date_preference == "monthfirst":
        order = [False, True]
    else:
        order = [False, True]

    for dayfirst in order:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst)
        if pd.notna(parsed):
            return pd.Timestamp(parsed)

    return pd.NaT


def _is_ambiguous_numeric_date(value: Any) -> bool:
    """Flag dates such as 04/12/23 where both month/day interpretations exist."""
    if value is None or pd.isna(value):
        return False

    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if not match:
        return False

    first = int(match.group(1))
    second = int(match.group(2))
    return 1 <= first <= 12 and 1 <= second <= 12 and first != second


def _clean_numeric(value: Any) -> float:
    """Use the app's existing currency/number cleaner without turning missing into zero."""
    return clean_number(value)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    key = text.lower()
    return any(term.lower() in key for term in terms)


def _append_reason(current: str, new_reason: str) -> str:
    if not new_reason:
        return current
    if not current:
        return new_reason
    if new_reason in current:
        return current
    return f"{current}; {new_reason}"


def _column_or_none(frame: pd.DataFrame, names: list[str]) -> str | None:
    return _find_column(frame, names)


def _series_or_default(
    frame: pd.DataFrame,
    column: str | None,
    default: Any = None,
) -> pd.Series:
    if column is None:
        return pd.Series([default] * len(frame), index=frame.index, dtype=object)
    return frame[column]


#______________________________________________________________________________
# SEMANTIC / HUMAN-STYLE VALIDATION HELPERS
#______________________________________________________________________________

def _note_evidence(note: str, action: str) -> tuple[list[str], list[str]]:
    """Return (supporting_notes, warnings) from free-text transaction notes."""
    support: list[str] = []
    warnings: list[str] = []

    if not note:
        return support, warnings

    if _contains_any(note, DRIP_NOTE_TERMS):
        if action == "DIVIDEND":
            support.append("note supports dividend reinvestment / share distribution")
        else:
            warnings.append("note mentions DRIP/reinvestment but action is not Dividend")

    if _contains_any(note, DUPLICATE_NOTE_TERMS):
        warnings.append("note mentions possible duplicate; transaction ID evidence remains authoritative")

    if _contains_any(note, UNCERTAIN_NOTE_TERMS):
        warnings.append("note indicates uncertainty and should be human-reviewed")

    if _contains_any(note, REBALANCE_NOTE_TERMS):
        support.append("note indicates portfolio rebalancing")

    if _contains_any(note, TAX_LOSS_NOTE_TERMS):
        if action == "SELL":
            support.append("note is consistent with a tax-loss sale")
        else:
            warnings.append("note mentions tax-loss harvesting but action is not Sell")

    if _contains_any(note, TRANSFER_NOTE_TERMS):
        warnings.append("note references a transfer/broker migration; explicit action still takes precedence")

    return support, warnings


def _signed_position_quantity(action: str, quantity: float) -> float:
    """Translate a cleaned transaction into its share-position movement."""
    if pd.isna(quantity):
        return np.nan

    quantity = float(quantity)

    if action == "BUY":
        return abs(quantity)
    if action == "SELL":
        return -abs(quantity)
    if action == "DIVIDEND":
        # For stock dividends / DRIP / share adjustments, the original sign is
        # meaningful: positive credits shares, negative reverses/removes shares.
        return quantity
    if action == "TRANSFER IN":
        return abs(quantity)
    if action == "TRANSFER OUT":
        return -abs(quantity)

    return np.nan


#______________________________________________________________________________
# CORE AUDIT BUILDER
#______________________________________________________________________________

def _build_audit_table(frame: pd.DataFrame, preferences: dict[str, Any] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Build one auditable record per source row.

    Hard exclusions are based only on objective evidence required for the
    benchmark price-table transformation. Notes/trends create warnings but do
    not silently delete an otherwise valid financial record.
    """
    preferences = preferences or {}
    date_preference = str(preferences.get("date_preference", "auto"))
    duplicate_policy = str(preferences.get("duplicate_policy", "quarantine_all"))
    incomplete_policy = str(preferences.get("incomplete_policy", "reject"))
    action_overrides = preferences.get("action_overrides", {}) or {}

    source = frame.copy().reset_index(drop=True)

    # Locate semantic columns using flexible substring matching from input_parser.
    txn_col = _column_or_none(source, TRANSACTION_ID_NAMES)
    date_col = _column_or_none(source, DATE_NAMES)
    ticker_col = _column_or_none(source, SECURITY_NAMES)
    action_col = _column_or_none(source, TYPE_NAMES)
    quantity_col = _column_or_none(source, QUANTITY_NAMES)
    price_col = _column_or_none(source, CURRENT_PRICE_NAMES)
    fee_col = _column_or_none(source, FEE_NAMES)
    notes_col = _column_or_none(source, NOTES_NAMES)
    account_col = _column_or_none(source, ACCOUNT_NAMES)

    detected = {
        "transaction_id": txn_col,
        "date": date_col,
        "ticker": ticker_col,
        "action": action_col,
        "quantity": quantity_col,
        "price": price_col,
        "fee": fee_col,
        "notes": notes_col,
        "account": account_col,
    }

    audit = pd.DataFrame(index=source.index)
    audit["Source Row"] = source.index + 2  # +1 header, +1 Excel 1-based indexing

    audit["Original Transaction ID"] = _series_or_default(source, txn_col, "")
    audit["Original Date"] = _series_or_default(source, date_col, None)
    audit["Original Ticker"] = _series_or_default(source, ticker_col, "")
    audit["Original Action"] = _series_or_default(source, action_col, "")
    audit["Original Shares"] = _series_or_default(source, quantity_col, np.nan)
    audit["Original Price"] = _series_or_default(source, price_col, np.nan)
    audit["Original Fee"] = _series_or_default(source, fee_col, np.nan)
    audit["Account"] = _series_or_default(source, account_col, "")
    audit["Notes"] = _series_or_default(source, notes_col, "")

    audit["Transaction ID"] = audit["Original Transaction ID"].map(_clean_transaction_id)
    audit["Date"] = audit["Original Date"].map(lambda v: _smart_clean_date(v, date_preference))
    audit["Ticker"] = audit["Original Ticker"].map(_clean_ticker)
    audit["Action"] = audit["Original Action"].map(lambda v: _clean_action(v, action_overrides))
    audit["Shares"] = audit["Original Shares"].map(_clean_numeric)
    audit["Price"] = audit["Original Price"].map(_clean_numeric)
    audit["Fee"] = audit["Original Fee"].map(_clean_numeric)
    audit["Account"] = audit["Account"].map(_text)
    audit["Notes"] = audit["Notes"].map(_text)

    # Preserve missing fees as missing for audit; fee accounting can later fill 0
    # if the source genuinely omits a fee.
    audit["Signed Shares"] = [
        _signed_position_quantity(action, quantity)
        for action, quantity in zip(audit["Action"], audit["Shares"])
    ]

    # Fully blank source rows are never silently dropped.
    source_text = source.astype(str).apply(lambda col: col.str.strip().str.lower())
    blank_mask = source.isna().all(axis=1) | source_text.isin(["", "nan", "none", "<na>"]).all(axis=1)
    audit["Blank Source Row"] = blank_mask

    # Hard duplicate transaction IDs: all repeated occurrences are quarantined.
    nonblank_ids = audit.loc[audit["Transaction ID"].ne(""), "Transaction ID"]
    id_counts = nonblank_ids.value_counts()
    duplicate_ids = set(id_counts[id_counts.gt(1)].index)
    if duplicate_policy == "keep_first":
        audit["Hard Duplicate ID"] = (
            audit["Transaction ID"].ne("")
            & audit.duplicated(subset=["Transaction ID"], keep="first")
        )
    else:
        audit["Hard Duplicate ID"] = audit["Transaction ID"].isin(duplicate_ids)

    # Economic duplicate fingerprint is a warning only. Two legitimate executions
    # can share identical economics while having different transaction IDs.
    audit["Economic Fingerprint"] = list(zip(
        audit["Date"],
        audit["Ticker"],
        audit["Action"],
        audit["Shares"].abs(),
        audit["Price"],
        audit["Fee"],
        audit["Account"].str.upper(),
    ))
    fingerprint_counts = audit["Economic Fingerprint"].value_counts(dropna=False)
    repeated_fingerprints = set(fingerprint_counts[fingerprint_counts.gt(1)].index)
    audit["Possible Economic Duplicate"] = audit["Economic Fingerprint"].isin(repeated_fingerprints)

    # Base status/reason. Warnings never override a hard rejection.
    statuses: list[str] = []
    reasons: list[str] = []
    supporting: list[str] = []
    warnings_col: list[str] = []

    for _, row in audit.iterrows():
        status = "AUTO_ACCEPT"
        reason = ""
        support_notes, warning_notes = _note_evidence(row["Notes"], row["Action"])

        if bool(row["Blank Source Row"]):
            status = "REJECT"
            reason = _append_reason(reason, "fully blank source row")

        if bool(row["Hard Duplicate ID"]):
            status = "REJECT"
            reason = _append_reason(reason, "transaction ID occurs more than once; all occurrences quarantined")

        if pd.isna(row["Date"]):
            status = "REJECT"
            reason = _append_reason(reason, "missing or invalid transaction date")
        elif _is_ambiguous_numeric_date(row["Original Date"]):
            warning_notes.append(f"ambiguous numeric date interpreted using {date_preference} preference")

        if row["Action"] in POSITION_ACTIONS:
            if not row["Ticker"]:
                status = "REJECT"
                reason = _append_reason(reason, "position-affecting event has no usable ticker")
            if pd.isna(row["Shares"]):
                status = "REJECT"
                reason = _append_reason(reason, "position-affecting event has no usable share quantity")
            if pd.isna(row["Price"]):
                status = "REJECT"
                reason = _append_reason(reason, "position-affecting event has no usable price for price-table mode")

        elif row["Action"] in {"TRANSFER IN", "TRANSFER OUT"}:
            # Direction is known, but the attached benchmark does not use transfer
            # rows in the price table. Keep visible for human accounting review.
            status = "REVIEW"
            reason = _append_reason(reason, "directed security transfer recognised but excluded from benchmark price-table mode")

        elif row["Action"] == "TRANSFER":
            status = "REVIEW"
            reason = _append_reason(reason, "generic transfer has no direction; do not guess in/out")

        elif row["Action"] in {"DEPOSIT", "WITHDRAWAL", "INTEREST", "RETURN OF CAPITAL", "CAPITAL GAIN", "WITHHOLDING TAX", "FEE"}:
            status = "REVIEW"
            reason = _append_reason(reason, "recognised cash/corporate event is not a share-position event")

        elif row["Action"] in {"SPLIT", "REVERSE SPLIT", "MERGER", "SPINOFF", "CONVERSION", "EXCHANGE", "TENDER", "RIGHTS", "OPTION EXERCISE", "ASSIGNMENT", "EXPIRY", "ADJUSTMENT"}:
            status = "REVIEW"
            reason = _append_reason(reason, "corporate action requires event-specific accounting; not auto-guessed")

        elif not row["Action"]:
            status = "REJECT"
            reason = _append_reason(reason, "missing transaction action/type")

        else:
            status = "REVIEW"
            reason = _append_reason(reason, f"unrecognised action '{row['Action']}'")

        if bool(row["Possible Economic Duplicate"]) and not bool(row["Hard Duplicate ID"]):
            warning_notes.append("economically identical row detected; unique transaction IDs mean it is not auto-deleted")

        # Notes can lower confidence without changing accounting facts.
        if warning_notes and status == "AUTO_ACCEPT":
            status = "ACCEPT_WITH_WARNING"

        statuses.append(status)
        reasons.append(reason if reason else "valid position-affecting transaction")
        supporting.append("; ".join(support_notes))
        warnings_col.append("; ".join(warning_notes))

    audit["Status"] = statuses
    audit["Reason"] = reasons
    audit["Supporting Evidence"] = supporting
    audit["Warnings"] = warnings_col

    if incomplete_policy == "review":
        incomplete_terms = (
            "missing or invalid transaction date",
            "no usable ticker",
            "no usable share quantity",
            "no usable price",
            "missing transaction action/type",
        )
        incomplete_mask = audit["Status"].eq("REJECT") & audit["Reason"].map(
            lambda text: any(term in str(text) for term in incomplete_terms)
        ) & ~audit["Hard Duplicate ID"] & ~audit["Blank Source Row"]
        audit.loc[incomplete_mask, "Status"] = "REVIEW"

    # Only objective, usable position rows contribute to the benchmark price table.
    audit["Include in Position Analysis"] = (
        audit["Status"].isin(["AUTO_ACCEPT", "ACCEPT_WITH_WARNING"])
        & audit["Action"].isin(POSITION_ACTIONS)
        & audit["Date"].notna()
        & audit["Ticker"].ne("")
        & audit["Shares"].notna()
        & audit["Price"].notna()
        & ~audit["Hard Duplicate ID"]
    )

    # Fee intelligence: warnings only.
    notional = audit["Shares"].abs() * audit["Price"].abs()
    audit["Fee Ratio"] = np.where(
        notional.gt(0) & audit["Fee"].notna(),
        audit["Fee"].abs() / notional,
        np.nan,
    )

    fee_outlier_mask = audit["Fee Ratio"].gt(0.10)
    audit["Fee Outlier"] = fee_outlier_mask.fillna(False)
    for idx in audit.index[fee_outlier_mask.fillna(False)]:
        audit.at[idx, "Warnings"] = _append_reason(
            audit.at[idx, "Warnings"],
            "fee exceeds 10% of transaction notional",
        )
        if audit.at[idx, "Status"] == "AUTO_ACCEPT":
            audit.at[idx, "Status"] = "ACCEPT_WITH_WARNING"

    metadata = {
        "detected_columns": detected,
        "duplicate_transaction_ids": sorted(duplicate_ids),
        "rows_read": int(len(source)),
        "blank_rows": int(blank_mask.sum()),
        "preferences": preferences,
    }
    return audit, metadata


#______________________________________________________________________________
# PRICE-TREND INTELLIGENCE
#______________________________________________________________________________

def _add_price_trend_intelligence(audit: pd.DataFrame) -> pd.DataFrame:
    """
    Compare each accepted ticker price with its previous accepted observation.

    Trend checks generate warnings only. A price is never overwritten merely
    because it looks unusual; splits, mergers and genuine market moves exist.
    """
    result = audit.copy()
    result["Previous Price"] = np.nan
    result["Previous Price Date"] = pd.NaT
    result["Days Since Previous Price"] = np.nan
    result["Price Change %"] = np.nan
    result["Price Outlier"] = False

    usable = result[result["Include in Position Analysis"]].copy()
    usable = usable.sort_values(["Ticker", "Date", "Source Row"], kind="stable")

    for ticker, group in usable.groupby("Ticker", sort=False):
        previous_price: float | None = None
        previous_date: pd.Timestamp | None = None

        for idx, row in group.iterrows():
            price = float(row["Price"])
            date = pd.Timestamp(row["Date"])

            if previous_price is not None and previous_date is not None and previous_price != 0:
                days = int((date - previous_date).days)
                change = price / previous_price - 1.0

                result.at[idx, "Previous Price"] = previous_price
                result.at[idx, "Previous Price Date"] = previous_date
                result.at[idx, "Days Since Previous Price"] = days
                result.at[idx, "Price Change %"] = change

                # Conservative warning thresholds. They do not reject the row.
                suspicious = (
                    (days <= 30 and abs(change) >= 1.00)
                    or (days <= 365 and abs(change) >= 3.00)
                )

                if suspicious:
                    result.at[idx, "Price Outlier"] = True
                    result.at[idx, "Warnings"] = _append_reason(
                        result.at[idx, "Warnings"],
                        f"unusual price move of {change:.1%} over {days} days; review for split/corporate action/data error",
                    )
                    if result.at[idx, "Status"] == "AUTO_ACCEPT":
                        result.at[idx, "Status"] = "ACCEPT_WITH_WARNING"

            previous_price = price
            previous_date = date

    return result


#______________________________________________________________________________
# BUILD THE CLEANED PRICE TABLE
#______________________________________________________________________________

def _price_table_from_audit(audit: pd.DataFrame) -> pd.DataFrame:
    """
    Produce: ticker | net_shares | last_price_used

    This implements the exact benchmark logic validated against the attached
    messy_transactions.xlsx and price_table_used(1).csv.
    """
    usable = audit[audit["Include in Position Analysis"]].copy()

    if usable.empty:
        return pd.DataFrame(columns=["ticker", "net_shares", "last_price_used"])

    usable = usable.sort_values(
        ["Ticker", "Date", "Source Row"],
        kind="stable",
    )

    quantities = (
        usable.groupby("Ticker", sort=True)["Signed Shares"]
        .sum()
        .rename("net_shares")
    )

    latest = (
        usable.groupby("Ticker", sort=True, group_keys=False)
        .tail(1)
        .set_index("Ticker")["Price"]
        .rename("last_price_used")
    )

    result = pd.concat([quantities, latest], axis=1).reset_index()
    result = result.rename(columns={"Ticker": "ticker"})

    return result[["ticker", "net_shares", "last_price_used"]]


def build_clean_price_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Public convenience function for directly producing the cleaned price table."""
    _, report = auto_clean_ledger(frame)
    return report["price_table"].copy()


#______________________________________________________________________________
# CONVERT ACCEPTED ROWS BACK INTO THE ORIGINAL LEDGER SHAPE
#______________________________________________________________________________

def _build_cleaned_pipeline_frame(
    original: pd.DataFrame,
    audit: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return a cleaned dataframe using the original source columns.

    This preserves compatibility with app.py / input_parser.py while removing
    hard-rejected and review-only rows from the automatic accounting path.
    DIVIDEND rows remain labelled DIVIDEND; they are not disguised as BUY.
    """
    if original is None or original.empty:
        return original.copy()

    source = original.copy().reset_index(drop=True)
    keep = audit["Include in Position Analysis"].copy()
    result = source.loc[keep.values].copy()

    date_col = _column_or_none(result, DATE_NAMES)
    ticker_col = _column_or_none(result, SECURITY_NAMES)
    action_col = _column_or_none(result, TYPE_NAMES)
    quantity_col = _column_or_none(result, QUANTITY_NAMES)
    price_col = _column_or_none(result, CURRENT_PRICE_NAMES)
    fee_col = _column_or_none(result, FEE_NAMES)

    kept_audit = audit.loc[keep].reset_index(drop=True)
    result = result.reset_index(drop=True)

    if date_col is not None:
        result[date_col] = kept_audit["Date"]
    if ticker_col is not None:
        result[ticker_col] = kept_audit["Ticker"]
    if action_col is not None:
        result[action_col] = kept_audit["Action"]
    if quantity_col is not None:
        result[quantity_col] = kept_audit["Shares"]
    if price_col is not None:
        result[price_col] = kept_audit["Price"]
    if fee_col is not None:
        result[fee_col] = kept_audit["Fee"]

    return result


#______________________________________________________________________________
# AUTO-CLEAN ENTRY POINT
#______________________________________________________________________________

def auto_clean_ledger(
    frame: pd.DataFrame,
    preferences: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Human-style cleaning entry point used by the dashboard.

    Returns:
      cleaned_frame
          High-confidence, position-affecting rows in the original ledger shape.

      report
          Full audit information and the derived clean price table.

    Important:
      The cleaner never silently deletes a source row. Rejected/review rows are
      preserved in report['audit_table'] with explicit reasons.
    """
    if frame is None:
        frame = pd.DataFrame()

    if frame.empty:
        empty_price = pd.DataFrame(columns=["ticker", "net_shares", "last_price_used"])
        report = {
            "rows_read": 0,
            "blank_rows_removed": 0,
            "tickers_normalised": 0,
            "actions_normalised": 0,
            "dates_reparsed": 0,
            "numbers_coerced": 0,
            "hard_duplicate_rows": 0,
            "accepted_rows": 0,
            "warning_rows": 0,
            "review_rows": 0,
            "rejected_rows": 0,
            "silently_dropped_rows": 0,
            "duplicate_transaction_ids": [],
            "detected_columns": {},
            "audit_table": pd.DataFrame(),
            "accepted_position_rows": pd.DataFrame(),
            "review_rows_table": pd.DataFrame(),
            "rejected_rows_table": pd.DataFrame(),
            "non_trading_rows": pd.DataFrame(),
            "price_table": empty_price,
        }
        return frame.copy(), report

    audit, metadata = _build_audit_table(frame, preferences=preferences)
    audit = _add_price_trend_intelligence(audit)
    price_table = _price_table_from_audit(audit)
    cleaned_frame = _build_cleaned_pipeline_frame(frame, audit)

    # Compatibility metrics retained from the earlier auto_clean.py report.
    ticker_before = audit["Original Ticker"].map(_text).str.strip().str.upper()
    ticker_after = audit["Ticker"].fillna("")
    action_before = audit["Original Action"].map(_text).str.strip().str.upper()
    action_after = audit["Action"].fillna("")
    date_before = audit["Original Date"].astype(str)
    date_after = audit["Date"].astype(str)

    numeric_originals = pd.concat(
        [
            audit["Original Shares"],
            audit["Original Price"],
            audit["Original Fee"],
        ],
        ignore_index=True,
    )
    numbers_coerced = int(
        numeric_originals.map(lambda x: isinstance(x, str) and x.strip() != "").sum()
    )

    accepted_mask = audit["Status"].eq("AUTO_ACCEPT")
    warning_mask = audit["Status"].eq("ACCEPT_WITH_WARNING")
    review_mask = audit["Status"].eq("REVIEW")
    reject_mask = audit["Status"].eq("REJECT")

    reconciled_count = int(
        accepted_mask.sum()
        + warning_mask.sum()
        + review_mask.sum()
        + reject_mask.sum()
    )

    non_trading_mask = ~audit["Action"].isin(POSITION_ACTIONS)

    report: dict[str, Any] = {
        "rows_read": int(len(audit)),
        "blank_rows_removed": int(audit["Blank Source Row"].sum()),
        "tickers_normalised": int((ticker_before != ticker_after).sum()),
        "actions_normalised": int((action_before != action_after).sum()),
        "dates_reparsed": int((date_before != date_after).sum()),
        "numbers_coerced": numbers_coerced,
        "hard_duplicate_rows": int(audit["Hard Duplicate ID"].sum()),
        "accepted_rows": int(accepted_mask.sum()),
        "warning_rows": int(warning_mask.sum()),
        "review_rows": int(review_mask.sum()),
        "rejected_rows": int(reject_mask.sum()),
        "silently_dropped_rows": int(len(audit) - reconciled_count),
        "duplicate_transaction_ids": metadata["duplicate_transaction_ids"],
        "detected_columns": metadata["detected_columns"],
        "audit_table": audit.copy(),
        "accepted_position_rows": audit[audit["Include in Position Analysis"]].copy(),
        "review_rows_table": audit[review_mask].copy(),
        "rejected_rows_table": audit[reject_mask].copy(),
        "non_trading_rows": audit[non_trading_mask].copy(),
        "price_table": price_table.copy(),
    }

    return cleaned_frame, report


#______________________________________________________________________________
# CASH EVENT EXTRACTION
#______________________________________________________________________________

def extract_cash_events(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert explicit non-trade cash events into Date / Type / Amount.

    Conservative rules:
      - DEPOSIT: cash in
      - WITHDRAWAL: cash out
      - DIVIDEND: treated as cash ONLY when an explicit cash Amount/Gross Value
        exists and the row does not contain a usable share quantity. A DIV row
        carrying shares may be a stock dividend/DRIP and is not guessed as cash.
      - Generic TRANSFER is never guessed because direction is ambiguous.
    """
    columns = ["Date", "Type", "Amount"]

    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)

    date_col = _column_or_none(frame, DATE_NAMES)
    type_col = _column_or_none(frame, TYPE_NAMES)
    quantity_col = _column_or_none(frame, QUANTITY_NAMES)
    amount_col = _column_or_none(frame, CASH_AMOUNT_NAMES)

    if amount_col is None:
        amount_col = _column_or_none(frame, GROSS_VALUE_NAMES)

    if date_col is None or type_col is None or amount_col is None:
        return pd.DataFrame(columns=columns)

    event = pd.DataFrame({
        "Date": frame[date_col].map(_smart_clean_date),
        "Type": frame[type_col].map(_clean_action),
        "Amount": frame[amount_col].map(_clean_numeric),
        "Shares": (
            frame[quantity_col].map(_clean_numeric)
            if quantity_col is not None
            else np.nan
        ),
    })

    recognised = event["Type"].isin({"DEPOSIT", "WITHDRAWAL", "DIVIDEND"})
    valid_amount = event["Amount"].notna() & event["Amount"].ne(0)
    valid_date = event["Date"].notna()

    # A dividend with shares is potentially a stock dividend/DRIP. Do not turn
    # its trade price/gross value into cash automatically.
    safe_dividend = ~event["Type"].eq("DIVIDEND") | event["Shares"].isna() | event["Shares"].eq(0)

    event = event[recognised & valid_amount & valid_date & safe_dividend].copy()

    if event.empty:
        return pd.DataFrame(columns=columns)

    event["Amount"] = event["Amount"].abs()

    return (
        event[columns]
        .drop_duplicates()
        .sort_values("Date", kind="stable")
        .reset_index(drop=True)
    )


#______________________________________________________________________________
# COMBINE CASH EVENT SOURCES
#______________________________________________________________________________

def combine_cash_events(
    *frames: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """
    Merge explicit cash-event sources without double counting exact
    Date / Type / Amount duplicates.
    """
    usable = [
        frame[["Date", "Type", "Amount"]].copy()
        for frame in frames
        if frame is not None
        and not frame.empty
        and {"Date", "Type", "Amount"}.issubset(frame.columns)
    ]

    if not usable:
        return None

    combined = pd.concat(usable, ignore_index=True)
    combined["Date"] = combined["Date"].map(_smart_clean_date)
    combined["Type"] = combined["Type"].map(_clean_action)
    combined["Amount"] = combined["Amount"].map(_clean_numeric)

    combined = combined[
        combined["Date"].notna()
        & combined["Type"].isin({"DEPOSIT", "WITHDRAWAL", "DIVIDEND"})
        & combined["Amount"].notna()
        & combined["Amount"].ne(0)
    ].copy()

    if combined.empty:
        return None

    combined["Amount"] = combined["Amount"].abs()

    return (
        combined
        .drop_duplicates(subset=["Date", "Type", "Amount"])
        .sort_values("Date", kind="stable")
        .reset_index(drop=True)
    )
