
#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

import io
import re
from typing import Any

import numpy as np
import pandas as pd
try:
    import yfinance as yf
except ImportError:  # validation-only environments may not need live Yahoo access
    yf = None

from parsing_engine import (
    build_parsing_report,
    find_best_header_row,
)


#______________________________________________________________________________
# COLUMN NAME OPTIONS
#______________________________________________________________________________




# --------------------------------------------------
# COLUMN NAME OPTIONS
# --------------------------------------------------

# These are keywords/phrases searched INSIDE column names

SECURITY_NAMES = [
    "ticker",
    "symbol",
    "security",
    "instrument",
    "asset",
    "code",
]

QUANTITY_NAMES = [
    "quantity",
    "qty",
    "shares",
    "units",
    "holding",
    "holdings",
    "position",
    "number of shares",
]

TYPE_NAMES = [
    "transaction type",
    "transaction action",
    "trade action",
    "trade side",
    "order side",
    "direction",
    "action",
    "side",
    "type",
]

DATE_NAMES = [
    "transaction date",
    "trade date",
    "date",
]

CURRENT_PRICE_NAMES = [
    "current price",
    "market price",
    "last price",
    "closing price",
    "close",
    "price",
]

ENTRY_PRICE_NAMES = [
    "purchase price",
    "entry price",
    "average entry price",
    "avg price",
    "average price",
    "cost price",
]

ACCOUNTING_PRICE_NAMES = [
    "price",
    "trade price",
    "execution price",
    "purchase price",
    "entry price",
    "average entry price",
    "avg price",
    "average price",
    "cost price",
    # Only fall back to valuation/current prices when no transaction price exists.
    "current price",
    "market price",
    "last price",
    "closing price",
    "close",
]

GROSS_VALUE_NAMES = [
    "gross value",
    "trade value",
    "transaction value",
]

FEE_NAMES = [
    "commission",
    "commissions",
    "fees",
    "fee",
]

CASH_AMOUNT_NAMES = [
    "cash amount",
    "amount",
    "value",
]

PRICE_NAMES = [
    "synthetic price",
    "closing price",
    "current price",
    "market price",
    "last price",
    "close",
    "price",
]

PRICE_SHEET_HINTS = [
    "synthetic price",
    "synthetic_prices",
    "frozen price",
    "frozen_prices",
    "price snapshot",
    "price_snapshot",
    "prices",
]

TICKER_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


# --------------------------------------------------
# COLUMN NAME NORMALISATION
# Makes names such as:
#
# Transaction_Type
# transaction-type
# Transaction.Type
#
# all become:
#
# transaction type
# --------------------------------------------------

def normalise_column_name(column_name):
    column_name = str(column_name).lower().strip()

    column_name = re.sub(
        r"[_\-.]+",
        " ",
        column_name,
    )

    column_name = re.sub(
        r"\s+",
        " ",
        column_name,
    )

    return column_name


# --------------------------------------------------
# COLUMN MATCHING
# --------------------------------------------------

# Checks whether any recognised keyword/phrase exists
# anywhere inside the column name.
def _header_tokens(value: Any) -> list[str]:
    return [token for token in normalise_column_name(value).split() if token]


def _candidate_phrase_in_header(column_name: Any, candidate: Any) -> bool:
    """Return True only when candidate appears on complete token boundaries."""
    header_tokens = _header_tokens(column_name)
    candidate_tokens = _header_tokens(candidate)
    if not candidate_tokens or len(candidate_tokens) > len(header_tokens):
        return False
    width = len(candidate_tokens)
    return any(
        header_tokens[i:i + width] == candidate_tokens
        for i in range(len(header_tokens) - width + 1)
    )


def _fragment_prefix_match(column_name: Any, candidate: Any) -> bool:
    """Controlled support for fragmented headers such as ``tickerest_2``.

    Only a single candidate token may match the *start* of a header token.
    This preserves useful noisy-header recovery without allowing ``action`` to
    match the end of ``transaction``.
    """
    candidate_tokens = _header_tokens(candidate)
    if len(candidate_tokens) != 1:
        return False
    term = candidate_tokens[0]
    if len(term) < 4:
        return False
    return any(token.startswith(term) for token in _header_tokens(column_name))


def _candidate_blocked_by_context(column_name: Any, candidate: Any) -> bool:
    """Block known semantic collisions for generic aliases.

    These are category-level safeguards, not one-off header exceptions.
    """
    words = set(_header_tokens(column_name))
    cand = normalise_column_name(candidate)

    # Generic ``type`` is only meaningful for a trade/action when the header
    # itself carries transaction/trade/order context or is exactly ``type``.
    if cand == "type":
        if words & {"security", "asset", "instrument", "account", "portfolio", "fund"}:
            return True

    # Action/side must never be inferred from identifier/reference metadata.
    if cand in {"action", "side"} and words & {"id", "reference", "identifier"}:
        return True

    # Generic security words must never consume transaction/order identifiers.
    if cand in {"security", "instrument", "asset", "code", "symbol", "ticker"}:
        if words & {"transaction", "txn", "trade", "order", "reference", "identifier", "id"}:
            return True

    return False


def column_matches(column_name, candidate_names):
    """Conservative semantic header matcher.

    Order of evidence:
      1. exact canonical/alias match;
      2. complete token/phrase match;
      3. controlled prefix-fragment match.

    Arbitrary substring matching is intentionally forbidden.
    """
    header = normalise_column_name(column_name)

    for candidate in candidate_names:
        cand = normalise_column_name(candidate)
        if header == cand and not _candidate_blocked_by_context(column_name, candidate):
            return True

    for candidate in candidate_names:
        if _candidate_blocked_by_context(column_name, candidate):
            continue
        if _candidate_phrase_in_header(column_name, candidate):
            return True

    for candidate in candidate_names:
        if _candidate_blocked_by_context(column_name, candidate):
            continue
        if _fragment_prefix_match(column_name, candidate):
            return True

    return False

