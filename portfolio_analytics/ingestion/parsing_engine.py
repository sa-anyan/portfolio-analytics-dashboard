from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import pandas as pd


# -----------------------------------------------------------------------------
# FINANCIAL SCHEMA VOCABULARY
# -----------------------------------------------------------------------------
# Each semantic role has both full terms and common abbreviations.  These are
# used only to identify columns; they do not alter any row values.

COLUMN_FAMILIES: dict[str, list[str]] = {
    "ticker": [
        "ticker", "symbol", "security", "instrument", "asset", "stock",
        "security code", "asset code", "security symbol", "ticker symbol",
    ],
    "quantity": [
        "quantity", "qty", "shares", "units", "holding", "holdings",
        "position", "size", "number of shares", "share quantity",
    ],
    "date": [
        "date", "trade date", "transaction date", "execution date",
        "settlement date", "as of date", "valuation date",
    ],
    "time": [
        "time", "trade time", "transaction time", "execution time",
    ],
    "datetime": [
        "datetime", "date time", "timestamp", "trade timestamp",
        "transaction timestamp", "execution timestamp",
    ],
    "action": [
        "action", "type", "transaction type", "direction", "side",
        "trade side", "order side",
    ],
    "price": [
        "price", "trade price", "execution price", "purchase price",
        "market price", "current price", "last price", "closing price",
        "entry price", "average price", "cost price",
    ],
    "open_price": ["open", "open price", "opening price"],
    "high_price": ["high", "high price", "session high"],
    "low_price": ["low", "low price", "session low"],
    "close_price": ["close", "close price", "closing price"],
    "adjusted_close": [
        "adjusted", "adjusted close", "adj close", "adjclose",
        "adjusted closing price",
    ],
    "returns": ["returns", "return", "daily return", "pct return", "percent return"],
    "volume": ["volume", "trading volume", "share volume"],
    "gross_value": [
        "gross value", "trade value", "transaction value", "notional",
        "consideration", "proceeds", "gross amount",
    ],
    "fees": [
        "fee", "fees", "commission", "commissions", "charges",
        "transaction cost", "broker fee",
    ],
    "transaction_id": [
        "transaction id", "txn id", "trade id", "order id", "reference",
        "transaction reference", "trade reference",
    ],
    "account": [
        "account", "portfolio", "book", "fund", "account name",
        "account id",
    ],
    "notes": [
        "notes", "note", "memo", "description", "details", "comment",
        "narrative", "remarks",
    ],
    "cash_amount": [
        "cash amount", "amount", "cash value", "cashflow", "cash flow",
    ],
    "market_value": [
        "market value", "current value", "position value", "holding value",
    ],
    "cost_basis": [
        "cost basis", "book cost", "book value", "total cost",
    ],
    "profit_loss": [
        "profit loss", "profit/loss", "pnl", "p&l", "gain loss",
        "gain/loss", "unrealised pnl", "unrealized pnl",
    ],
}

ABBREVIATIONS: dict[str, list[str]] = {
    "ticker": ["tkr", "sym"],
    "quantity": ["qnty", "qty"],
    "transaction_id": ["tx id", "txid", "txn", "txnid"],
    "gross_value": ["gross", "notional"],
    "fees": ["comm", "commission"],
    "datetime": ["dt", "timestamp"],
    "profit_loss": ["pnl", "p&l"],
}

ROLE_BLOCKED_CONTEXT: dict[str, set[str]] = {
    # Prevent generic action aliases such as ``type`` from consuming metadata.
    "action": {"security", "asset", "instrument", "account", "portfolio", "fund", "reference", "identifier", "id"},
    # Security roles must not consume transaction/order/reference identifiers.
    "ticker": {"transaction", "txn", "trade", "order", "reference", "identifier", "id"},
    # Transaction IDs must not consume account/security IDs.
    "transaction_id": {"account", "security", "asset", "instrument", "portfolio", "fund"},
}

