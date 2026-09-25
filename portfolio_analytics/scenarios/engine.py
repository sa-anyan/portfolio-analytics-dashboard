"""Composable deterministic scenario functions.

Every action accepts a Portfolio State dictionary and returns a new state. Actions
never mutate the accepted user portfolio. Multiple actions are executed in order,
so later actions consume the state produced by earlier actions.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np
import pandas as pd

from portfolio_analytics.analytics.engine import returns_from_analytics, run_current_book_risk
from portfolio_analytics.core.portfolio_state import refresh_state_totals


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def _find_position(state: dict[str, Any], ticker: str) -> dict[str, Any] | None:
    target = str(ticker).strip().upper()
    for row in state.get("positions", []):
        if str(row.get("ticker") or "").upper() == target:
            return row
    return None


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    totals = state.get("totals", {})
    return {
        "equity": totals.get("equity"),
        "cash": totals.get("cash"),
        "gross_exposure": totals.get("gross_exposure"),
        "net_exposure": totals.get("net_exposure"),
    }


def _trade_to_quantity(
    state: dict[str, Any],
    ticker: str,
    new_quantity: float,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Execute one hypothetical trade at the current scenario mark.

    This helper preserves the accounting identity of the scenario book. It updates
    cash, average entry price and realised P&L using the same long/short average-cost
    rules as the accepted portfolio. No fee or slippage is invented.
    """
    result = deepcopy(state)
    row = _find_position(result, ticker)
    if row is None:
        raise ValueError(f"Position '{ticker}' was not found.")

    price = row.get("current_price")
    if price is None or float(price) <= 0:
        raise ValueError(f"A current price is required to resize {ticker}.")

    price = float(price)
    old_quantity = float(row.get("quantity") or 0.0)
    old_entry = row.get("average_entry_price")
    old_entry = float(old_entry) if old_entry is not None else None
    new_quantity = float(new_quantity)
    signed_trade = new_quantity - old_quantity

    if abs(signed_trade) < 1e-15:
        return refresh_state_totals(result), {
            "signed_trade_quantity": 0.0,
            "trade_price": price,
            "cash_change": 0.0,
            "realised_pnl_change": 0.0,
        }

    # Positive signed trade is a buy; negative signed trade is a sell.
    cash_change = -(signed_trade * price)
    result["cash"]["current"] = (
        float(result["cash"].get("current", 0.0) or 0.0) + cash_change
    )

    realised_change = 0.0

    # Opening from flat or increasing in the same direction.
    if abs(old_quantity) < 1e-15 or old_quantity * signed_trade > 0:
        old_abs = abs(old_quantity)
        add_abs = abs(signed_trade)
        if old_entry is None:
            # Existing unknown basis remains unknown. A fresh position opened from
            # flat has a known scenario basis at the execution mark.
            new_entry = price if old_abs < 1e-15 else None
        else:
            new_entry = (old_abs * old_entry + add_abs * price) / (old_abs + add_abs)
        row["quantity"] = new_quantity
        row["average_entry_price"] = new_entry

    else:
        # Reducing, closing or reversing an existing position.
        closing_quantity = min(abs(old_quantity), abs(signed_trade))
        if old_entry is not None:
            if old_quantity > 0:
                realised_change = closing_quantity * (price - old_entry)
            else:
                realised_change = closing_quantity * (old_entry - price)

        row["realised_pnl"] = float(row.get("realised_pnl") or 0.0) + realised_change
        row["quantity"] = new_quantity

        if abs(new_quantity) < 1e-15:
            row["average_entry_price"] = None
        elif old_quantity * new_quantity > 0:
            # Partial close: surviving position keeps its prior basis.
            row["average_entry_price"] = old_entry
        else:
            # Reversal: excess trade opens the opposite position at this mark.
            row["average_entry_price"] = price

    result["totals"]["realised_pnl"] = (
        float(result.get("totals", {}).get("realised_pnl") or 0.0)
        + realised_change
    )
    result = refresh_state_totals(result)

    return result, {
        "signed_trade_quantity": signed_trade,
        "trade_price": price,
        "cash_change": cash_change,
        "realised_pnl_change": realised_change,
    }


