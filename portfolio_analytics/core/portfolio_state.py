"""Build the single canonical Portfolio State consumed by v4 downstream engines."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


#______________________________________________________________________________
# POSITION ACCOUNTING
#______________________________________________________________________________

@dataclass
class _Position:
    quantity: float = 0.0
    average_entry_price: float = 0.0
    realised_pnl: float = 0.0
    asset_name: str = ""
    asset_class: str = ""
    duration: float | None = None
    rate_sensitivity: float | None = None


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _apply_signed_trade(position: _Position, signed_quantity: float, price: float) -> float:
    """Apply a signed trade using average-cost accounting.

    Positive signed quantity is a buy; negative signed quantity is a sell.
    Position quantity is positive for long and negative for short.
    """
    old_q = float(position.quantity)
    trade_q = float(signed_quantity)
    new_q = old_q + trade_q
    realised = 0.0

    if abs(trade_q) < 1e-15:
        return 0.0

    # Opening or adding in the same direction.
    if abs(old_q) < 1e-15 or old_q * trade_q > 0:
        old_abs = abs(old_q)
        add_abs = abs(trade_q)
        position.average_entry_price = (
            (old_abs * position.average_entry_price + add_abs * price)
            / (old_abs + add_abs)
        )
        position.quantity = new_q
        return 0.0

    # Reducing, closing or reversing the position.
    closing_qty = min(abs(old_q), abs(trade_q))
    if old_q > 0:
        realised = closing_qty * (price - position.average_entry_price)
    else:
        realised = closing_qty * (position.average_entry_price - price)

    position.realised_pnl += realised

    if abs(new_q) < 1e-15:
        position.quantity = 0.0
        position.average_entry_price = 0.0
    elif old_q * new_q > 0:
        position.quantity = new_q
    else:
        position.quantity = new_q
        position.average_entry_price = price

    return realised


#______________________________________________________________________________
# HOLDINGS PATH
#______________________________________________________________________________

def _positions_from_holdings(records: list[dict[str, Any]]) -> dict[str, _Position]:
    states: dict[str, _Position] = {}

    for row in records:
        ticker = str(row.get("Ticker") or "").strip().upper()
        quantity = _optional_float(row.get("Quantity"))
        if not ticker or quantity is None or abs(quantity) < 1e-15:
            continue

        entry = _optional_float(row.get("Average Entry Price"))
        current = states.setdefault(ticker, _Position())

        # Holdings snapshots can contain duplicate rows. Aggregate quantity and
        # derive a weighted entry price only from rows with known cost basis.
        old_q = current.quantity
        new_q = old_q + quantity

        if entry is not None and entry > 0:
            if abs(old_q) < 1e-15 or old_q * quantity > 0:
                old_cost_qty = abs(old_q) if current.average_entry_price > 0 else 0.0
                add_qty = abs(quantity)
                denominator = old_cost_qty + add_qty
                if denominator > 0:
                    current.average_entry_price = (
                        old_cost_qty * current.average_entry_price + add_qty * entry
                    ) / denominator
            elif old_q * new_q <= 0:
                # A mixed-sign snapshot is inherently ambiguous. The surviving
                # direction uses the row's basis instead of inventing realised P&L.
                current.average_entry_price = entry if abs(new_q) > 1e-15 else 0.0

        current.quantity = new_q
        current.asset_name = str(row.get("Asset Name") or current.asset_name or "")
        current.asset_class = str(row.get("Asset Class") or current.asset_class or "")
        current.duration = _optional_float(row.get("Duration")) if row.get("Duration") is not None else current.duration
        current.rate_sensitivity = (
            _optional_float(row.get("Rate Sensitivity"))
            if row.get("Rate Sensitivity") is not None
            else current.rate_sensitivity
        )

    return {ticker: state for ticker, state in states.items() if abs(state.quantity) > 1e-15}


#______________________________________________________________________________
# DATED HOLDINGS -> ACCOUNTING EVENTS
#______________________________________________________________________________

def _accounting_history_from_holdings(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert dated current holdings into deterministic acquisition events.

    Each dated non-cash holding with a supplied entry price becomes one inferred
    acquisition at that exact user-supplied price. A matching external funding
    flow is recorded so investment performance is not distorted by the purchase
    cash movement. This reconstructs the supplied current holdings only; it does
    not invent previously sold positions or missing dividends.
    """
    trades: list[dict[str, Any]] = []
    cashflows: list[dict[str, Any]] = []
    missing: list[str] = []

    for i, row in enumerate(records):
        ticker = str(row.get("Ticker") or "").strip().upper()
        asset_class = str(row.get("Asset Class") or "").strip()
        if not ticker or ticker == "CASH" or asset_class.lower() == "cash":
            continue
        quantity = _optional_float(row.get("Quantity"))
        price = _optional_float(row.get("Average Entry Price"))
        date = pd.to_datetime(row.get("Purchase Date"), errors="coerce")
        if quantity is None or abs(quantity) < 1e-15:
            continue
        if pd.isna(date) or price is None or price <= 0:
            missing.append(ticker)
            continue

        signed_quantity = float(quantity)
        trade_type = "BUY" if signed_quantity > 0 else "SELL"
        trade_value = abs(signed_quantity * float(price))
        event_id = f"HOLDING-{i+1}"
        trades.append({
            "Date": date.normalize(),
            "Type": trade_type,
            "Ticker": ticker,
            "Asset Name": str(row.get("Asset Name") or ""),
            "Quantity": abs(signed_quantity),
            "Signed Quantity": signed_quantity,
            "Price": float(price),
            "Gross Value": trade_value,
            "Fees": 0.0,
            "Transaction ID": event_id,
            "Asset Class": asset_class,
            "Currency": str(row.get("Currency") or "USD").upper(),
            "Duration": row.get("Duration"),
            "Rate Sensitivity": row.get("Rate Sensitivity"),
        })
        # The holdings snapshot does not provide its historical funding ledger.
        # Treat acquisition cost as an inferred external contribution.
        cashflows.append({
            "Date": date.normalize(),
            "Type": "DEPOSIT",
            "Amount": trade_value,
            "Transaction ID": f"{event_id}-FUNDING",
            "Currency": str(row.get("Currency") or "USD").upper(),
        })

    return {
        "available": bool(trades),
        "method": "reconstructed dated holdings",
        "trades": trades,
        "cashflows": cashflows,
        "missing_dated_basis": sorted(set(missing)),
    }