#______________________________________________________________________________
# STANDARDISE TEXT AND COLUMN NAMES
#______________________________________________________________________________

def normalise(value: Any) -> str:
    return " ".join(
        str(value).strip().lower().replace("_", " ").replace("-", " ").split()
    )


def standardise_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    return TICKER_ALIASES.get(ticker, ticker)


def _column_lookup(frame: pd.DataFrame) -> dict[str, str]:
    return {normalise(column): column for column in frame.columns}


def _find_column(frame: pd.DataFrame, candidates: list[str]):
    """Find the best semantic column using a deterministic evidence hierarchy.

    Exact aliases outrank token phrases, which outrank controlled fragmented
    prefix matches.  Candidate order is used only as a tie-breaker.
    """
    columns = list(frame.columns)
    if not columns:
        return None

    ranked: list[tuple[int, int, int, str]] = []
    for candidate_index, candidate in enumerate(candidates):
        cand_n = normalise_column_name(candidate)
        for column_index, column in enumerate(columns):
            if _candidate_blocked_by_context(column, candidate):
                continue
            header_n = normalise_column_name(column)
            level = 0
            if header_n == cand_n:
                level = 3
            elif _candidate_phrase_in_header(column, candidate):
                level = 2
            elif _fragment_prefix_match(column, candidate):
                level = 1
            if level:
                # Higher evidence first, then earlier alias priority, then source order.
                ranked.append((level, -candidate_index, -column_index, column))

    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][3]


def _find_security_column(frame: pd.DataFrame):
    """Find the investable security column without mistaking IDs/references for tickers.

    Preference is given to explicit ticker/symbol/security/asset headers.  Generic
    ``code`` is accepted only when the header itself is security-like (for example
    ``Security Code``), never for transaction/order/reference IDs.
    """
    if frame is None or frame.empty and len(frame.columns) == 0:
        return None

    columns = list(frame.columns)
    normalised = {column: normalise(column) for column in columns}

    # Strong, unambiguous security headers first.
    strong_terms = [
        "ticker", "ticker symbol", "symbol", "security",
        "instrument", "asset", "stock",
    ]
    blocked_tokens = {"transaction", "txn", "trade", "order", "reference", "id"}

    for term in strong_terms:
        term_n = normalise(term)
        for column in columns:
            header = normalised[column]
            if term_n == header or term_n in header:
                if not (blocked_tokens & set(header.split())):
                    return column

    # ``code`` is intentionally narrow.  This preserves Security Code / Asset Code
    # while excluding Transaction Code, Order Code, Reference Code, etc.
    for column in columns:
        header = normalised[column]
        words = set(header.split())
        if "code" in words and words & {"security", "asset", "instrument", "stock"}:
            if not (words & blocked_tokens):
                return column

    return None


#______________________________________________________________________________
# CLEAN MESSY NUMBERS
#______________________________________________________________________________

def clean_number(value: Any) -> float:
    if pd.isna(value):
        return np.nan

    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)

    text = str(value).strip()

    if text.upper() in {"", "-", "—", "N/A", "NA", "NAN", "NONE"}:
        return np.nan

    negative_parentheses = text.startswith("(") and text.endswith(")")

    text = (
        text.replace(",", "")
        .replace("$", "")
        .replace("£", "")
        .replace("€", "")
    )

    match = re.search(r"[-+]?\d*\.?\d+", text)

    if not match:
        return np.nan

    number = float(match.group())

    if negative_parentheses:
        number = -abs(number)

    return number


#______________________________________________________________________________
# CLEAN MESSY DATES
#______________________________________________________________________________

def clean_date(value: Any):
    if pd.isna(value):
        return pd.NaT

    if isinstance(value, pd.Timestamp):
        return value

    text = str(value).strip()

    if not text:
        return pd.NaT

    parsed = pd.to_datetime(text, errors="coerce")

    if pd.notna(parsed):
        return parsed

    return pd.to_datetime(text, errors="coerce", dayfirst=True)


#______________________________________________________________________________
# FIND THE REAL HEADER ROW
#______________________________________________________________________________

def header_score(values: list[Any]) -> int:
    # Compatibility wrapper. The schema-first parser now performs the scoring.
    from parsing_engine import header_row_score
    return int(round(header_row_score(values)))


def find_header_row(raw_frame: pd.DataFrame, max_rows: int = 20) -> int:
    row, _ = find_best_header_row(raw_frame, max_rows=max_rows)
    return row


#______________________________________________________________________________
# READ A TABLE WITH HEADER DETECTION
#______________________________________________________________________________

def read_csv_smart(file_bytes: bytes) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=object)
    header_row = find_header_row(raw)

    return pd.read_csv(
        io.BytesIO(file_bytes),
        header=header_row,
        dtype=object,
    )