def _add_position(
    state: dict[str, Any],
    *,
    ticker: str,
    quantity: float,
    price: float,
    side: str = "LONG",
) -> dict[str, Any]:
    result = deepcopy(state)
    ticker = str(ticker).strip().upper()
    if _find_position(result, ticker) is not None:
        raise ValueError(f"Position '{ticker}' already exists; resize it instead.")
    if price <= 0:
        raise ValueError(f"A positive current price is required for {ticker}.")

    signed_quantity = abs(float(quantity)) if side.upper() != "SHORT" else -abs(float(quantity))
    result["positions"].append({
        "ticker": ticker,
        "asset_name": "",
        "asset_class": "",
        "quantity": signed_quantity,
        "side": "LONG" if signed_quantity > 0 else "SHORT",
        "average_entry_price": float(price),
        "current_price": float(price),
        "price_source": "scenario supplied",
        "signed_market_value": None,
        "exposure": None,
        "cost_basis": None,
        "realised_pnl": 0.0,
        "unrealised_pnl": 0.0,
        "duration": None,
        "rate_sensitivity": None,
    })
    result["cash"]["current"] = float(result["cash"].get("current", 0.0) or 0.0) - signed_quantity * float(price)
    return refresh_state_totals(result)


#______________________________________________________________________________
# PRICE SHOCK
#______________________________________________________________________________