#______________________________________________________________________________
# LEDGER PATH
#______________________________________________________________________________

def _positions_from_ledger(
    trade_records: list[dict[str, Any]],
    cashflow_records: list[dict[str, Any]],
    starting_cash: float,
) -> tuple[dict[str, _Position], float, list[dict[str, Any]], float]:
    events: list[dict[str, Any]] = []

    for row in trade_records:
        date = pd.to_datetime(row.get("Date"), errors="coerce")
        if pd.isna(date):
            continue
        events.append({"kind": "trade", "date": date, "row": row})

    for row in cashflow_records:
        date = pd.to_datetime(row.get("Date"), errors="coerce")
        if pd.isna(date):
            continue
        events.append({"kind": "cashflow", "date": date, "row": row})

    events.sort(key=lambda event: event["date"])

    positions: dict[str, _Position] = {}
    cash = float(starting_cash)
    external_net_flows = 0.0
    accounting_log: list[dict[str, Any]] = []

    for event in events:
        row = event["row"]
        cash_before = cash

        if event["kind"] == "cashflow":
            event_type = str(row.get("Type") or "").upper()
            amount = abs(float(row.get("Amount") or 0.0))
            if event_type in {"DEPOSIT", "DIVIDEND"}:
                cash_change = amount
            else:
                cash_change = -amount
            cash += cash_change
            if event_type == "DEPOSIT":
                external_net_flows += amount
            elif event_type == "WITHDRAWAL":
                external_net_flows -= amount

            accounting_log.append({
                "date": event["date"].isoformat(),
                "event": event_type,
                "ticker": "",
                "signed_quantity": 0.0,
                "price": 0.0,
                "cash_before": cash_before,
                "cash_change": cash_change,
                "cash_after": cash,
                "realised_pnl": 0.0,
            })
            continue

        ticker = str(row.get("Ticker") or "").upper()
        signed_quantity = float(row.get("Signed Quantity") or 0.0)
        price = float(row.get("Price") or 0.0)
        fees = abs(float(row.get("Fees") or 0.0))
        if not ticker or abs(signed_quantity) < 1e-15 or price <= 0:
            continue

        state = positions.setdefault(ticker, _Position())
        state.asset_name = str(row.get("Asset Name") or state.asset_name or "")
        state.asset_class = str(row.get("Asset Class") or state.asset_class or "")
        state.duration = _optional_float(row.get("Duration")) if row.get("Duration") is not None else state.duration
        state.rate_sensitivity = (
            _optional_float(row.get("Rate Sensitivity"))
            if row.get("Rate Sensitivity") is not None
            else state.rate_sensitivity
        )

        # Positive signed quantity is a BUY, negative is a SELL.
        cash_change = -(signed_quantity * price) - fees
        cash += cash_change
        realised = _apply_signed_trade(state, signed_quantity, price)

        accounting_log.append({
            "date": event["date"].isoformat(),
            "event": "BUY" if signed_quantity > 0 else "SELL",
            "ticker": ticker,
            "signed_quantity": signed_quantity,
            "price": price,
            "cash_before": cash_before,
            "cash_change": cash_change,
            "cash_after": cash,
            "realised_pnl": realised,
        })

    positions = {ticker: state for ticker, state in positions.items() if abs(state.quantity) > 1e-15}
    return positions, cash, accounting_log, external_net_flows


