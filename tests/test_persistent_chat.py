from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.ai.copilot import build_copilot_context, execute_route
from portfolio_analytics.ai.router import validate_route
from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


def _objects():
    parsed = parse_dataframe(pd.DataFrame({
        "Ticker": ["BTC-USD", "GLD"],
        "Quantity": [-2.0, 10.0],
        "Average Entry Price": [100.0, 180.0],
        "Current Price": [80.0, 200.0],
        "Asset Class": ["Crypto", "Commodity"],
        "Duration": [None, 4.0],
    }))
    state = build_portfolio_state(parsed)
    dates = pd.bdate_range("2025-01-01", periods=120)
    history = pd.DataFrame({
        "BTC-USD": 80 * np.cumprod(1 + np.linspace(-0.015, 0.016, 120)),
        "GLD": 200 * np.cumprod(1 + np.linspace(-0.004, 0.005, 120)),
    }, index=dates)
    analytics = run_analytics(state, history)
    return parsed, state, analytics


def test_latest_scenario_lookup_reads_scenario_without_changing_accepted_state():
    parsed, state, analytics = _objects()
    baseline = deepcopy(state)
    context = build_copilot_context(parsed, state, analytics)
    first_route = validate_route({
        "intent": "scenario", "ticker": None, "fields": [],
        "actions": [{"action": "price_shock", "ticker": "BTC", "value": -25}],
        "scope": "accepted", "scenario_base": "accepted", "clarification": None,
    }, context)
    first = execute_route(first_route, state=state, analytics=analytics, context=context)["scenario"]

    context2 = build_copilot_context(parsed, state, analytics, first)
    lookup_route = validate_route({
        "intent": "lookup", "ticker": "BTC", "fields": ["current_price"],
        "actions": [], "scope": "latest_scenario", "scenario_base": "accepted", "clarification": None,
    }, context2)
    result = execute_route(lookup_route, state=state, analytics=analytics, context=context2)["result"]
    assert result["scope"] == "latest_scenario"
    assert result["portfolio"]["position"]["current_price"] == pytest.approx(60.0)
    assert state == baseline


def test_followup_scenario_can_continue_from_latest_hypothetical_state():
    parsed, state, analytics = _objects()
    baseline = deepcopy(state)
    context = build_copilot_context(parsed, state, analytics)
    first_route = validate_route({
        "intent": "scenario", "ticker": None, "fields": [],
        "actions": [{"action": "price_shock", "ticker": "BTC", "value": -25}],
        "scope": "accepted", "scenario_base": "accepted", "clarification": None,
    }, context)
    first = execute_route(first_route, state=state, analytics=analytics, context=context)["scenario"]

    context2 = build_copilot_context(parsed, state, analytics, first)
    second_route = validate_route({
        "intent": "scenario", "ticker": None, "fields": [],
        "actions": [{"action": "resize", "ticker": "BTC", "percent_change": -50}],
        "scope": "latest_scenario", "scenario_base": "latest_scenario", "clarification": None,
    }, context2)
    second = execute_route(second_route, state=state, analytics=analytics, context=context2)["scenario"]
    btc = next(row for row in second["scenario_state"]["positions"] if row["ticker"] == "BTC-USD")
    assert btc["current_price"] == pytest.approx(60.0)
    assert btc["quantity"] == pytest.approx(-1.0)
    assert second["meta"]["conversation_base"] == "latest_scenario"
    assert state == baseline


def test_missing_latest_scenario_forces_routes_back_to_accepted_portfolio():
    parsed, state, analytics = _objects()
    context = build_copilot_context(parsed, state, analytics)
    route = validate_route({
        "intent": "lookup", "ticker": "BTC", "fields": ["quantity"], "actions": [],
        "scope": "latest_scenario", "scenario_base": "latest_scenario", "clarification": None,
    }, context)
    assert route["scope"] == "accepted"
    assert route["scenario_base"] == "accepted"


def test_invalid_scope_is_rejected():
    parsed, state, analytics = _objects()
    context = build_copilot_context(parsed, state, analytics)
    with pytest.raises(ValueError, match="scope"):
        validate_route({
            "intent": "lookup", "ticker": "BTC", "fields": ["quantity"], "actions": [],
            "scope": "imaginary", "scenario_base": "accepted", "clarification": None,
        }, context)