ROLE_PRIORITY = [
    "ticker",
    "quantity",
    "datetime",
    "date",
    "time",
    "action",
    "transaction_id",
    "price",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "adjusted_close",
    "returns",
    "volume",
    "gross_value",
    "fees",
    "account",
    "notes",
    "cash_amount",
    "market_value",
    "cost_basis",
    "profit_loss",
]


# -----------------------------------------------------------------------------
# NORMALISATION AND MATCH SCORING
# -----------------------------------------------------------------------------

def normalise_header(value: Any) -> str:
    """Normalise a header while preserving word boundaries."""
    text = str(value).strip().lower()
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"[^a-z0-9/&+ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(value: str) -> list[str]:
    return [token for token in normalise_header(value).split() if token]


def _sequence_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio() * 100.0


def _phrase_in_tokens(keyword_tokens: list[str], column_tokens: list[str]) -> bool:
    if not keyword_tokens or len(keyword_tokens) > len(column_tokens):
        return False
    width = len(keyword_tokens)
    return any(
        column_tokens[i:i + width] == keyword_tokens
        for i in range(len(column_tokens) - width + 1)
    )


def _prefix_fragment_score(column_tokens: list[str], keyword_tokens: list[str]) -> float:
    """Controlled support for noisy headers such as ``tickerest_2``.

    Only a single keyword token of length >=4 may match the beginning of a
    header token. This deliberately rejects suffix collisions like
    ``action`` inside ``transaction``.
    """
    if len(keyword_tokens) != 1:
        return 0.0
    keyword = keyword_tokens[0]
    if len(keyword) < 4:
        return 0.0
    candidates = [token for token in column_tokens if token.startswith(keyword)]
    if not candidates:
        return 0.0
    best = max(candidates, key=lambda token: len(keyword) / max(len(token), 1))
    coverage = len(keyword) / max(len(best), 1)
    return min(89.0, 76.0 + 13.0 * coverage)


def score_term_match(column_name: Any, term: str) -> tuple[float, str]:
    """Score a header/alias match without arbitrary substring matching.

    Evidence hierarchy:
      exact alias > complete token phrase > controlled prefix fragment > fuzzy.
    Fuzzy similarity is deliberately capped below auto-accept thresholds.
    """
    column = normalise_header(column_name)
    keyword = normalise_header(term)

    if not column or not keyword:
        return 0.0, "no_match"
    if column == keyword:
        return 100.0, "exact"

    column_tokens = _tokens(column)
    keyword_tokens = _tokens(keyword)

    if _phrase_in_tokens(keyword_tokens, column_tokens):
        extra = max(0, len(column_tokens) - len(keyword_tokens))
        score = max(90.0, 96.0 - min(6.0, extra * 1.5))
        return score, "token_phrase"

    prefix_score = _prefix_fragment_score(column_tokens, keyword_tokens)
    if prefix_score:
        return prefix_score, "prefix_fragment"

    # All tokens present but not necessarily adjacent. Useful for headers such
    # as ``transaction broker type`` while still respecting word boundaries.
    if keyword_tokens and all(token in column_tokens for token in keyword_tokens):
        return min(89.0, 84.0 + len(keyword_tokens)), "token_set"

    fuzzy = _sequence_score(column, keyword)
    return min(fuzzy, 74.0), "sequence"


