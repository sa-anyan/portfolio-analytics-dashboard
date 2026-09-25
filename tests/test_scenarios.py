from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe
from portfolio_analytics.scenarios.engine import (
    apply_price_shock,
    apply_rate_shock,
    apply_resize,
    run_scenario,
)


def _setup():
    frame = pd.DataFrame({
        "Ticker": ["BTC", "BOND", "GLD"],
        "Quantity": [-2.0, 10.0, 5.0],
        "Average Entry Price": [100.0, 100.0, 100.0],
        "Current Price": [80.0, 100.0, 100.0],
        "Duration": [None, 5.0, None],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    state = build_portfolio_state(parsed)

    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2025-01-01", periods=320)
    history = pd.DataFrame({
        "BTC-USD": 80 * np.cumprod(1 + rng.normal(0.0005, 0.04, len(dates))),
        "BOND": 100 * np.cumprod(1 + rng.normal(0.0001, 0.005, len(dates))),
        "GLD": 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, len(dates))),
    }, index=dates)
    analytics = run_analytics(state, history)
    return state, analytics


def test_price_fall_benefits_short_equity():
    state, _ = _setup()
    before = state["totals"]["equity"]
    shocked, _ = apply_price_shock(state, "BTC-USD", -50)
    assert shocked["totals"]["equity"] > before
    assert state["positions"][0]["current_price"] == 80  # baseline untouched


def test_halving_short_position_changes_cash_correctly():
    state, _ = _setup()
    before_cash = state["cash"]["current"]
    resized, _ = apply_resize(state, "BTC-USD", percent_change=-50)
    btc = next(row for row in resized["positions"] if row["ticker"] == "BTC-USD")
    assert btc["quantity"] == -1
    # Covering one short BTC at 80 costs 80 cash.
    assert resized["cash"]["current"] == pytest.approx(before_cash - 80)


def test_rate_shock_uses_duration_only_where_available():
    state, _ = _setup()
    shocked, log = apply_rate_shock(state, 100)
    bond = next(row for row in shocked["positions"] if row["ticker"] == "BOND")
    assert bond["current_price"] == pytest.approx(95.0)
    assert "BTC-USD" in log["skipped_no_rate_model"]


def test_multiple_actions_feed_each_other_sequentially():
    state, analytics = _setup()
    result = run_scenario(state, analytics, [
        {"action": "price_shock", "ticker": "BTC-USD", "value": -25},
        {"action": "resize", "ticker": "BTC-USD", "percent_change": -50},
        {"action": "rate_shock", "value": 100},
    ])
    btc = next(row for row in result["scenario_state"]["positions"] if row["ticker"] == "BTC-USD")
    assert btc["current_price"] == pytest.approx(60.0)
    assert btc["quantity"] == pytest.approx(-1.0)
    assert len(result["actions"]) == 3
    assert result["scenario_analytics"]["risk"]["annual_volatility"] is not None


def test_target_risk_can_scale_down_without_leverage():
    state, analytics = _setup()
    current_vol = analytics["risk"]["annual_volatility"]
    target = current_vol / 2
    result = run_scenario(state, analytics, [
        {"action": "target_risk", "target_annual_volatility": target, "allow_leverage": False},
    ])
    after = result["scenario_analytics"]["risk"]["annual_volatility"]
    assert after == pytest.approx(target, rel=1e-6)


def test_long_to_long_reallocation_preserves_equity_at_same_marks():
    state, analytics = _setup()
    before_equity = state["totals"]["equity"]
    result = run_scenario(state, analytics, [
        {
            "action": "reallocation",
            "from_ticker": "GLD",
            "to_ticker": "BOND",
            "amount": 200,
            "percent_of_source_exposure": None,
        }
    ])
    assert result["scenario_state"]["totals"]["equity"] == pytest.approx(before_equity)


def test_scenario_rejects_unknown_action_before_execution():
    state, analytics = _setup()
    with pytest.raises(ValueError):
        run_scenario(state, analytics, [{"action": "invent_portfolio_math"}])