def read_excel_sheet_smart(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    raw = pd.read_excel(
        io.BytesIO(file_bytes),
        sheet_name=sheet_name,
        header=None,
        dtype=object,
    )

    header_row = find_header_row(raw)

    return pd.read_excel(
        io.BytesIO(file_bytes),
        sheet_name=sheet_name,
        header=header_row,
        dtype=object,
    )


#______________________________________________________________________________
# EXTRACT EMBEDDED / FROZEN PRICE SNAPSHOT
#______________________________________________________________________________

def _is_price_sheet_name(sheet_name: str) -> bool:
    key = normalise(sheet_name)

    return any(
        normalise(hint) in key
        for hint in PRICE_SHEET_HINTS
    )


def extract_frozen_prices(
    frame: pd.DataFrame,
    sheet_name: str = "",
) -> pd.DataFrame:
    """
    Convert an embedded price sheet into:
        Ticker | Price | As Of | Source Sheet

    Supports both long format (Ticker, Close, Date) and wide format
    (Date, AAPL, MSFT, BTC, ...). Only positive numeric prices are kept.
    """
    columns = [
        "Ticker",
        "Price",
        "As Of",
        "Source Sheet",
    ]

    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)

    ticker_col = _find_column(
        frame,
        SECURITY_NAMES,
    )
    price_col = _find_column(
        frame,
        PRICE_NAMES,
    )
    date_col = _find_column(
        frame,
        DATE_NAMES + [
            "as of",
            "as of date",
            "snapshot date",
            "valuation date",
        ],
    )

    # Long format: one row per ticker/date observation.
    if ticker_col is not None and price_col is not None:
        result = pd.DataFrame({
            "Ticker": frame[ticker_col].fillna("").map(
                standardise_ticker
            ),
            "Price": frame[price_col].map(
                clean_number
            ),
            "As Of": (
                frame[date_col].map(clean_date)
                if date_col is not None
                else pd.NaT
            ),
            "Source Sheet": sheet_name,
        })

        result = result[
            result["Ticker"].ne("")
            & result["Price"].notna()
            & result["Price"].gt(0)
        ].copy()

        if result.empty:
            return pd.DataFrame(columns=columns)

        result = result.sort_values(
            ["Ticker", "As Of"],
            kind="stable",
            na_position="first",
        )

        return (
            result.groupby(
                "Ticker",
                as_index=False,
                sort=False,
            )
            .tail(1)
            .reset_index(drop=True)
        )

    # Wide format: Date + one column per ticker. We only treat a generic table
    # as prices when the sheet name strongly indicates that it is a price sheet.
    if not _is_price_sheet_name(sheet_name):
        return pd.DataFrame(columns=columns)

    date_series = None

    if date_col is not None:
        date_series = frame[date_col].map(
            clean_date
        )

    rows = []

    for column in frame.columns:
        if date_col is not None and column == date_col:
            continue

        ticker = standardise_ticker(column)
        numeric = frame[column].map(
            clean_number
        )

        usable = numeric[
            numeric.notna()
            & numeric.gt(0)
        ]

        if usable.empty:
            continue

        last_index = usable.index[-1]

        as_of = (
            date_series.loc[last_index]
            if date_series is not None
            and last_index in date_series.index
            else pd.NaT
        )

        rows.append({
            "Ticker": ticker,
            "Price": float(usable.loc[last_index]),
            "As Of": as_of,
            "Source Sheet": sheet_name,
        })

    return pd.DataFrame(
        rows,
        columns=columns,
    )




def infer_ticker_from_source_name(source_name: str | None) -> str | None:
    """Infer a single-security symbol from a file or sheet name.

    This is intentionally conservative: only simple symbol-like stems are
    accepted (letters/numbers plus . - _), and generic names are rejected.
    """
    if not source_name:
        return None
    name = str(source_name).strip()
    # Remove path and common spreadsheet extensions without importing pathlib.
    name = re.split(r"[/\\]", name)[-1]
    stem = re.sub(r"\.(csv|xlsx|xls)$", "", name, flags=re.IGNORECASE).strip()
    generic = {
        "data", "dataset", "prices", "price", "market", "market_data",
        "historical", "history", "sheet1", "transactions", "portfolio",
    }
    if not stem or stem.lower() in generic:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", stem):
        return None
    return stem.upper()


#______________________________________________________________________________
# DETECT FILE FORMAT
#______________________________________________________________________________