def score_column_role(column_name: Any, role: str) -> dict[str, Any]:
    terms = list(COLUMN_FAMILIES.get(role, [])) + list(ABBREVIATIONS.get(role, []))

    best_score = 0.0
    best_term = None
    best_rule = "no_match"

    for term in terms:
        score, rule = score_term_match(column_name, term)

        words = set(_tokens(str(column_name)))
        blocked = ROLE_BLOCKED_CONTEXT.get(role, set())
        term_n = normalise_header(term)

        # Context blocks are role-level safeguards. Exact, explicit semantic
        # phrases remain allowed (for example ``transaction type`` for action),
        # while generic aliases such as ``type`` cannot consume ``Security Type``.
        explicit_phrase = len(_tokens(term_n)) > 1 and rule in {"exact", "token_phrase"}
        if blocked and words & blocked and not explicit_phrase:
            score = min(score, 49.0)
            rule = "blocked_context"

        # Some finance words are valid security-column fallbacks but are much
        # less specific than an actual ticker/symbol header.  Without this
        # penalty an exact generic header such as ``Asset`` can incorrectly
        # beat a highly informative fragmented header such as ``tickerest_2``.
        if role == "ticker" and normalise_header(term) in {
            "asset", "stock", "security", "instrument"
        }:
            score = min(score, 78.0)
            if rule == "exact":
                rule = "generic_security_term"

        if score > best_score:
            best_score = score
            best_term = term
            best_rule = rule

    return {
        "role": role,
        "column": str(column_name),
        "score": round(float(best_score), 2),
        "matched_term": best_term,
        "rule": best_rule,
    }


def confidence_band(score: float) -> str:
    if score >= 90:
        return "AUTO_ACCEPT"
    if score >= 75:
        return "PROBABLE"
    if score >= 60:
        return "REVIEW"
    return "UNMAPPED"


# -----------------------------------------------------------------------------
# HEADER ROW DETECTION
# -----------------------------------------------------------------------------

def header_row_score(values: list[Any]) -> float:
    """Score one raw row as a potential header row."""
    useful_scores: list[float] = []

    for value in values:
        text = normalise_header(value)
        if not text or text in {"nan", "none", "nat"}:
            continue

        best = 0.0
        for role in ROLE_PRIORITY:
            match = score_column_role(text, role)
            best = max(best, float(match["score"]))

        if best >= 60:
            useful_scores.append(best)

    # Reward both match quality and the number of financial-looking cells.
    return sum(useful_scores) + len(useful_scores) * 20.0


def find_best_header_row(raw_frame: pd.DataFrame, max_rows: int = 20) -> tuple[int, float]:
    if raw_frame is None or raw_frame.empty:
        return 0, 0.0

    best_row = 0
    best_score = float("-inf")

    for row_index in range(min(max_rows, len(raw_frame))):
        score = header_row_score(raw_frame.iloc[row_index].tolist())
        if score > best_score:
            best_score = score
            best_row = row_index

    return best_row, max(best_score, 0.0)


# -----------------------------------------------------------------------------
# SCHEMA DETECTION
# -----------------------------------------------------------------------------