def test_resize_long_partial_close_updates_realised_pnl_and_preserves_equity():
    frame = pd.DataFrame({
        "Ticker": ["NVDA"],
        "Quantity": [10.0],
        "Average Entry Price": [100.0],
        "Current Price": [120.0],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    state = build_portfolio_state(parsed)
    before_equity = state["totals"]["equity"]

    resized, detail = apply_resize(state, "NVDA", target_quantity=5.0)
    row = resized["positions"][0]

    assert resized["totals"]["equity"] == pytest.approx(before_equity)
    assert resized["cash"]["current"] == pytest.approx(1600.0)
    assert row["quantity"] == pytest.approx(5.0)
    assert row["average_entry_price"] == pytest.approx(100.0)
    assert row["realised_pnl"] == pytest.approx(100.0)
    assert row["unrealised_pnl"] == pytest.approx(100.0)
    assert resized["totals"]["realised_pnl"] == pytest.approx(100.0)
    assert detail["realised_pnl_change"] == pytest.approx(100.0)


def test_resize_short_partial_cover_updates_realised_pnl_and_preserves_equity():
    frame = pd.DataFrame({
        "Ticker": ["BTC"],
        "Quantity": [-2.0],
        "Average Entry Price": [100.0],
        "Current Price": [80.0],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    state = build_portfolio_state(parsed)
    before_equity = state["totals"]["equity"]

    resized, detail = apply_resize(state, "BTC-USD", target_quantity=-1.0)
    row = resized["positions"][0]

    assert resized["totals"]["equity"] == pytest.approx(before_equity)
    assert resized["cash"]["current"] == pytest.approx(920.0)
    assert row["quantity"] == pytest.approx(-1.0)
    assert row["average_entry_price"] == pytest.approx(100.0)
    assert row["realised_pnl"] == pytest.approx(20.0)
    assert row["unrealised_pnl"] == pytest.approx(20.0)
    assert resized["totals"]["realised_pnl"] == pytest.approx(20.0)
    assert detail["realised_pnl_change"] == pytest.approx(20.0)


def test_resize_increase_updates_weighted_average_entry_price():
    frame = pd.DataFrame({
        "Ticker": ["NVDA"],
        "Quantity": [10.0],
        "Average Entry Price": [100.0],
        "Current Price": [120.0],
    })
    parsed = parse_dataframe(frame, starting_cash=5000)
    state = build_portfolio_state(parsed)

    resized, _ = apply_resize(state, "NVDA", target_quantity=15.0)
    row = resized["positions"][0]

    expected_entry = (10 * 100 + 5 * 120) / 15
    assert row["average_entry_price"] == pytest.approx(expected_entry)
    assert resized["totals"]["equity"] == pytest.approx(state["totals"]["equity"])


def test_resize_reversal_realises_old_side_and_resets_basis_to_scenario_mark():
    frame = pd.DataFrame({
        "Ticker": ["NVDA"],
        "Quantity": [10.0],
        "Average Entry Price": [100.0],
        "Current Price": [120.0],
    })
    parsed = parse_dataframe(frame, starting_cash=5000)
    state = build_portfolio_state(parsed)

    resized, detail = apply_resize(state, "NVDA", target_quantity=-5.0)
    row = resized["positions"][0]

    assert row["quantity"] == pytest.approx(-5.0)
    assert row["average_entry_price"] == pytest.approx(120.0)
    assert row["realised_pnl"] == pytest.approx(200.0)
    assert row["unrealised_pnl"] == pytest.approx(0.0)
    assert detail["signed_trade_quantity"] == pytest.approx(-15.0)


def test_compound_order_uses_prior_action_price_for_resize_trade():
    state, analytics = _setup()
    result = run_scenario(state, analytics, [
        {"action": "price_shock", "ticker": "BTC-USD", "value": -25},
        {"action": "resize", "ticker": "BTC-USD", "percent_change": -50},
    ])
    resize_log = result["actions"][1]
    assert resize_log["trade_price"] == pytest.approx(60.0)
    assert resize_log["cash_change"] == pytest.approx(-60.0)


def test_position_comparison_is_available_for_copilot():
    state, analytics = _setup()
    result = run_scenario(state, analytics, [
        {"action": "price_shock", "ticker": "BTC-USD", "value": 10},
    ])
    positions = result["comparison"]["positions"]
    btc = next(row for row in positions if row["ticker"] == "BTC-USD")
    assert btc["price_before"] == pytest.approx(80.0)
    assert btc["price_after"] == pytest.approx(88.0)
    assert btc["price_change_pct"] == pytest.approx(10.0)


def test_rate_shock_reports_requested_ticker_not_in_portfolio():
    state, _ = _setup()
    _, log = apply_rate_shock(state, 100, tickers=["BOND", "MISSING"])
    assert log["requested_not_found"] == ["MISSING"]


def test_invalid_price_shock_is_rejected_before_execution():
    state, analytics = _setup()
    with pytest.raises(ValueError):
        run_scenario(state, analytics, [
            {"action": "price_shock", "ticker": "BTC-USD", "value": -100},
        ])


def test_invalid_resize_percent_cannot_accidentally_reverse_position():
    state, analytics = _setup()
    with pytest.raises(ValueError):
        run_scenario(state, analytics, [
            {"action": "resize", "ticker": "BTC-USD", "percent_change": -150},
        ])