def detect_frame_type(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty:
        return None

    report = build_parsing_report(frame)
    return report["classification"]["mode"]


#______________________________________________________________________________
# READ CSV OR EXCEL FILE
#______________________________________________________________________________

def read_upload(file_bytes: bytes, file_name: str):
    name = file_name.lower()

    if name.endswith(".csv"):
        frame = read_csv_smart(file_bytes)
        kind = detect_frame_type(frame)

        parsing_report = build_parsing_report(frame)

        if kind in {"ledger", "holdings"} and not parsing_report["required_variables_valid"]:
            raise ValueError(
                "Could not confidently identify both Ticker and Quantity columns. "
                "Review the file headers before continuing."
            )

        if kind == "market_data":
            if not parsing_report["required_variables_valid"]:
                raise ValueError(
                    "Historical market data was detected, but its date/price fields "
                    "were not identified confidently."
                )
            inferred_ticker = None
            if "ticker" not in parsing_report.get("schema", {}):
                inferred_ticker = infer_ticker_from_source_name(file_name)
                if not inferred_ticker:
                    raise ValueError(
                        "Historical market data was detected without a Ticker column. "
                        "Rename the file to the security symbol (for example A2M.csv) "
                        "or include a Ticker column."
                    )
            return {
                "mode": "market_data",
                "market_data": frame,
                "transactions": None,
                "cashflows": None,
                "holdings": None,
                "frozen_prices": None,
                "parsing_report": parsing_report,
                "market_data_start": None,
                "inferred_ticker": inferred_ticker,
                "ticker_source": "filename" if inferred_ticker else "column",
            }

        if kind == "ledger":
            return {
                "mode": "ledger",
                "transactions": frame,
                "cashflows": None,
                "holdings": None,
                "frozen_prices": None,
                "parsing_report": parsing_report,
                "market_data_start": None,
            }

        if kind == "holdings":
            return {
                "mode": "holdings",
                "transactions": None,
                "cashflows": None,
                "holdings": frame,
                "frozen_prices": None,
                "parsing_report": parsing_report,
                "market_data_start": (pd.Timestamp.today().normalize() - pd.DateOffset(years=5)),
            }

        raise ValueError(
            "Could not confidently classify this CSV as holdings, a transaction "
            "ledger, cashflows, or historical market data."
        )

    if name.endswith(".xlsx"):
        excel = pd.ExcelFile(io.BytesIO(file_bytes))
        ledgers = []
        holdings = []
        cashflows = []
        market_data_tables = []
        frozen_price_frames = []

        for sheet_name in excel.sheet_names:
            # Price snapshots are read with their ordinary first-row header so
            # wide time-series sheets such as Synthetic_Prices are preserved.
            if _is_price_sheet_name(sheet_name):
                price_frame = pd.read_excel(
                    io.BytesIO(file_bytes),
                    sheet_name=sheet_name,
                    header=0,
                    dtype=object,
                )

                frozen = extract_frozen_prices(
                    price_frame,
                    sheet_name,
                )

                if not frozen.empty:
                    frozen_price_frames.append(
                        frozen
                    )
                    continue

            frame = read_excel_sheet_smart(
                file_bytes,
                sheet_name,
            )
            kind = detect_frame_type(frame)

            if kind == "ledger":
                ledgers.append((sheet_name, frame))
            elif kind == "holdings":
                holdings.append((sheet_name, frame))
            elif kind == "cashflows":
                cashflows.append((sheet_name, frame))
            elif kind == "market_data":
                market_data_tables.append((sheet_name, frame))

            # Also support long-format frozen-price tables whose sheet name is
            # not conventional, as long as they clearly contain ticker+price.
            frozen = extract_frozen_prices(
                frame,
                sheet_name,
            )

            if not frozen.empty and kind is None:
                frozen_price_frames.append(
                    frozen
                )

        frozen_prices = None

        if frozen_price_frames:
            frozen_prices = pd.concat(
                frozen_price_frames,
                ignore_index=True,
            )

        if ledgers:
            ledger_frame = ledgers[0][1]
            parsing_report = build_parsing_report(ledger_frame)
            if not parsing_report["required_variables_valid"]:
                raise ValueError(
                    "Could not confidently identify both Ticker and Quantity columns "
                    "in the transaction ledger."
                )
            return {
                "mode": "ledger",
                "transactions": ledger_frame,
                "cashflows": cashflows[0][1] if cashflows else None,
                "holdings": None,
                "frozen_prices": frozen_prices,
                "parsing_report": parsing_report,
                "market_data_start": None,
                "source_sheet": ledgers[0][0],
            }

        if market_data_tables:
            market_sheet = market_data_tables[0][0]
            market_frame = market_data_tables[0][1]
            parsing_report = build_parsing_report(market_frame)
            if not parsing_report["required_variables_valid"]:
                raise ValueError(
                    "Historical market data was detected, but its required fields "
                    "were not identified confidently."
                )
            inferred_ticker = None
            if "ticker" not in parsing_report.get("schema", {}):
                inferred_ticker = infer_ticker_from_source_name(market_sheet)
                if not inferred_ticker:
                    inferred_ticker = infer_ticker_from_source_name(file_name)
                if not inferred_ticker:
                    raise ValueError(
                        "Historical market data was detected without a Ticker column. "
                        "Use a symbol-like sheet/file name or include a Ticker column."
                    )
            return {
                "mode": "market_data",
                "market_data": market_frame,
                "transactions": None,
                "cashflows": None,
                "holdings": None,
                "frozen_prices": frozen_prices,
                "parsing_report": parsing_report,
                "market_data_start": None,
                "source_sheet": market_sheet,
                "inferred_ticker": inferred_ticker,
                "ticker_source": "sheet/file name" if inferred_ticker else "column",
            }

        if holdings:
            holdings_frame = holdings[0][1]
            parsing_report = build_parsing_report(holdings_frame)
            if not parsing_report["required_variables_valid"]:
                raise ValueError(
                    "Could not confidently identify both Ticker and Quantity columns "
                    "in the holdings sheet."
                )
            return {
                "mode": "holdings",
                "transactions": None,
                "cashflows": None,
                "holdings": holdings_frame,
                "frozen_prices": frozen_prices,
                "parsing_report": parsing_report,
                "market_data_start": (pd.Timestamp.today().normalize() - pd.DateOffset(years=5)),
                "source_sheet": holdings[0][0],
            }

        raise ValueError(
            "Could not find a holdings or transaction table in this workbook."
        )

    raise ValueError("Upload a CSV or XLSX file.")


#______________________________________________________________________________
# PREPARE HOLDINGS FOR REVIEW
#______________________________________________________________________________

def prepare_holdings_for_review(frame: pd.DataFrame) -> pd.DataFrame:
    security_col = _find_security_column(frame)
    quantity_col = _find_column(frame, QUANTITY_NAMES)
    current_price_col = _find_column(frame, CURRENT_PRICE_NAMES)
    entry_price_col = _find_column(frame, ENTRY_PRICE_NAMES)

    if security_col is None or quantity_col is None:
        raise ValueError("Holdings need a ticker/security column and a quantity/shares column.")

    # Preserve the raw security and quantity until validation.  Invalid values
    # (for example ``samuel`` / ``notyet``) must reach the human review layer
    # rather than being silently removed during preparation.
    result = pd.DataFrame({
        "Security": frame[security_col],
        "Quantity": frame[quantity_col],
        "Source Row": frame.index,
        "Source Record": frame.apply(lambda row: row.to_dict(), axis=1),
    })

    if current_price_col is not None:
        result["Provided Price"] = frame[current_price_col].map(clean_number)

    if entry_price_col is not None:
        result["Provided Entry Price"] = frame[entry_price_col].map(clean_number)

    result["Security"] = result["Security"].fillna("").astype(str).str.strip()

    # Ignore only genuinely blank editor rows.  Everything else is validated
    # and, when necessary, extracted into Security & Input Review.
    quantity_blank = (
        result["Quantity"].isna()
        | result["Quantity"].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])
    )
    result = result[~(result["Security"].eq("") & quantity_blank)].reset_index(drop=True)

    return result


