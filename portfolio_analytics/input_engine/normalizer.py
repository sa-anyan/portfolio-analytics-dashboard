"""Normalise user portfolio data into the v4 canonical input schemas.

The normaliser is intentionally deterministic. It never calculates portfolio risk
or asks the language model to infer missing financial values.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


#______________________________________________________________________________
# CANONICAL COLUMN NAMES
#______________________________________________________________________________

TICKER_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

COLUMN_ALIASES = {
    "ticker": {
        "ticker", "symbol", "security", "security code", "instrument",
        "instrument code", "asset ticker", "stock", "code",
    },
    "asset_name": {
        "asset", "asset name", "security name", "instrument name", "name",
        "description",
    },
    "quantity": {
        "quantity", "qty", "shares", "units", "units held", "holding",
        "position size", "position", "amount held",
    },
    "side": {"side", "position side", "long short", "long/short"},
    "price": {
        "price", "current price", "current price local", "current price (local)", "market price", "last price", "close",
        "close price", "valuation price",
    },
    "entry_price": {
        "entry price", "average entry price", "avg entry price", "average price",
        "cost price", "cost basis price", "purchase price", "avg cost",
        "avg cost local", "avg cost (local)", "average cost", "average cost local", "average cost (local)",
    },
    "date": {
        "date", "trade date", "transaction date", "purchase date", "acquisition date",
        "datetime", "timestamp", "executed at",
    },
    "type": {
        "type", "transaction type", "action", "trade type", "side action",
    },
    "gross_value": {
        "gross value", "gross amount", "trade value", "notional", "value",
        "transaction value",
    },
    "fees": {"fees", "fee", "commission", "commissions", "charges", "costs"},
    "amount": {"amount", "cash amount", "cash value", "flow amount"},
    "transaction_id": {
        "transaction id", "trade id", "order id", "reference", "reference id",
    },
    "asset_class": {
        "asset class", "class", "category", "security type", "instrument type",
    },
    "duration": {
        "duration", "modified duration", "mod duration", "interest rate duration",
    },
    "currency": {"currency", "ccy", "currency code", "denomination", "quote currency"},
    "rate_sensitivity": {
        "rate sensitivity", "interest rate sensitivity", "rate beta",
        "rate shock sensitivity",
    },
}

HOLDINGS_COLUMNS = [
    "Ticker",
    "Asset Name",
    "Quantity",
    "Purchase Date",
    "Average Entry Price",
    "Current Price",
    "Currency",
    "Asset Class",
    "Duration",
    "Rate Sensitivity",
]

LEDGER_COLUMNS = [
    "Date",
    "Type",
    "Ticker",
    "Asset Name",
    "Quantity",
    "Signed Quantity",
    "Price",
    "Gross Value",
    "Fees",
    "Transaction ID",
    "Currency",
    "Asset Class",
    "Duration",
    "Rate Sensitivity",
]

CASHFLOW_COLUMNS = ["Date", "Type", "Amount", "Transaction ID", "Currency"]


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def normalise_label(value: Any) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )


def standardise_ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if ticker in {"", "NAN", "NONE", "<NA>"}:
        return ""
    return TICKER_ALIASES.get(ticker, ticker)



def standardise_currency(value: Any) -> str:
    """Return an ISO-style currency code; missing currency deliberately defaults to USD."""
    text = str(value or "").strip().upper().replace(" ", "")
    if text in {"", "NAN", "NONE", "<NA>", "N/A", "NA"}:
        return "USD"
    aliases = {
        "$": "USD", "US$": "USD", "USDOLLAR": "USD", "USDOLLARS": "USD",
        "£": "GBP", "STERLING": "GBP", "POUND": "GBP", "POUNDS": "GBP",
        "€": "EUR", "EURO": "EUR", "EUROS": "EUR",
        "GBPENCE": "GBX", "PENCE": "GBX", "GBPENCE": "GBX",
    }
    return aliases.get(text, text)

def clean_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None

    if isinstance(value, (int, float, np.integer, np.floating)):
        number = float(value)
        return number if np.isfinite(number) else None

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>", "n/a", "na", "-"}:
        return None

    negative_parentheses = text.startswith("(") and text.endswith(")")
    text = (
        text.replace("$", "")
        .replace("£", "")
        .replace("€", "")
        .replace(",", "")
        .replace("%", "")
        .strip("() ")
    )

    try:
        result = float(text)
    except ValueError:
        return None

    if negative_parentheses:
        result = -abs(result)

    return result if np.isfinite(result) else None


def _lookup_columns(frame: pd.DataFrame) -> dict[str, str]:
    lookup: dict[str, str] = {}
    normalised = {normalise_label(column): str(column) for column in frame.columns}

    for role, aliases in COLUMN_ALIASES.items():
        for label, original in normalised.items():
            if label in aliases:
                lookup[role] = original
                break

    return lookup


def detect_column_map(frame: pd.DataFrame) -> dict[str, str]:
    """Return semantic role -> source column mapping."""
    if frame is None or frame.empty:
        return {}
    return _lookup_columns(frame)


def _series(frame: pd.DataFrame, column_map: dict[str, str], role: str, default: Any = None) -> pd.Series:
    column = column_map.get(role)
    if column and column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


#______________________________________________________________________________
# HOLDINGS NORMALISATION
#______________________________________________________________________________

def normalise_holdings(
    frame: pd.DataFrame,
    *,
    column_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Normalise a holdings snapshot.

    Holdings may be long or short. Positive quantity represents long exposure and
    negative quantity represents short exposure. If a separate Side column is
    supplied, LONG forces a positive sign and SHORT forces a negative sign.
    """
    if frame is None or frame.empty:
        raise ValueError("Holdings data is empty.")

    mapping = column_map or detect_column_map(frame)
    if "ticker" not in mapping or "quantity" not in mapping:
        raise ValueError("Holdings require at least Ticker/Symbol and Quantity/Units.")

    result = pd.DataFrame(index=frame.index)
    result["Ticker"] = _series(frame, mapping, "ticker").map(standardise_ticker)
    result["Asset Name"] = _series(frame, mapping, "asset_name", "").fillna("").astype(str).str.strip()
    result["Quantity"] = _series(frame, mapping, "quantity").map(clean_number)

    side = _series(frame, mapping, "side", "").fillna("").astype(str).str.upper().str.strip()
    short_mask = side.str.contains("SHORT", na=False)
    long_mask = side.str.contains("LONG", na=False)
    result.loc[short_mask & result["Quantity"].notna(), "Quantity"] = -result.loc[
        short_mask & result["Quantity"].notna(), "Quantity"
    ].abs()
    result.loc[long_mask & result["Quantity"].notna(), "Quantity"] = result.loc[
        long_mask & result["Quantity"].notna(), "Quantity"
    ].abs()

    result["Purchase Date"] = pd.to_datetime(_series(frame, mapping, "date"), errors="coerce").dt.normalize()
    result["Average Entry Price"] = _series(frame, mapping, "entry_price").map(clean_number)
    result["Current Price"] = _series(frame, mapping, "price").map(clean_number)
    raw_currency = _series(frame, mapping, "currency", "")
    result["Currency"] = raw_currency.map(standardise_currency)
    result["Asset Class"] = _series(frame, mapping, "asset_class", "").fillna("").astype(str).str.strip()
    result["Duration"] = _series(frame, mapping, "duration").map(clean_number)
    result["Rate Sensitivity"] = _series(frame, mapping, "rate_sensitivity").map(clean_number)

    issues: list[dict[str, Any]] = []
    for idx, row in result.iterrows():
        source_row = int(idx) + 2 if isinstance(idx, (int, np.integer)) else str(idx)
        if not row["Ticker"]:
            issues.append({"row": source_row, "field": "Ticker", "issue": "Missing ticker", "severity": "error"})
        if row["Quantity"] is None or pd.isna(row["Quantity"]) or abs(float(row["Quantity"])) < 1e-15:
            issues.append({"row": source_row, "field": "Quantity", "issue": "Missing or zero quantity", "severity": "error"})
        for price_field in ["Average Entry Price", "Current Price"]:
            value = row[price_field]
            if value is not None and pd.notna(value) and float(value) <= 0:
                issues.append({"row": source_row, "field": price_field, "issue": "Price must be positive", "severity": "error"})

    valid = result[
        result["Ticker"].ne("")
        & result["Quantity"].notna()
        & result["Quantity"].abs().gt(1e-15)
    ].copy()

    # Multiple rows for the same ticker are intentionally retained at this stage.
    # Portfolio State owns aggregation because it also owns cost-basis logic.
    return valid[HOLDINGS_COLUMNS].reset_index(drop=True), issues