def apply_price_shock(
    state: dict[str, Any],
    ticker: str,
    percent_change: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = deepcopy(state)
    row = _find_position(result, ticker)
    if row is None:
        raise ValueError(f"Position '{ticker}' was not found.")
    old_price = row.get("current_price")
    if old_price is None or float(old_price) <= 0:
        raise ValueError(f"A current price is required to shock {ticker}.")

    new_price = float(old_price) * (1.0 + float(percent_change) / 100.0)
    if new_price <= 0:
        raise ValueError("Price shock would make the asset price zero or negative.")

    row["current_price"] = new_price
    row["price_source"] = "scenario price shock"
    result = refresh_state_totals(result)
    return result, {
        "action": "price_shock",
        "ticker": str(ticker).upper(),
        "percent_change": float(percent_change),
        "old_price": float(old_price),
        "new_price": new_price,
    }


#______________________________________________________________________________
# INTEREST-RATE SHOCK
#______________________________________________________________________________

def apply_rate_shock(
    state: dict[str, Any],
    rate_change_bps: float,
    *,
    tickers: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a deterministic rate shock only where sensitivity is known.

    Priority:
    1. ``rate_sensitivity`` = percentage price change for a +100 bp rate move.
    2. ``duration`` = modified-duration first-order approximation ``-D * Δy``.

    Equities/crypto are NOT assigned an invented rate relationship. Positions
    without one of these inputs are left unchanged and reported as skipped.
    """
    result = deepcopy(state)
    selected = {str(t).strip().upper() for t in tickers if str(t).strip()} if tickers else None
    available = {str(row.get("ticker") or "").upper() for row in result.get("positions", [])}
    not_found = sorted(selected - available) if selected is not None else []
    changed: list[dict[str, Any]] = []
    skipped: list[str] = []

    for row in result.get("positions", []):
        ticker = str(row.get("ticker") or "").upper()
        if selected is not None and ticker not in selected:
            continue
        old_price = row.get("current_price")
        if old_price is None or float(old_price) <= 0:
            skipped.append(ticker)
            continue

        sensitivity = row.get("rate_sensitivity")
        duration = row.get("duration")

        if sensitivity is not None:
            # Example: -5 means +100 bp -> approximately -5% price change.
            pct_change = float(sensitivity) * (float(rate_change_bps) / 100.0)
            method = "user-supplied rate sensitivity"
        elif duration is not None:
            decimal_change = -float(duration) * (float(rate_change_bps) / 10_000.0)
            pct_change = decimal_change * 100.0
            method = "modified-duration approximation"
        else:
            skipped.append(ticker)
            continue

        new_price = float(old_price) * (1.0 + pct_change / 100.0)
        if new_price <= 0:
            raise ValueError(f"Rate shock makes {ticker}'s modelled price non-positive.")
        row["current_price"] = new_price
        row["price_source"] = "scenario rate shock"
        changed.append({
            "ticker": ticker,
            "method": method,
            "percent_price_change": pct_change,
            "old_price": float(old_price),
            "new_price": new_price,
        })

    result = refresh_state_totals(result)
    return result, {
        "action": "rate_shock",
        "rate_change_bps": float(rate_change_bps),
        "changed": changed,
        "skipped_no_rate_model": sorted(set(skipped)),
        "requested_not_found": not_found,
    }


#______________________________________________________________________________
# POSITION RESIZING
#______________________________________________________________________________

def apply_resize(
    state: dict[str, Any],
    ticker: str,
    *,
    percent_change: float | None = None,
    target_quantity: float | None = None,
    target_exposure: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = _find_position(state, ticker)
    if row is None:
        raise ValueError(f"Position '{ticker}' was not found.")
    old_quantity = float(row.get("quantity") or 0.0)
    price = row.get("current_price")
    if price is None or float(price) <= 0:
        raise ValueError(f"A current price is required to resize {ticker}.")

    modes = sum(value is not None for value in [percent_change, target_quantity, target_exposure])
    if modes != 1:
        raise ValueError("Resize requires exactly one of percent_change, target_quantity or target_exposure.")

    if percent_change is not None:
        new_quantity = old_quantity * (1.0 + float(percent_change) / 100.0)
        mode = "percent_change"
        requested = float(percent_change)
    elif target_quantity is not None:
        new_quantity = float(target_quantity)
        mode = "target_quantity"
        requested = float(target_quantity)
    else:
        direction = 1.0 if old_quantity >= 0 else -1.0
        new_quantity = direction * abs(float(target_exposure)) / float(price)
        mode = "target_exposure"
        requested = float(target_exposure)

    result, trade = _trade_to_quantity(state, ticker, new_quantity)
    return result, {
        "action": "resize",
        "ticker": str(ticker).upper(),
        "mode": mode,
        "requested": requested,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        **trade,
    }


#______________________________________________________________________________
# REALLOCATION
#______________________________________________________________________________

def apply_reallocation(
    state: dict[str, Any],
    from_ticker: str,
    to_ticker: str,
    *,
    amount: float | None = None,
    percent_of_source_exposure: float | None = None,
    to_price: float | None = None,
    to_side: str = "LONG",
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _find_position(state, from_ticker)
    if source is None:
        raise ValueError(f"Source position '{from_ticker}' was not found.")
    source_price = source.get("current_price")
    if source_price is None or float(source_price) <= 0:
        raise ValueError(f"A current price is required for {from_ticker}.")

    if (amount is None) == (percent_of_source_exposure is None):
        raise ValueError("Reallocation requires amount or percent_of_source_exposure, but not both.")

    source_exposure = abs(float(source.get("quantity") or 0.0) * float(source_price))
    shift = float(amount) if amount is not None else source_exposure * float(percent_of_source_exposure) / 100.0
    if shift <= 0 or shift > source_exposure + 1e-9:
        raise ValueError("Reallocation amount must be positive and cannot exceed source exposure.")

    source_direction = 1.0 if float(source["quantity"]) > 0 else -1.0
    remaining_exposure = source_exposure - shift
    new_source_quantity = source_direction * remaining_exposure / float(source_price)
    result, source_trade = _trade_to_quantity(state, from_ticker, new_source_quantity)

    target = _find_position(result, to_ticker)
    if target is not None:
        target_price = target.get("current_price")
        if target_price is None or float(target_price) <= 0:
            raise ValueError(f"A current price is required for {to_ticker}.")
        target_direction = 1.0 if float(target.get("quantity") or 0.0) >= 0 else -1.0
        new_target_quantity = float(target["quantity"]) + target_direction * shift / float(target_price)
        result, target_trade = _trade_to_quantity(result, to_ticker, new_target_quantity)
    else:
        target_trade = None
        if to_price is None or float(to_price) <= 0:
            raise ValueError(f"A scenario/current price is required to add new position {to_ticker}.")
        result = _add_position(
            result,
            ticker=to_ticker,
            quantity=shift / float(to_price),
            price=float(to_price),
            side=to_side,
        )

    return result, {
        "action": "reallocation",
        "from_ticker": str(from_ticker).upper(),
        "to_ticker": str(to_ticker).upper(),
        "shifted_notional": shift,
        "source_trade": source_trade,
        "target_trade": target_trade,
    }


#______________________________________________________________________________
# TARGET RISK
#______________________________________________________________________________

def apply_target_risk(
    state: dict[str, Any],
    analytics: dict[str, Any],
    target_annual_volatility: float,
    *,
    allow_leverage: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scale all risky positions proportionally toward a target annual volatility.

    This v4 baseline preserves the existing risky-asset mix and adjusts the cash
    residual. It is deterministic, auditable and avoids pretending an optimiser
    found a unique 'best' allocation. Reallocation optimisation can be layered on
    later without changing the scenario interface.
    """
    returns = returns_from_analytics(analytics)
    current = run_current_book_risk(
        state,
        returns,
        risk_free_rate=float(analytics.get("meta", {}).get("risk_free_rate", 0.0) or 0.0),
        var_level=float(analytics.get("meta", {}).get("var_level", 0.95) or 0.95),
    )
    current_vol = current.get("risk", {}).get("annual_volatility")
    target = float(target_annual_volatility)
    if target > 1.0:
        target = target / 100.0
    if target <= 0:
        raise ValueError("Target annual volatility must be positive.")
    if current_vol is None or float(current_vol) <= 0:
        raise ValueError("Current annual volatility is unavailable, so target risk cannot be solved.")

    scale = target / float(current_vol)
    if scale > 1.0 and not allow_leverage:
        raise ValueError(
            "Target risk is above current risk. Increasing exposure would require leverage; "
            "set allow_leverage=true to permit it."
        )

    result = deepcopy(state)
    changes: list[dict[str, Any]] = []

    # Capture tickers first because a trade to zero removes the flat row during
    # state refresh. Each resize is executed through the same hypothetical trade
    # accounting helper used by ordinary scenario resizing.
    targets = [
        (str(row.get("ticker") or "").upper(), float(row.get("quantity") or 0.0))
        for row in result.get("positions", [])
        if str(row.get("ticker") or "").strip()
    ]

    for ticker, old_q in targets:
        new_q = old_q * scale
        result, trade = _trade_to_quantity(result, ticker, new_q)
        changes.append({
            "ticker": ticker,
            "old_quantity": old_q,
            "new_quantity": new_q,
            **trade,
        })

    return result, {
        "action": "target_risk",
        "target_annual_volatility": target,
        "current_annual_volatility": float(current_vol),
        "scale_factor": scale,
        "allow_leverage": bool(allow_leverage),
        "changes": changes,
    }


#______________________________________________________________________________
# SCENARIO COMPARISON
#______________________________________________________________________________

def _position_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("ticker") or "").upper(): row
        for row in state.get("positions", [])
        if str(row.get("ticker") or "").strip()
    }