#______________________________________________________________________________
# VALIDATE QUANTITY
#______________________________________________________________________________

def convert_quantity(value: Any):
    quantity = clean_number(value)

    if pd.isna(quantity):
        return None, f"Quantity '{value}' is not a valid number."

    if quantity == 0:
        return None, "Quantity cannot be zero."

    return float(quantity), None


#______________________________________________________________________________
# RESOLVE SECURITY
#______________________________________________________________________________

def _has_complete_company_name_term(query: str, asset_name: Any) -> bool:
    """Require a meaningful COMPLETE query word to appear in the Yahoo name.

    This deliberately prevents very short prefix searches such as ``van`` from
    surfacing ``Vanguard ...``. Exact ticker matches are handled separately.
    """
    query_tokens = re.findall(r"[A-Za-z0-9]+", str(query).casefold())
    name_tokens = set(re.findall(r"[A-Za-z0-9]+", str(asset_name).casefold()))

    meaningful_query_tokens = [token for token in query_tokens if len(token) >= 4]
    return any(token in name_tokens for token in meaningful_query_tokens)


def resolve_security_candidates(security_input: Any, max_results: int = 8) -> list[dict[str, Any]]:
    """
    Search Yahoo Finance using either an exact ticker or meaningful company/security
    name words. Non-ticker results must contain at least one complete query word in
    the returned security name, so short fragments such as ``van`` do not surface
    ``Vanguard``. Candidates are still returned for explicit human confirmation.
    """
    security_input = str(security_input).strip()

    if not security_input:
        return []

    security_input = TICKER_ALIASES.get(
        security_input.upper(),
        security_input,
    )

    cleaned_security = re.sub(
        r"(?i)\.US$",
        "",
        str(security_input).strip(),
    )
    cleaned_security = re.sub(
        r"(?i)-US$",
        "",
        cleaned_security,
    )
    security_input = cleaned_security

    try:
        search = yf.Search(
            security_input,
            max_results=max_results,
            news_count=0,
            enable_fuzzy_query=True,
            raise_errors=False,
        )
        quotes = search.quotes or []
    except Exception:
        quotes = []

    if not quotes:
        return []

    security_upper = security_input.upper()
    candidates = []
    seen = set()

    for quote in quotes:
        ticker = quote.get("symbol")
        if not ticker:
            continue

        ticker = str(ticker)
        ticker_key = ticker.upper()
        if ticker_key in seen:
            continue
        seen.add(ticker_key)

        asset_name = (
            quote.get("longname")
            or quote.get("shortname")
            or quote.get("name")
            or ticker
        )
        asset_type = quote.get("quoteType") or quote.get("typeDisp")
        exchange = quote.get("exchange") or quote.get("exchDisp")
        exact = ticker_key == security_upper

        # Exact ticker entry remains valid. For company/security-name searches,
        # require at least one complete meaningful word from the user's query to
        # appear in Yahoo's returned name. This blocks prefix-only behaviour such
        # as "van" -> "Vanguard ..." while allowing "Vanguard".
        if not exact and not _has_complete_company_name_term(security_input, asset_name):
            continue

        candidates.append({
            "Ticker": ticker,
            "Asset Name": asset_name,
            "Asset Type": asset_type,
            "Exchange": exchange,
            "Match Status": "Valid ticker" if exact else "Review match",
            "Exact Ticker Match": exact,
        })

    # Exact ticker must always be first. Yahoo's remaining order is retained because
    # it already reflects search relevance for names / descriptive phrases.
    candidates.sort(key=lambda item: not item["Exact Ticker Match"])
    return candidates


def resolve_security(security_input: Any) -> dict[str, Any]:
    """Backward-compatible single-result resolver used by existing code paths."""
    candidates = resolve_security_candidates(security_input)

    if not candidates:
        return {
            "Ticker": None,
            "Asset Name": None,
            "Asset Type": None,
            "Exchange": None,
            "Match Status": "Not found",
            "Candidates": [],
        }

    best = candidates[0].copy()
    best["Candidates"] = candidates
    return best


#______________________________________________________________________________
# VALIDATE HOLDINGS
#______________________________________________________________________________

def validate_holdings(working_holdings: pd.DataFrame):
    valid_rows = []
    review_rows = []

    for row_number, row in working_holdings.iterrows():
        security = str(row.get("Security", row.get("Ticker", ""))).strip()
        quantity, quantity_error = convert_quantity(row.get("Quantity", ""))
        match = resolve_security(security)

        ticker = match.get("Ticker")
        match_status = match.get("Match Status")
        provided_price = row.get("Provided Price", np.nan)
        entry_price = row.get("Provided Entry Price", np.nan)

        if ticker and quantity is not None and match_status == "Valid ticker":
            valid_rows.append({
                "Ticker": str(ticker),
                "Asset Name": match.get("Asset Name") or str(ticker),
                "Quantity": quantity,
                "Provided Price": provided_price,
                "Provided Entry Price": entry_price,
            })
            continue

        problems = []

        if not ticker:
            problems.append("Security not found")
        elif match_status == "Review match":
            problems.append("Review match")

        if quantity_error:
            problems.append(quantity_error)

        review_rows.append({
            "Source Row": row.get("Source Row", row_number),
            "Source Record": row.get("Source Record"),
            "Input": security,
            "Ticker": ticker,
            "Asset Name": match.get("Asset Name"),
            "Asset Type": match.get("Asset Type"),
            "Exchange": match.get("Exchange"),
            "Original Quantity": row.get("Quantity"),
            "Provided Price": provided_price,
            "Provided Entry Price": entry_price,
            "Match Status": match_status,
            "Status": " | ".join(problems),
        })

    valid = pd.DataFrame(valid_rows)
    review = pd.DataFrame(review_rows)

    if not valid.empty:
        valid = (
            valid.groupby("Ticker", as_index=False)
            .agg({
                "Asset Name": "last",
                "Quantity": "sum",
                "Provided Price": "last",
                "Provided Entry Price": "last",
            })
        )
        valid = valid[valid["Quantity"].ne(0)].copy()

    return valid, review