#______________________________________________________________________________
# PRICE RESOLUTION
#______________________________________________________________________________

def _resolve_prices(
    parsed: dict[str, Any],
    positions: dict[str, _Position],
    latest_prices: dict[str, float] | None,
) -> tuple[dict[str, float], list[str], dict[str, str]]:
    latest = {str(k).upper(): float(v) for k, v in (latest_prices or {}).items() if _optional_float(v) is not None and float(v) > 0}
    supplied = {
        str(k).upper(): float(v)
        for k, v in parsed.get("variables", {}).get("provided_prices", {}).items()
        if _optional_float(v) is not None and float(v) > 0
    }

    prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    for ticker, position in positions.items():
        # CASH is a reserved non-market asset. It must never be repriced from a
        # security that happens to trade under the symbol CASH. One unit of cash
        # remains one unit in its stated currency; FX conversion belongs upstream.
        is_cash = ticker == "CASH" or str(position.asset_class or "").strip().lower() == "cash"
        if is_cash:
            prices[ticker] = 1.0
            sources[ticker] = "cash/unit value"
        elif ticker in latest:
            prices[ticker] = latest[ticker]
            sources[ticker] = "market/latest"
        elif ticker in supplied:
            prices[ticker] = supplied[ticker]
            sources[ticker] = "user supplied"

    missing = [ticker for ticker in positions if ticker not in prices]
    return prices, missing, sources


#______________________________________________________________________________
# BUILD PORTFOLIO STATE
#______________________________________________________________________________