def _best_exact_header(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    """Return the first exact alias present, respecting alias priority."""
    by_norm = {normalise_header(col): col for col in frame.columns}
    for alias in aliases:
        col = by_norm.get(normalise_header(alias))
        if col is not None:
            return col
    return None


def _apply_transaction_schema_precedence(
    frame: pd.DataFrame,
    schema: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Prefer transaction semantics inside hybrid portfolio exports.

    Broker/watchlist exports often contain both trade fields and a current OHLC
    snapshot. In that context, ``Trade Date`` is the accounting date and
    ``Purchase Price``/``Trade Price`` is the accounting price. Generic market
    ``Date``/``Current Price`` must not steal those roles.
    """
    if frame is None or frame.empty:
        return schema

    action_col = _best_exact_header(frame, [
        "transaction type", "transaction action", "trade action",
        "trade side", "order side", "action", "side", "type",
    ])
    quantity_col = _best_exact_header(frame, [
        "quantity", "qty", "shares", "units", "number of shares",
    ])
    trade_date_col = _best_exact_header(frame, [
        "transaction date", "trade date", "execution date", "settlement date",
    ])
    entry_price_col = _best_exact_header(frame, [
        "trade price", "execution price", "purchase price", "entry price",
        "average entry price", "avg price", "average price", "cost price",
    ])

    strong_transaction_signature = bool(
        action_col and quantity_col and trade_date_col and entry_price_col
    )
    if not strong_transaction_signature:
        return schema

    adjusted = dict(schema)

    # Remove any role that currently consumes our preferred transaction columns.
    for role, col in (("date", trade_date_col), ("price", entry_price_col)):
        for existing_role in list(adjusted):
            if existing_role != role and adjusted[existing_role].get("column") == col:
                adjusted.pop(existing_role, None)

    adjusted["date"] = {
        **score_column_role(trade_date_col, "date"),
        "confidence": confidence_band(score_column_role(trade_date_col, "date")["score"]),
    }
    adjusted["price"] = {
        **score_column_role(entry_price_col, "price"),
        "confidence": confidence_band(score_column_role(entry_price_col, "price")["score"]),
    }

    # A transaction ID must be an actual identifier/reference column. A fuzzy
    # match such as ``Trade Date`` must never survive as transaction_id.
    txn = adjusted.get("transaction_id")
    if txn is not None:
        txn_norm = normalise_header(txn.get("column", ""))
        valid_txn_aliases = {normalise_header(x) for x in (
            COLUMN_FAMILIES["transaction_id"] + ABBREVIATIONS.get("transaction_id", [])
        )}
        if txn_norm not in valid_txn_aliases:
            adjusted.pop("transaction_id", None)

    return adjusted

def infer_schema(frame: pd.DataFrame, minimum_score: float = 60.0) -> dict[str, dict[str, Any]]:
    """
    Assign at most one dataframe column to each semantic role and at most one
    semantic role to each dataframe column.
    """
    if frame is None or frame.empty:
        return {}

    candidates: list[dict[str, Any]] = []

    for role in ROLE_PRIORITY:
        for column in frame.columns:
            match = score_column_role(column, role)
            match["confidence"] = confidence_band(match["score"])
            if match["score"] >= minimum_score:
                candidates.append(match)

    # Global best-first assignment prevents one generic "price" column from
    # being claimed by several semantic roles.
    priority_index = {role: i for i, role in enumerate(ROLE_PRIORITY)}
    candidates.sort(
        key=lambda item: (
            -float(item["score"]),
            priority_index.get(item["role"], 999),
        )
    )

    assigned_roles: set[str] = set()
    assigned_columns: set[str] = set()
    schema: dict[str, dict[str, Any]] = {}

    for item in candidates:
        role = item["role"]
        column = item["column"]

        if role in assigned_roles or column in assigned_columns:
            continue

        schema[role] = item
        assigned_roles.add(role)
        assigned_columns.add(column)

    return schema


# -----------------------------------------------------------------------------
# PORTFOLIO VS TRANSACTION-LOG CLASSIFICATION
# -----------------------------------------------------------------------------

def _normalised_action_values(frame: pd.DataFrame, schema: dict[str, dict[str, Any]]) -> set[str]:
    if "action" not in schema:
        return set()

    column = schema["action"]["column"]
    if column not in frame.columns:
        return set()

    values = (
        frame[column]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
    )
    return set(values.tolist())


def classify_frame(frame: pd.DataFrame, schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Classify the observed table before any cleaning/accounting logic runs."""
    roles = set(schema)
    action_values = _normalised_action_values(frame, schema)

    trade_words = {
        "BUY", "BOT", "BOUGHT", "PURCHASE", "PURCHASED", "BTO",
        "SELL", "SOLD", "SALE", "STO", "BUY TO COVER", "SELL TO CLOSE",
    }
    cash_words = {
        "DEPOSIT", "WITHDRAWAL", "DIV", "DIVIDEND", "INTEREST",
        "TRANSFER", "XFER", "CONTRIBUTION",
    }

    transaction_score = 0
    holdings_score = 0
    cashflow_score = 0
    market_data_score = 0
    evidence: list[str] = []

    if "ticker" in roles:
        holdings_score += 3
        transaction_score += 1
        market_data_score += 2
    if "quantity" in roles:
        holdings_score += 3
        transaction_score += 1
    if "date" in roles or "datetime" in roles:
        transaction_score += 2
        cashflow_score += 1
        market_data_score += 2
        evidence.append("date/datetime column detected")
    if "action" in roles:
        transaction_score += 3
        evidence.append("transaction action/side column detected")
    if "transaction_id" in roles:
        transaction_score += 3
        evidence.append("transaction ID column detected")
    if "fees" in roles:
        transaction_score += 1
    if "gross_value" in roles:
        transaction_score += 1
    if "market_value" in roles:
        holdings_score += 2
    if "cost_basis" in roles:
        holdings_score += 2

    market_roles = {
        "open_price", "high_price", "low_price", "close_price",
        "adjusted_close", "returns", "volume",
    }
    observed_market_roles = roles & market_roles
    market_data_score += 2 * len(observed_market_roles)

    ohlc_roles = {"open_price", "high_price", "low_price", "close_price"}
    if len(roles & ohlc_roles) >= 3:
        market_data_score += 5
        evidence.append("OHLC price-history columns detected")
    if "adjusted_close" in roles:
        market_data_score += 2
        evidence.append("adjusted-close column detected")
    if "volume" in roles:
        market_data_score += 1
        evidence.append("trading-volume column detected")
    if "returns" in roles:
        market_data_score += 1
        evidence.append("returns column detected")

    if action_values & trade_words:
        transaction_score += 4
        evidence.append("BUY/SELL-style values found")

    if action_values and action_values <= cash_words and "ticker" not in roles:
        cashflow_score += 6
    if "cash_amount" in roles:
        cashflow_score += 2

    # Repeated tickers mean very different things depending on the schema.
    # With OHLC/date fields they are normal time-series observations, not ledger evidence.
    if "ticker" in schema and schema["ticker"]["column"] in frame.columns:
        ticker_col = schema["ticker"]["column"]
        ticker_values = frame[ticker_col].dropna().astype(str).str.strip()
        if len(ticker_values) and ticker_values.duplicated().any():
            if observed_market_roles:
                market_data_score += 1
                evidence.append("repeated tickers across price-history rows found")
            else:
                transaction_score += 1
                evidence.append("repeated security identifiers found")

    # Strong transaction evidence outranks OHLC metadata. Hybrid broker/watchlist
    # exports can legitimately contain Current Price + OHLC + Volume alongside
    # Trade Date + Purchase Price + Quantity + BUY/SELL. Those files belong in
    # the ledger/accounting path, with market fields retained as reference data.
    strong_transaction_signature = (
        "action" in roles
        and "quantity" in roles
        and "price" in roles
        and ("date" in roles or "datetime" in roles)
        and bool(action_values & trade_words)
    )

    if strong_transaction_signature and "ticker" in roles:
        mode = "ledger"
        if market_data_score >= 10 and len(observed_market_roles) >= 2:
            evidence.append("hybrid portfolio export detected; transaction fields take precedence")
    # Pure market data still does not require Quantity.
    elif (
        market_data_score >= 10
        and ("date" in roles or "datetime" in roles)
        and len(observed_market_roles) >= 2
        and ("ticker" in roles or len(roles & ohlc_roles) >= 3)
    ):
        mode = "market_data"
    elif cashflow_score > max(transaction_score, holdings_score) and cashflow_score >= 5:
        mode = "cashflows"
    elif transaction_score >= holdings_score + 2 and "ticker" in roles and "quantity" in roles:
        mode = "ledger"
    elif "ticker" in roles and "quantity" in roles:
        mode = "holdings"
    else:
        mode = None

    return {
        "mode": mode,
        "transaction_score": transaction_score,
        "holdings_score": holdings_score,
        "cashflow_score": cashflow_score,
        "market_data_score": market_data_score,
        "evidence": evidence,
    }


# -----------------------------------------------------------------------------
# DATE PROFILE / AMBIGUITY DETECTION
# -----------------------------------------------------------------------------

def analyse_date_column(frame: pd.DataFrame, schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
    role = "datetime" if "datetime" in schema else "date" if "date" in schema else None

    result = {
        "present": role is not None,
        "role": role,
        "column": schema.get(role, {}).get("column") if role else None,
        "contains_time": False,
        "already_datetime": False,
        "slash_rows": 0,
        "dayfirst_evidence": 0,
        "monthfirst_evidence": 0,
        "ambiguous_rows": 0,
        "suggested_format": None,
        "requires_user_choice": False,
    }

    if role is None:
        return result

    column = schema[role]["column"]
    series = frame[column]

    result["already_datetime"] = bool(pd.api.types.is_datetime64_any_dtype(series))

    texts = series.dropna().astype(str).str.strip()
    result["contains_time"] = bool(texts.str.contains(r"\d{1,2}:\d{2}", regex=True).any())

    slash_pattern = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\D.*)?$")

    for text in texts:
        match = slash_pattern.match(text)
        if not match:
            continue

        result["slash_rows"] += 1
        first = int(match.group(1))
        second = int(match.group(2))

        if first > 12 and second <= 12:
            result["dayfirst_evidence"] += 1
        elif second > 12 and first <= 12:
            result["monthfirst_evidence"] += 1
        elif first <= 12 and second <= 12:
            result["ambiguous_rows"] += 1

    day_evidence = result["dayfirst_evidence"]
    month_evidence = result["monthfirst_evidence"]

    if day_evidence and not month_evidence:
        result["suggested_format"] = "DD/MM/YYYY"
    elif month_evidence and not day_evidence:
        result["suggested_format"] = "MM/DD/YYYY"
    elif day_evidence and month_evidence:
        result["suggested_format"] = "MIXED"
        result["requires_user_choice"] = True
    elif result["ambiguous_rows"]:
        result["suggested_format"] = "AMBIGUOUS"
        result["requires_user_choice"] = True

    return result


# -----------------------------------------------------------------------------
# COMPLETE PARSING REPORT
# -----------------------------------------------------------------------------

def build_parsing_report(
    frame: pd.DataFrame,
    *,
    header_row: int | None = None,
    header_score: float | None = None,
) -> dict[str, Any]:
    schema = infer_schema(frame)
    schema = _apply_transaction_schema_precedence(frame, schema)
    classification = classify_frame(frame, schema)
    date_profile = analyse_date_column(frame, schema)

    ticker_score = float(schema.get("ticker", {}).get("score", 0.0))
    quantity_score = float(schema.get("quantity", {}).get("score", 0.0))

    if classification["mode"] == "market_data":
        date_score = max(
            float(schema.get("date", {}).get("score", 0.0)),
            float(schema.get("datetime", {}).get("score", 0.0)),
        )
        market_role_count = len(set(schema) & {
            "open_price", "high_price", "low_price", "close_price",
            "adjusted_close", "returns", "volume",
        })
        # Ticker is optional for a strong single-security OHLC file; the upload
        # layer may infer the symbol from the file or sheet name.
        strong_ohlc = len(set(schema) & {"open_price", "high_price", "low_price", "close_price"}) >= 3
        required_ok = (
            date_score >= 75.0
            and market_role_count >= 2
            and (ticker_score >= 75.0 or strong_ohlc)
        )
    else:
        required_ok = ticker_score >= 75.0 and quantity_score >= 75.0

    warnings: list[str] = []
    if not required_ok and classification["mode"] not in {"cashflows", "market_data"}:
        warnings.append(
            "Ticker and Quantity were not both identified with at least 75% confidence."
        )
    if date_profile["requires_user_choice"]:
        warnings.append(
            "The date column contains an ambiguous or mixed DD/MM vs MM/DD pattern."
        )

    schema_rows = []
    for role in ROLE_PRIORITY:
        if role not in schema:
            continue
        item = schema[role]
        schema_rows.append({
            "Variable": role,
            "Detected Column": item["column"],
            "Match %": item["score"],
            "Confidence": item["confidence"],
            "Matched Term": item["matched_term"],
            "Match Rule": item["rule"],
        })

    return {
        "schema": schema,
        "schema_table": pd.DataFrame(schema_rows),
        "classification": classification,
        "date_profile": date_profile,
        "required_variables_valid": required_ok,
        "header_row": header_row,
        "header_score": header_score,
        "warnings": warnings,
    }