#______________________________________________________________________________
# FINALISE CURRENT HOLDINGS
#______________________________________________________________________________

def finalise_holdings(valid_holdings: pd.DataFrame) -> pd.DataFrame:
    result = valid_holdings.copy()

    if result.empty:
        return result

    result["Position"] = np.where(result["Quantity"] > 0, "LONG", "SHORT")
    result["Average Entry Price"] = result.get("Provided Entry Price", np.nan)
    result["Realised P&L"] = np.nan

    return result


#______________________________________________________________________________
# CLEAN TRANSACTION LEDGER
#______________________________________________________________________________

def clean_transaction_ledger(frame: pd.DataFrame):
    result = frame.copy()

    security_col = _find_column(result, SECURITY_NAMES)
    quantity_col = _find_column(result, QUANTITY_NAMES)
    type_col = _find_column(result, TYPE_NAMES)
    date_col = _find_column(result, DATE_NAMES)
    price_col = _find_column(result, CURRENT_PRICE_NAMES)
    gross_col = _find_column(result, GROSS_VALUE_NAMES)
    fee_col = _find_column(result, FEE_NAMES)

    if not all([security_col, quantity_col, type_col, date_col]):
        raise ValueError("Transaction ledger needs Date, Type/Action, Ticker, and Quantity/Shares.")

    result[type_col] = result[type_col].astype(str).str.upper().str.strip()
    result[date_col] = result[date_col].map(clean_date)
    result[quantity_col] = result[quantity_col].map(clean_number)

    if price_col is not None:
        result[price_col] = result[price_col].map(clean_number)

    if gross_col is not None:
        result[gross_col] = result[gross_col].map(clean_number)

    if fee_col is not None:
        result[fee_col] = result[fee_col].map(clean_number).fillna(0.0)

    buy_sell = result[type_col].isin(["BUY", "SELL"])
    valid_price = pd.Series(False, index=result.index)

    if price_col is not None:
        valid_price = valid_price | result[price_col].notna()

    if gross_col is not None:
        valid_price = valid_price | result[gross_col].notna()

    valid_trade = (
        buy_sell
        & result[security_col].notna()
        & result[security_col].astype(str).str.strip().ne("")
        & result[quantity_col].notna()
        & result[quantity_col].ne(0)
        & result[date_col].notna()
        & valid_price
    )

    # BUY/SELL is the single source of truth for trade direction.
    # Broker exports may also sign Shares/Quantity. We therefore convert
    # accepted trade quantity to an absolute trade size before accounting.
    result.loc[
        buy_sell & result[quantity_col].notna(),
        quantity_col,
    ] = (
        result.loc[
            buy_sell & result[quantity_col].notna(),
            quantity_col,
        ].abs()
    )

    # Duplicate transaction IDs are not silently included in accounting.
    # They remain visible in the review table and can be corrected/accepted.
    transaction_id_col = _find_column(
        result,
        [
            "txnid",
            "transaction id",
            "transaction_id",
            "trade id",
            "trade_id",
        ],
    )

    duplicate_trade = pd.Series(False, index=result.index)

    if transaction_id_col is not None:
        ids = (
            result[transaction_id_col]
            .astype(str)
            .str.strip()
        )

        usable_ids = (
            result[transaction_id_col].notna()
            & ids.ne("")
            & ids.str.lower().ne("nan")
        )

        duplicate_trade = (
            usable_ids
            & ids.duplicated(keep=False)
            & buy_sell
        )

    if "Potential Duplicate" in result.columns:
        duplicate_trade = (
            duplicate_trade
            | (
                result["Potential Duplicate"]
                .fillna(False)
                .astype(bool)
                & buy_sell
            )
        )

    accepted_trade = valid_trade & ~duplicate_trade

    issues = result[
        buy_sell & ~accepted_trade
    ].copy()

    if not issues.empty:
        issues["Accounting Review Reason"] = np.where(
            duplicate_trade.loc[issues.index],
            "Potential duplicate transaction",
            "Incomplete or invalid BUY/SELL transaction",
        )

    clean = result[accepted_trade].copy()

    return clean, issues



#______________________________________________________________________________
# BUILD DATA QUALITY REPORT
#______________________________________________________________________________

