from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.ai.copilot import build_copilot_context, execute_route
from portfolio_analytics.ai.router import normalise_route, parse_route_text, validate_route
from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


#______________________________________________________________________________
# FIXTURE BUILDERS
#______________________________________________________________________________

def _engine_objects():
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
    btc = 80 * np.cumprod(1 + np.linspace(-0.015, 0.016, 120))
    gld = 200 * np.cumprod(1 + np.linspace(-0.004, 0.005, 120))
    history = pd.DataFrame({"BTC-USD": btc, "GLD": gld}, index=dates)
    analytics = run_analytics(state, history)
    context = build_copilot_context(parsed, state, analytics)
    return parsed, state, analytics, context


#______________________________________________________________________________
# NORMALISATION
#______________________________________________________________________________

def test_route_normalises_ticker_aliases_and_field_names():
    route = normalise_route({
        "intent": "LOOKUP",
        "ticker": "btc",
        "fields": ["Current Price", "quantity", "Current Price"],
        "actions": [],
        "clarification": None,
    })
    assert route["intent"] == "lookup"
    assert route["ticker"] == "BTC-USD"
    assert route["fields"] == ["current_price", "quantity"]


def test_action_ticker_aliases_are_normalised():
    route = normalise_route({
        "intent": "scenario",
        "ticker": None,
        "fields": [],
        "actions": [
            {"action": "price_shock", "ticker": "btc", "value": -20},
            {"action": "reallocation", "from_ticker": "btc", "to_ticker": "eth", "amount": 20, "to_price": 50},
        ],
        "clarification": None,
    })
    assert route["actions"][0]["ticker"] == "BTC-USD"
    assert route["actions"][1]["from_ticker"] == "BTC-USD"
    assert route["actions"][1]["to_ticker"] == "ETH-USD"


#______________________________________________________________________________
# VALIDATION BOUNDARY
#______________________________________________________________________________

def test_lookup_must_reference_open_position():
    _, _, _, context = _engine_objects()
    with pytest.raises(ValueError, match="not an open position"):
        validate_route({
            "intent": "lookup",
            "ticker": "NVDA",
            "fields": ["quantity"],
            "actions": [],
            "clarification": None,
        }, context)


def test_unknown_lookup_field_is_rejected():
    _, _, _, context = _engine_objects()
    with pytest.raises(ValueError, match="Unsupported lookup field"):
        validate_route({
            "intent": "lookup",
            "ticker": "BTC",
            "fields": ["secret_metric"],
            "actions": [],
            "clarification": None,
        }, context)


def test_non_scenario_intent_cannot_hide_actions():
    _, _, _, context = _engine_objects()
    with pytest.raises(ValueError, match="cannot contain scenario actions"):
        validate_route({
            "intent": "lookup",
            "ticker": "BTC",
            "fields": ["quantity"],
            "actions": [{"action": "price_shock", "ticker": "BTC", "value": -20}],
            "clarification": None,
        }, context)


def test_scenario_action_must_reference_open_position():
    _, _, _, context = _engine_objects()
    with pytest.raises(ValueError, match="not an open position"):
        validate_route({
            "intent": "scenario",
            "ticker": None,
            "fields": [],
            "actions": [{"action": "price_shock", "ticker": "NVDA", "value": -20}],
            "clarification": None,
        }, context)


def test_reallocation_new_target_requires_price():
    _, _, _, context = _engine_objects()
    with pytest.raises(ValueError, match="needs a positive to_price"):
        validate_route({
            "intent": "scenario",
            "ticker": None,
            "fields": [],
            "actions": [{
                "action": "reallocation",
                "from_ticker": "GLD",
                "to_ticker": "NVDA",
                "amount": 100,
                "percent_of_source_exposure": None,
                "to_price": None,
                "to_side": "LONG",
            }],
            "clarification": None,
        }, context)


def test_ambiguous_route_can_become_clarification():
    _, _, _, context = _engine_objects()
    route = validate_route({
        "intent": "clarification",
        "ticker": None,
        "fields": [],
        "actions": [],
        "clarification": "Do you mean annualised volatility or VaR?",
    }, context)
    assert route["intent"] == "clarification"
    assert "volatility" in route["clarification"]


def test_invalid_json_is_rejected_before_execution():
    _, _, _, context = _engine_objects()
    with pytest.raises(RuntimeError, match="invalid route JSON"):
        parse_route_text("not-json", context)


#______________________________________________________________________________
# DETERMINISTIC EXECUTION
#______________________________________________________________________________

def test_lookup_execution_returns_real_position_values():
    _, state, analytics, context = _engine_objects()
    route = validate_route({
        "intent": "lookup",
        "ticker": "BTC",
        "fields": ["quantity", "current_price"],
        "actions": [],
        "clarification": None,
    }, context)
    execution = execute_route(route, state=state, analytics=analytics, context=context)
    position = execution["result"]["portfolio"]["position"]
    assert execution["kind"] == "lookup"
    assert position["ticker"] == "BTC-USD"
    assert position["quantity"] == pytest.approx(-2.0)
    assert position["current_price"] == pytest.approx(80.0)


def test_compound_scenario_executes_sequentially_without_mutating_baseline():
    _, state, analytics, context = _engine_objects()
    baseline = deepcopy(state)
    route = validate_route({
        "intent": "scenario",
        "ticker": None,
        "fields": [],
        "actions": [
            {"action": "price_shock", "ticker": "BTC", "value": -25},
            {"action": "resize", "ticker": "BTC", "percent_change": -50},
        ],
        "clarification": None,
    }, context)
    execution = execute_route(route, state=state, analytics=analytics, context=context)
    scenario = execution["scenario"]
    btc_after = next(row for row in scenario["scenario_state"]["positions"] if row["ticker"] == "BTC-USD")
    assert execution["kind"] == "scenario"
    assert btc_after["current_price"] == pytest.approx(60.0)
    assert btc_after["quantity"] == pytest.approx(-1.0)
    assert state == baseline


def test_rate_shock_is_routed_to_existing_deterministic_engine():
    _, state, analytics, context = _engine_objects()
    route = validate_route({
        "intent": "scenario",
        "ticker": None,
        "fields": [],
        "actions": [{"action": "rate_shock", "value": 100, "tickers": ["GLD"]}],
        "clarification": None,
    }, context)
    execution = execute_route(route, state=state, analytics=analytics, context=context)
    detail = execution["scenario"]["actions"][0]
    assert detail["action"] == "rate_shock"
    assert detail["changed"][0]["ticker"] == "GLD"


def test_target_risk_route_requires_deterministic_scenario_action():
    _, state, analytics, context = _engine_objects()
    route = validate_route({
        "intent": "scenario",
        "ticker": None,
        "fields": [],
        "actions": [{
            "action": "target_risk",
            "target_annual_volatility": 0.01,
            "allow_leverage": False,
        }],
        "clarification": None,
    }, context)
    execution = execute_route(route, state=state, analytics=analytics, context=context)
    assert execution["kind"] == "scenario"
    assert execution["scenario"]["actions"][0]["action"] == "target_risk"