def build_portfolio_state(
    parsed: dict[str, Any],
    *,
    latest_prices: dict[str, float] | None = None,
    market_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the one canonical state dictionary used by every downstream engine."""
    classification = parsed.get("classification")
    normalised = parsed.get("normalised_dataset", {})
    starting_cash = float(parsed.get("inputs", {}).get("starting_cash", 0.0) or 0.0)

    if classification == "holdings":
        holding_records = normalised.get("holdings", [])
        cash_records = [
            row for row in holding_records
            if str(row.get("Ticker") or "").strip().upper() == "CASH"
            or str(row.get("Asset Class") or "").strip().lower() == "cash"
        ]
        investment_records = [row for row in holding_records if row not in cash_records]
        positions = _positions_from_holdings(investment_records)

        # Explicit snapshot cash is account cash, not a quoted security.
        explicit_cash = 0.0
        for row in cash_records:
            quantity = _optional_float(row.get("Quantity"))
            unit_value = _optional_float(row.get("Current Price"))
            if quantity is not None:
                explicit_cash += float(quantity) * float(unit_value if unit_value is not None else 1.0)

        cash = starting_cash + explicit_cash
        accounting_log: list[dict[str, Any]] = []
        external_net_flows = 0.0
    elif classification == "ledger":
        positions, cash, accounting_log, external_net_flows = _positions_from_ledger(
            normalised.get("ledger", []),
            normalised.get("cashflows", []),
            starting_cash,
        )
    else:
        raise ValueError(f"Unsupported parsed portfolio classification: {classification}")

    if classification == "ledger":
        accounting_history = {
            "available": bool(normalised.get("ledger")),
            "method": "actual transaction ledger",
            "trades": deepcopy(normalised.get("ledger", [])),
            "cashflows": deepcopy(normalised.get("cashflows", [])),
            "missing_dated_basis": [],
        }
    else:
        accounting_history = _accounting_history_from_holdings(normalised.get("holdings", []))

    prices, missing_prices, price_sources = _resolve_prices(parsed, positions, latest_prices)

    rows: list[dict[str, Any]] = []
    total_realised = (
        float(sum(float(row.get("realised_pnl") or 0.0) for row in accounting_log))
        if classification == "ledger"
        else 0.0
    )
    total_unrealised_values: list[float] = []

    for ticker, position in positions.items():
        price = prices.get(ticker)
        quantity = float(position.quantity)
        entry = float(position.average_entry_price) if position.average_entry_price > 0 else None
        signed_value = quantity * price if price is not None else None
        exposure = abs(signed_value) if signed_value is not None else None
        cost_basis = abs(quantity) * entry if entry is not None else None
        unrealised = quantity * (price - entry) if price is not None and entry is not None else None
        if unrealised is not None:
            total_unrealised_values.append(float(unrealised))
        rows.append({
            "ticker": ticker,
            "asset_name": position.asset_name,
            "asset_class": position.asset_class,
            "quantity": quantity,
            "side": "LONG" if quantity > 0 else "SHORT",
            "average_entry_price": entry,
            "current_price": price,
            "price_source": price_sources.get(ticker),
            "signed_market_value": signed_value,
            "exposure": exposure,
            "cost_basis": cost_basis,
            "realised_pnl": float(position.realised_pnl),
            "unrealised_pnl": unrealised,
            "duration": position.duration,
            "rate_sensitivity": position.rate_sensitivity,
        })

    valued_rows = [row for row in rows if row["signed_market_value"] is not None]
    signed_market_value = float(sum(float(row["signed_market_value"]) for row in valued_rows))
    long_exposure = float(sum(float(row["signed_market_value"]) for row in valued_rows if float(row["quantity"]) > 0))
    short_exposure = float(-sum(float(row["signed_market_value"]) for row in valued_rows if float(row["quantity"]) < 0))
    gross_exposure = long_exposure + short_exposure
    net_exposure = long_exposure - short_exposure
    equity = float(cash + signed_market_value)
    total_cost_basis = float(sum(float(row["cost_basis"]) for row in rows if row["cost_basis"] is not None))
    unrealised_total = float(sum(total_unrealised_values)) if total_unrealised_values else None

    quantities = [abs(float(row["quantity"])) for row in rows]

    state = {
        "meta": {
            "source_kind": parsed.get("source", {}).get("kind"),
            "source_filename": parsed.get("source", {}).get("filename"),
            "path": classification,
            "valuation_as_of": (market_metadata or {}).get("as_of"),
            "market_source": (market_metadata or {}).get("source"),
            "missing_prices": missing_prices,
            "parser_confidence": parsed.get("confidence"),
        },
        "inputs": {
            "starting_cash": starting_cash,
            "latest_prices": prices,
            "user_dataset": deepcopy(parsed.get("user_dataset", {})),
            "normalised_dataset": deepcopy(normalised),
        },
        "positions": rows,
        "cash": {
            "starting": starting_cash,
            "current": float(cash),
            "explicit_snapshot_cash": float(explicit_cash) if classification == "holdings" else 0.0,
            "external_net_flows": float(external_net_flows),
        },
        "totals": {
            "position_count": len(rows),
            "net_quantity": float(sum(float(row["quantity"]) for row in rows)),
            "total_absolute_quantity": float(sum(quantities)),
            "average_absolute_quantity": float(np.mean(quantities)) if quantities else 0.0,
            "signed_market_value": signed_market_value,
            "long_exposure": long_exposure,
            "short_exposure": short_exposure,
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "cash": float(cash),
            "equity": equity,
            "gross_leverage": gross_exposure / abs(equity) if abs(equity) > 1e-12 else None,
            "net_leverage": net_exposure / abs(equity) if abs(equity) > 1e-12 else None,
            "cost_basis": total_cost_basis if total_cost_basis > 0 else None,
            "realised_pnl": total_realised,
            "unrealised_pnl": unrealised_total,
        },
        "accounting_history": accounting_history,
        "accounting_log": accounting_log,
    }

    # A dedicated copy is kept for scenario restoration. Scenario functions must
    # work on deep copies and never mutate this baseline state.
    state["scenario_baseline"] = {
        "positions": deepcopy(rows),
        "cash": deepcopy(state["cash"]),
        "totals": deepcopy(state["totals"]),
    }

    return state


#______________________________________________________________________________
# STATE REFRESH FOR SCENARIOS
#______________________________________________________________________________

def refresh_state_totals(state: dict[str, Any]) -> dict[str, Any]:
    """Recalculate valuation-derived fields after a deterministic scenario action."""
    result = deepcopy(state)
    rows = result.get("positions", [])

    for row in rows:
        quantity = float(row.get("quantity") or 0.0)
        price = _optional_float(row.get("current_price"))
        entry = _optional_float(row.get("average_entry_price"))
        row["side"] = "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT"
        row["signed_market_value"] = quantity * price if price is not None else None
        row["exposure"] = abs(row["signed_market_value"]) if row["signed_market_value"] is not None else None
        row["cost_basis"] = abs(quantity) * entry if entry is not None else None
        row["unrealised_pnl"] = quantity * (price - entry) if price is not None and entry is not None else None

    rows[:] = [row for row in rows if abs(float(row.get("quantity") or 0.0)) > 1e-15]
    valued = [row for row in rows if row.get("signed_market_value") is not None]
    cash = float(result.get("cash", {}).get("current", 0.0) or 0.0)
    signed_value = float(sum(float(row["signed_market_value"]) for row in valued))
    long_exposure = float(sum(float(row["signed_market_value"]) for row in valued if float(row["quantity"]) > 0))
    short_exposure = float(-sum(float(row["signed_market_value"]) for row in valued if float(row["quantity"]) < 0))
    equity = cash + signed_value
    quantities = [abs(float(row["quantity"])) for row in rows]

    result["totals"].update({
        "position_count": len(rows),
        "net_quantity": float(sum(float(row["quantity"]) for row in rows)),
        "total_absolute_quantity": float(sum(quantities)),
        "average_absolute_quantity": float(np.mean(quantities)) if quantities else 0.0,
        "signed_market_value": signed_value,
        "long_exposure": long_exposure,
        "short_exposure": short_exposure,
        "gross_exposure": long_exposure + short_exposure,
        "net_exposure": long_exposure - short_exposure,
        "cash": cash,
        "equity": equity,
        "gross_leverage": (long_exposure + short_exposure) / abs(equity) if abs(equity) > 1e-12 else None,
        "net_leverage": (long_exposure - short_exposure) / abs(equity) if abs(equity) > 1e-12 else None,
        "cost_basis": float(sum(float(row["cost_basis"]) for row in rows if row.get("cost_basis") is not None)) or None,
        # Historical realised P&L is preserved across hypothetical scenario marks.
        # Scenario trades are temporary and do not rewrite the accepted ledger.
        "realised_pnl": result.get("totals", {}).get("realised_pnl"),
        "unrealised_pnl": (
            float(sum(float(row["unrealised_pnl"]) for row in rows if row.get("unrealised_pnl") is not None))
            if any(row.get("unrealised_pnl") is not None for row in rows)
            else None
        ),
    })

    return result