def build_data_quality_report(
    original_frame: pd.DataFrame,
    clean_transactions: pd.DataFrame,
    issue_rows: pd.DataFrame,
) -> dict[str, Any]:
    """
    Summarise the quality of an uploaded transaction ledger.

    This report does not guess missing values. It only counts and
    classifies what the parser can confidently observe.
    """

    report = {
        "Rows Read": int(len(original_frame)),
        "Valid Transactions": int(len(clean_transactions)),
        "Incomplete Transactions": int(len(issue_rows)),
        "Potential Duplicate Rows": 0,
        "Invalid Dates": 0,
        "Invalid Quantities": 0,
        "Invalid Prices": 0,
        "Missing Tickers": 0,
        "Unrecognised Actions": 0,
    }

    frame = original_frame.copy()

    security_col = _find_column(
        frame,
        SECURITY_NAMES,
    )

    quantity_col = _find_column(
        frame,
        QUANTITY_NAMES,
    )

    type_col = _find_column(
        frame,
        TYPE_NAMES,
    )

    date_col = _find_column(
        frame,
        DATE_NAMES,
    )

    price_col = _find_column(
        frame,
        CURRENT_PRICE_NAMES,
    )

    gross_col = _find_column(
        frame,
        GROSS_VALUE_NAMES,
    )

    transaction_id_col = _find_column(
        frame,
        [
            "txnid",
            "transaction id",
            "transaction_id",
            "trade id",
            "trade_id",
        ],
    )

    # Missing ticker/security.
    if security_col is not None:
        missing_security = (
            frame[security_col]
            .isna()
            | frame[security_col]
            .astype(str)
            .str.strip()
            .eq("")
        )

        report["Missing Tickers"] = int(
            missing_security.sum()
        )

    # Invalid/unparseable dates.
    if date_col is not None:
        parsed_dates = frame[
            date_col
        ].map(
            clean_date
        )

        raw_present = (
            frame[date_col]
            .notna()
            & frame[date_col]
            .astype(str)
            .str.strip()
            .ne("")
        )

        report["Invalid Dates"] = int(
            (
                raw_present
                & parsed_dates.isna()
            ).sum()
        )

    # Invalid quantities.
    if quantity_col is not None:
        parsed_quantity = frame[
            quantity_col
        ].map(
            clean_number
        )

        raw_present = (
            frame[quantity_col]
            .notna()
            & frame[quantity_col]
            .astype(str)
            .str.strip()
            .ne("")
        )

        report["Invalid Quantities"] = int(
            (
                raw_present
                & parsed_quantity.isna()
            ).sum()
        )

    # Invalid prices. If Gross Value is usable, do not count the row
    # as invalid-price because the ledger can still be processed.
    if price_col is not None:
        parsed_price = frame[
            price_col
        ].map(
            clean_number
        )

        price_present = (
            frame[price_col]
            .notna()
            & frame[price_col]
            .astype(str)
            .str.strip()
            .ne("")
        )

        if gross_col is not None:
            parsed_gross = frame[
                gross_col
            ].map(
                clean_number
            )

            report["Invalid Prices"] = int(
                (
                    price_present
                    & parsed_price.isna()
                    & parsed_gross.isna()
                ).sum()
            )

        else:
            report["Invalid Prices"] = int(
                (
                    price_present
                    & parsed_price.isna()
                ).sum()
            )

    # Unrecognised actions.
    if type_col is not None:
        actions = (
            frame[type_col]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.strip()
        )

        recognised_actions = {
            "BUY",
            "SELL",
            "DEPOSIT",
            "WITHDRAWAL",
            "DIVIDEND",
            "TRANSFER",
            "",
        }

        report["Unrecognised Actions"] = int(
            (~actions.isin(
                recognised_actions
            )).sum()
        )

    # Duplicate detection. Prefer transaction ID if supplied.
    if transaction_id_col is not None:
        ids = frame[
            transaction_id_col
        ]

        duplicate_mask = (
            ids.notna()
            & ids.astype(str)
            .str.strip()
            .ne("")
            & ids.duplicated(
                keep=False
            )
        )

        report[
            "Potential Duplicate Rows"
        ] = int(
            duplicate_mask.sum()
        )

    else:
        duplicate_subset = [
            column
            for column in [
                date_col,
                type_col,
                security_col,
                quantity_col,
                price_col,
                gross_col,
            ]
            if column is not None
        ]

        if duplicate_subset:
            duplicate_mask = (
                frame.duplicated(
                    subset=duplicate_subset,
                    keep=False,
                )
            )

            report[
                "Potential Duplicate Rows"
            ] = int(
                duplicate_mask.sum()
            )

    return report


#______________________________________________________________________________
# FLAG POTENTIAL DUPLICATES
#______________________________________________________________________________