def compare_states(
    base_state: dict[str, Any],
    scenario_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return position-level before/after values for Copilot and the UI."""
    before = _position_map(base_state)
    after = _position_map(scenario_state)
    rows: list[dict[str, Any]] = []

    for ticker in sorted(set(before) | set(after)):
        left = before.get(ticker, {})
        right = after.get(ticker, {})
        old_quantity = float(left.get("quantity") or 0.0)
        new_quantity = float(right.get("quantity") or 0.0)
        old_price = left.get("current_price")
        new_price = right.get("current_price")

        price_change_pct = None
        if old_price is not None and new_price is not None and float(old_price) != 0:
            price_change_pct = (float(new_price) / float(old_price) - 1.0) * 100.0

        rows.append({
            "ticker": ticker,
            "quantity_before": old_quantity,
            "quantity_after": new_quantity,
            "quantity_change": new_quantity - old_quantity,
            "price_before": old_price,
            "price_after": new_price,
            "price_change_pct": price_change_pct,
            "market_value_before": left.get("signed_market_value"),
            "market_value_after": right.get("signed_market_value"),
            "exposure_before": left.get("exposure"),
            "exposure_after": right.get("exposure"),
            "average_entry_price_before": left.get("average_entry_price"),
            "average_entry_price_after": right.get("average_entry_price"),
            "realised_pnl_before": left.get("realised_pnl"),
            "realised_pnl_after": right.get("realised_pnl"),
            "unrealised_pnl_before": left.get("unrealised_pnl"),
            "unrealised_pnl_after": right.get("unrealised_pnl"),
        })

    return rows


#______________________________________________________________________________
# SCENARIO ACTION VALIDATION
#______________________________________________________________________________

ALLOWED_ACTIONS = {
    "price_shock",
    "rate_shock",
    "resize",
    "remove_position",
    "reallocation",
    "target_risk",
}


def validate_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate every LLM/user supplied instruction before any action runs."""
    if not isinstance(actions, list) or not actions:
        raise ValueError("Scenario requires at least one action.")

    def _finite_number(value: Any, label: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{label} requires a numeric value.")
        number = float(value)
        if not np.isfinite(number):
            raise ValueError(f"{label} must be finite.")
        return number

    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("Every scenario action must be a dictionary.")

        action = str(item.get("action") or "").strip().lower()
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported scenario action: {action}")

        if action in {"price_shock", "resize", "remove_position"}:
            if not str(item.get("ticker") or "").strip():
                raise ValueError(f"{action} requires a ticker.")

        if action == "price_shock":
            value = _finite_number(item.get("value"), "price_shock")
            if value <= -100.0:
                raise ValueError("price_shock must leave the asset price above zero.")

        elif action == "rate_shock":
            _finite_number(item.get("value"), "rate_shock")
            tickers = item.get("tickers")
            if tickers is not None and not isinstance(tickers, list):
                raise ValueError("rate_shock tickers must be a list when supplied.")

        elif action == "resize":
            supplied = [
                item.get("percent_change") is not None,
                item.get("target_quantity") is not None,
                item.get("target_exposure") is not None,
            ]
            if sum(supplied) != 1:
                raise ValueError("resize requires exactly one resize target.")
            if item.get("percent_change") is not None:
                value = _finite_number(item.get("percent_change"), "resize percent_change")
                if value < -100.0:
                    raise ValueError("resize percent_change cannot be below -100%; use target_quantity to reverse a position.")
            if item.get("target_quantity") is not None:
                _finite_number(item.get("target_quantity"), "resize target_quantity")
            if item.get("target_exposure") is not None:
                value = _finite_number(item.get("target_exposure"), "resize target_exposure")
                if value < 0:
                    raise ValueError("resize target_exposure cannot be negative.")

        elif action == "reallocation":
            source = str(item.get("from_ticker") or "").strip().upper()
            target = str(item.get("to_ticker") or "").strip().upper()
            if not source or not target:
                raise ValueError("reallocation requires from_ticker and to_ticker.")
            if source == target:
                raise ValueError("reallocation source and target must be different positions.")
            if (item.get("amount") is None) == (item.get("percent_of_source_exposure") is None):
                raise ValueError("reallocation requires amount or percent_of_source_exposure, but not both.")
            if item.get("amount") is not None:
                value = _finite_number(item.get("amount"), "reallocation amount")
                if value <= 0:
                    raise ValueError("reallocation amount must be positive.")
            if item.get("percent_of_source_exposure") is not None:
                value = _finite_number(item.get("percent_of_source_exposure"), "reallocation percent")
                if value <= 0 or value > 100:
                    raise ValueError("reallocation percent_of_source_exposure must be above 0 and at most 100.")
            if item.get("to_price") is not None:
                value = _finite_number(item.get("to_price"), "reallocation to_price")
                if value <= 0:
                    raise ValueError("reallocation to_price must be positive.")

        elif action == "target_risk":
            value = _finite_number(item.get("target_annual_volatility"), "target_risk")
            if value <= 0:
                raise ValueError("target_risk target_annual_volatility must be positive.")

    return actions



#______________________________________________________________________________
# COMPOSABLE SCENARIO RUNNER
#______________________________________________________________________________

def run_scenario(
    base_state: dict[str, Any],
    analytics: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run one or many scenario actions sequentially on a copy of the portfolio."""
    validate_actions(actions)
    state = deepcopy(base_state)
    action_log: list[dict[str, Any]] = []
    before = _snapshot(state)

    for raw_action in actions:
        action = str(raw_action.get("action") or "").strip().lower()
        action_before = _snapshot(state)

        if action == "price_shock":
            state, detail = apply_price_shock(
                state,
                str(raw_action.get("ticker") or ""),
                float(raw_action.get("value")),
            )
        elif action == "rate_shock":
            state, detail = apply_rate_shock(
                state,
                float(raw_action.get("value")),
                tickers=raw_action.get("tickers"),
            )
        elif action == "resize":
            state, detail = apply_resize(
                state,
                str(raw_action.get("ticker") or ""),
                percent_change=raw_action.get("percent_change"),
                target_quantity=raw_action.get("target_quantity"),
                target_exposure=raw_action.get("target_exposure"),
            )
        elif action == "remove_position":
            state, detail = apply_resize(
                state,
                str(raw_action.get("ticker") or ""),
                target_quantity=0.0,
            )
            detail["action"] = "remove_position"
        elif action == "reallocation":
            state, detail = apply_reallocation(
                state,
                str(raw_action.get("from_ticker") or ""),
                str(raw_action.get("to_ticker") or ""),
                amount=raw_action.get("amount"),
                percent_of_source_exposure=raw_action.get("percent_of_source_exposure"),
                to_price=raw_action.get("to_price"),
                to_side=str(raw_action.get("to_side") or "LONG"),
            )
        elif action == "target_risk":
            state, detail = apply_target_risk(
                state,
                analytics,
                float(raw_action.get("target_annual_volatility")),
                allow_leverage=bool(raw_action.get("allow_leverage", False)),
            )
        else:
            raise ValueError(f"Unsupported scenario action: {action}")

        detail["before"] = action_before
        detail["after"] = _snapshot(state)
        action_log.append(detail)

    returns = returns_from_analytics(analytics)
    scenario_risk = run_current_book_risk(
        state,
        returns,
        risk_free_rate=float(analytics.get("meta", {}).get("risk_free_rate", 0.0) or 0.0),
        var_level=float(analytics.get("meta", {}).get("var_level", 0.95) or 0.95),
    )

    return {
        "meta": {
            "action_count": len(actions),
            "execution": "sequential",
            "baseline_mutated": False,
            "risk_method": "static historical reweighting using scenario end-state exposures",
        },
        "base": before,
        "scenario_state": state,
        "scenario_analytics": scenario_risk,
        "actions": action_log,
        "comparison": {
            # Backward-compatible summary fields used by the current v4 UI.
            "equity_change": float(state.get("totals", {}).get("equity") or 0.0) - float(before.get("equity") or 0.0),
            "cash_change": float(state.get("totals", {}).get("cash") or 0.0) - float(before.get("cash") or 0.0),
            "gross_exposure_change": float(state.get("totals", {}).get("gross_exposure") or 0.0) - float(before.get("gross_exposure") or 0.0),
            "net_exposure_change": float(state.get("totals", {}).get("net_exposure") or 0.0) - float(before.get("net_exposure") or 0.0),
            "annual_volatility_before": analytics.get("risk", {}).get("annual_volatility"),
            "annual_volatility_after": scenario_risk.get("risk", {}).get("annual_volatility"),
            "var_value_before": analytics.get("risk", {}).get("var_value"),
            "var_value_after": scenario_risk.get("risk", {}).get("var_value"),
            "expected_shortfall_value_before": analytics.get("risk", {}).get("expected_shortfall_value"),
            "expected_shortfall_value_after": scenario_risk.get("risk", {}).get("expected_shortfall_value"),
            # Rich structured deltas for Copilot and future scenario visualisations.
            "portfolio": {
                "before": before,
                "after": _snapshot(state),
            },
            "risk": {
                "annual_volatility_before": analytics.get("risk", {}).get("annual_volatility"),
                "annual_volatility_after": scenario_risk.get("risk", {}).get("annual_volatility"),
                "var_value_before": analytics.get("risk", {}).get("var_value"),
                "var_value_after": scenario_risk.get("risk", {}).get("var_value"),
                "expected_shortfall_value_before": analytics.get("risk", {}).get("expected_shortfall_value"),
                "expected_shortfall_value_after": scenario_risk.get("risk", {}).get("expected_shortfall_value"),
            },
            "positions": compare_states(base_state, state),
        },
    }