#______________________________________________________________________________
# LEDGER NORMALISATION
#______________________________________________________________________________

def normalise_ledger(
    frame: pd.DataFrame,
    *,
    column_map: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Normalise a transaction ledger.

    This route is only for ledgers. Trade direction is encoded deterministically:
    BUY -> positive Signed Quantity, SELL -> negative Signed Quantity.
    Cash-flow rows are separated from security trades.
    """
    if frame is None or frame.empty:
        raise ValueError("Ledger data is empty.")

    mapping = column_map or detect_column_map(frame)
    required = {"date", "type"}
    if not required.issubset(mapping):
        raise ValueError("Ledger data requires Date and Type columns.")

    source = frame.copy()
    event_type = _series(source, mapping, "type").fillna("").astype(str).str.upper().str.strip()
    dates = pd.to_datetime(_series(source, mapping, "date"), errors="coerce")

    trade_mask = event_type.isin({"BUY", "SELL"})
    cash_mask = event_type.isin({"DEPOSIT", "WITHDRAWAL", "DIVIDEND"})

    if trade_mask.any() and ("ticker" not in mapping or "quantity" not in mapping):
        raise ValueError("BUY/SELL ledger rows require Ticker/Symbol and Quantity/Units.")

    trades = pd.DataFrame(index=source.index[trade_mask])
    trades["Date"] = dates.loc[trade_mask]
    trades["Type"] = event_type.loc[trade_mask]
    trades["Ticker"] = _series(source, mapping, "ticker").loc[trade_mask].map(standardise_ticker)
    trades["Asset Name"] = _series(source, mapping, "asset_name", "").loc[trade_mask].fillna("").astype(str).str.strip()
    trades["Quantity"] = _series(source, mapping, "quantity").loc[trade_mask].map(clean_number).abs()
    trades["Signed Quantity"] = np.where(
        trades["Type"].eq("BUY"),
        trades["Quantity"],
        -trades["Quantity"],
    )
    trades["Price"] = _series(source, mapping, "price").loc[trade_mask].map(clean_number)
    trades["Gross Value"] = _series(source, mapping, "gross_value").loc[trade_mask].map(clean_number)
    trades["Fees"] = _series(source, mapping, "fees", 0.0).loc[trade_mask].map(clean_number).fillna(0.0)
    trades["Transaction ID"] = _series(source, mapping, "transaction_id", "").loc[trade_mask].fillna("").astype(str).str.strip()
    trades["Currency"] = _series(source, mapping, "currency", "").loc[trade_mask].map(standardise_currency)
    trades["Asset Class"] = _series(source, mapping, "asset_class", "").loc[trade_mask].fillna("").astype(str).str.strip()
    trades["Duration"] = _series(source, mapping, "duration").loc[trade_mask].map(clean_number)
    trades["Rate Sensitivity"] = _series(source, mapping, "rate_sensitivity").loc[trade_mask].map(clean_number)

    # If price is absent but gross value is present, derive the unit trade price.
    missing_price = trades["Price"].isna() & trades["Gross Value"].notna() & trades["Quantity"].gt(0)
    if missing_price.any():
        trades.loc[missing_price, "Price"] = (
            trades.loc[missing_price, "Gross Value"].abs()
            / trades.loc[missing_price, "Quantity"]
        )

    cashflows = pd.DataFrame(index=source.index[cash_mask])
    cashflows["Date"] = dates.loc[cash_mask]
    cashflows["Type"] = event_type.loc[cash_mask]
    amount_series = _series(source, mapping, "amount")
    gross_series = _series(source, mapping, "gross_value")
    cashflows["Amount"] = amount_series.loc[cash_mask].map(clean_number)
    fallback_amount = gross_series.loc[cash_mask].map(clean_number)
    cashflows["Amount"] = cashflows["Amount"].fillna(fallback_amount).abs()
    cashflows["Transaction ID"] = _series(source, mapping, "transaction_id", "").loc[cash_mask].fillna("").astype(str).str.strip()
    cashflows["Currency"] = _series(source, mapping, "currency", "").loc[cash_mask].map(standardise_currency)

    issues: list[dict[str, Any]] = []
    for idx, row in trades.iterrows():
        source_row = int(idx) + 2 if isinstance(idx, (int, np.integer)) else str(idx)
        if pd.isna(row["Date"]):
            issues.append({"row": source_row, "field": "Date", "issue": "Invalid date", "severity": "error"})
        if not row["Ticker"]:
            issues.append({"row": source_row, "field": "Ticker", "issue": "Missing ticker", "severity": "error"})
        if pd.isna(row["Quantity"]) or float(row["Quantity"]) <= 0:
            issues.append({"row": source_row, "field": "Quantity", "issue": "Quantity must be positive", "severity": "error"})
        if pd.isna(row["Price"]) or float(row["Price"]) <= 0:
            issues.append({"row": source_row, "field": "Price", "issue": "Price or Gross Value is required", "severity": "error"})

    for idx, row in cashflows.iterrows():
        source_row = int(idx) + 2 if isinstance(idx, (int, np.integer)) else str(idx)
        if pd.isna(row["Date"]):
            issues.append({"row": source_row, "field": "Date", "issue": "Invalid date", "severity": "error"})
        if pd.isna(row["Amount"]):
            issues.append({"row": source_row, "field": "Amount", "issue": "Cash-flow amount is required", "severity": "error"})

    trades = trades[
        trades["Date"].notna()
        & trades["Ticker"].ne("")
        & trades["Quantity"].notna()
        & trades["Quantity"].gt(0)
        & trades["Price"].notna()
        & trades["Price"].gt(0)
    ].copy()

    cashflows = cashflows[
        cashflows["Date"].notna()
        & cashflows["Amount"].notna()
    ].copy()

    trades = trades.sort_values("Date", kind="stable").reset_index(drop=True)
    cashflows = cashflows.sort_values("Date", kind="stable").reset_index(drop=True)

    return trades[LEDGER_COLUMNS], cashflows[CASHFLOW_COLUMNS], issues