def flag_potential_duplicates(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """
    Add a Potential Duplicate column without dropping any rows.
    """

    result = frame.copy()

    transaction_id_col = _find_column(
        result,
        [
            "txnid",
            "transaction id",
            "transaction_id",
            "trade id",
            "trade_id",
        ],
    )

    if transaction_id_col is not None:
        ids = result[
            transaction_id_col
        ]

        result[
            "Potential Duplicate"
        ] = (
            ids.notna()
            & ids.astype(str)
            .str.strip()
            .ne("")
            & ids.duplicated(
                keep=False
            )
        )

        return result

    subset = []

    for candidates in [
        DATE_NAMES,
        TYPE_NAMES,
        SECURITY_NAMES,
        QUANTITY_NAMES,
        CURRENT_PRICE_NAMES,
        GROSS_VALUE_NAMES,
    ]:
        column = _find_column(
            result,
            candidates,
        )

        if (
            column is not None
            and column not in subset
        ):
            subset.append(
                column
            )

    if subset:
        result[
            "Potential Duplicate"
        ] = result.duplicated(
            subset=subset,
            keep=False,
        )

    else:
        result[
            "Potential Duplicate"
        ] = False

    return result




#______________________________________________________________________________
# STANDARDISE LEDGER FOR ACCOUNTING
#______________________________________________________________________________

def standardise_ledger_for_accounting(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert flexible ledger headers into the canonical accounting schema.

    Alias collisions are coalesced instead of renamed into duplicate labels.
    Existing canonical values win. Alias columns only fill missing/blank cells.
    """
    result = frame.copy()

    def _blank_mask(series: pd.Series) -> pd.Series:
        return series.isna() | series.astype(str).str.strip().str.lower().isin(["", "nan", "none", "nat"])

    def _matching_columns(
        candidates: list[str],
        security: bool = False,
        merge_all_exact: bool = True,
    ) -> list:
        """Return only columns safe to merge into one accounting field.

        Coalescing is intentionally stricter than discovery.  Exact canonical/
        alias headers may be merged together.  When no exact alias exists we
        allow only the single best semantic fallback.  This prevents metadata
        such as ``Derived Price Method`` or ``Price Change %`` from being merged
        into the numeric ``Price`` field merely because they contain the token
        ``price``.
        """
        # Exact aliases are returned in candidate-priority order, not source
        # column order. This matters for hybrid exports where ``Trade Date``
        # must beat generic market ``Date`` and ``Purchase Price`` must beat
        # ``Current Price`` for accounting.
        exact = []
        normalised_columns = {}
        for col in result.columns:
            normalised_columns.setdefault(normalise_column_name(col), []).append(col)
        for candidate in candidates:
            for col in normalised_columns.get(normalise_column_name(candidate), []):
                if col not in exact:
                    exact.append(col)

        if exact:
            return exact if merge_all_exact else exact[:1]

        if security:
            primary = _find_security_column(result)
        else:
            primary = _find_column(result, candidates)
        return [primary] if primary is not None else []

    def _coalesce_group(
        candidates: list[str],
        target: str,
        security: bool = False,
        target_first: bool = True,
        merge_all_exact: bool = True,
    ):
        nonlocal result
        matches = _matching_columns(
            candidates,
            security=security,
            merge_all_exact=merge_all_exact,
        )
        if target in result.columns and target_first:
            ordered = [target] + [c for c in matches if c != target]
        else:
            ordered = list(matches)

        if not ordered:
            return

        # In hybrid exports a generic market ``Date`` can coexist with a
        # transaction ``Trade Date``. Preserve the former as reference metadata
        # rather than merging it into the accounting date.
        if target == "Date" and target in result.columns and target not in ordered:
            reference_name = "Reference Date"
            suffix = 2
            while reference_name in result.columns:
                reference_name = f"Reference Date {suffix}"
                suffix += 1
            result = result.rename(columns={target: reference_name})

        # Use object during coalescing so Pandas cannot fail when a legitimate
        # alias arrives with a different dtype (for example numeric strings).
        # ``infer_objects`` restores a natural dtype afterwards where possible.
        base = result[ordered[0]].copy().astype("object")
        for col in ordered[1:]:
            source = result[col]
            mask = _blank_mask(base) & ~_blank_mask(source)
            base.loc[mask] = source.loc[mask].astype("object")
        base = base.infer_objects(copy=False)

        drop_cols = [c for c in ordered if c in result.columns]
        result = result.drop(columns=drop_cols)
        result[target] = base

    _coalesce_group(DATE_NAMES, "Date", target_first=False, merge_all_exact=False)
    _coalesce_group(TYPE_NAMES, "Type")
    _coalesce_group(SECURITY_NAMES, "Ticker", security=True)
    _coalesce_group(QUANTITY_NAMES, "Quantity")
    _coalesce_group(ACCOUNTING_PRICE_NAMES, "Price", merge_all_exact=("Price" in result.columns))
    _coalesce_group(GROSS_VALUE_NAMES, "Gross Value")
    _coalesce_group(FEE_NAMES, "Fees")

    # Defensive final guard if the incoming frame itself already had duplicate labels.
    if result.columns.duplicated().any():
        rebuilt = pd.DataFrame(index=result.index)
        for name in dict.fromkeys(result.columns):
            block = result.loc[:, result.columns == name]
            combined = block.iloc[:, 0].copy()
            for i in range(1, block.shape[1]):
                source = block.iloc[:, i]
                mask = _blank_mask(combined) & ~_blank_mask(source)
                combined.loc[mask] = source.loc[mask]
            rebuilt[name] = combined
        result = rebuilt

    required = ["Date", "Type", "Ticker", "Quantity"]
    missing = [column for column in required if column not in result.columns]
    if missing:
        raise ValueError(
            "Transaction data is missing required accounting fields: "
            + ", ".join(missing)
        )

    return result


#______________________________________________________________________________
# EXCLUDE UNRESOLVED SECURITIES FROM CURRENT ANALYSIS
#______________________________________________________________________________

def exclude_unresolved_securities(
    transactions: pd.DataFrame,
    excluded_securities: list[str],
) -> pd.DataFrame:
    """
    Temporarily exclude transaction rows whose security/ticker still needs
    user review. The original rows remain available in the editable draft.
    """

    if not excluded_securities:
        return transactions.copy()

    result = transactions.copy()

    security_col = _find_security_column(result)

    if security_col is None:
        return result

    excluded = {
        str(value).strip()
        for value in excluded_securities
    }

    return result[
        ~result[
            security_col
        ]
        .astype(str)
        .str.strip()
        .isin(excluded)
    ].copy()


#______________________________________________________________________________
# VALIDATE LEDGER TICKERS
#______________________________________________________________________________

def ledger_security_inputs(transactions: pd.DataFrame) -> list[str]:
    security_col = _find_security_column(transactions)

    if security_col is None:
        return []

    values = transactions[security_col].dropna().astype(str).str.strip()
    return list(dict.fromkeys(value for value in values if value))


def validate_ledger_tickers(transactions: pd.DataFrame):
    accepted = {}
    review = []

    for security in ledger_security_inputs(transactions):
        match = resolve_security(security)

        if match.get("Ticker") and match.get("Match Status") == "Valid ticker":
            accepted[security] = str(match["Ticker"])
        else:
            review.append({"Input": security, **match})

    return accepted, review


def apply_ledger_ticker_map(transactions: pd.DataFrame, ticker_map: dict[str, str]) -> pd.DataFrame:
    result = transactions.copy()
    security_col = _find_column(result, SECURITY_NAMES)

    if security_col is None:
        return result

    result[security_col] = (
        result[security_col]
        .astype(str)
        .str.strip()
        .map(lambda x: ticker_map.get(x, x))
    )

    return result
