"""Independent numerical oracles for the audit failures and their accounting boundaries."""
import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.analytics.engine import (
    _series_metrics, run_analytics, run_account_performance, historical_attribution,
)
from portfolio_analytics.analytics.combination_risk import calculate_combination_risk
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe
from portfolio_analytics.scenarios.engine import run_scenario, apply_target_risk

DATES = pd.bdate_range("2025-01-02", periods=4)


def state(rows, cash=0, **kwargs):
    return build_portfolio_state(parse_dataframe(pd.DataFrame(rows), starting_cash=cash), **kwargs)


def test_drawdown_counts_loss_from_initial_capital_in_metrics_and_chart():
    assert _series_metrics(pd.Series([-.1, .05]))["max_drawdown"] == pytest.approx(-.1)
    s = state({"Ticker": ["AAA"], "Quantity": [1], "Current Price": [100]})
    result = run_analytics(s, pd.DataFrame({"AAA": [100, 90, 94.5]}, index=DATES[:3]))
    assert result["performance"]["max_drawdown"] == pytest.approx(-.1)
    assert result["series"]["portfolio_path"][0]["drawdown"] == pytest.approx(-.1)


def test_combination_drawdown_and_shared_ranking_window():
    dates = pd.bdate_range("2025-01-01", periods=40)
    returns = pd.DataFrame({"A": [-.1] + [0.] * 39, "B": [-.1] + [0.] * 39}, index=dates)
    result = calculate_combination_risk(returns, ["A", "B"])
    assert result["results"][0]["max_drawdown"] == pytest.approx(-.1)
    returns["C"] = 0.01
    returns.loc[dates[:5], "C"] = np.nan
    result = calculate_combination_risk(returns, ["A", "B", "C"])
    assert {row["observations"] for row in result["results"]} == {35}
    assert result["common_observations"] == 35


def test_duplicate_lots_accumulate_without_phantom_return():
    s = state({"Ticker": ["AAA", "AAA"], "Quantity": [1, 2], "Current Price": [100, 100],
               "Average Entry Price": [100, 100], "Purchase Date": [DATES[0], DATES[1]]})
    actual = run_account_performance(s, pd.DataFrame({"AAA": [100]*4}, index=DATES))
    path = pd.DataFrame(actual["portfolio_path"])
    assert list(path.equity) == [100, 300, 300, 300]
    assert path.daily_return.iloc[1] == pytest.approx(0)
    assert actual["accounting_summary"]["gain_after_external_flows"] == pytest.approx(0)


def test_starting_and_explicit_cash_are_in_snapshot_equity_and_not_gain():
    s = state({"Ticker": ["AAA", "CASH"], "Quantity": [1, 50], "Current Price": [100, 1],
               "Average Entry Price": [100, 1], "Purchase Date": [DATES[0], None]}, cash=100)
    actual = run_account_performance(s, pd.DataFrame({"AAA": [100]*4}, index=DATES))
    assert actual["accounting_summary"]["ending_equity"] == 250
    assert actual["accounting_summary"]["opening_capital"] == 150
    assert actual["accounting_summary"]["gain_after_external_flows"] == 0


def test_ledger_starting_capital_and_first_day_execution_pnl():
    s = state({"Date": [DATES[0]], "Type": ["BUY"], "Ticker": ["AAA"],
               "Quantity": [1], "Price": [100]}, cash=100, latest_prices={"AAA": 110})
    actual = run_account_performance(s, pd.DataFrame({"AAA": [110]*4}, index=DATES))
    assert actual["accounting_summary"]["gain_after_external_flows"] == 10
    assert actual["portfolio_path"][0]["daily_return"] == pytest.approx(.1)


def test_cash_weight_and_geometric_linking_make_attribution_reconcile():
    s = state({"Ticker": ["AAA", "CASH"], "Quantity": [1, 100], "Current Price": [81, 1],
               "Average Entry Price": [100, 1], "Purchase Date": [DATES[0], None]})
    actual = run_account_performance(s, pd.DataFrame({"AAA": [100, 90, 81, 100]}, index=DATES))
    result = historical_attribution(actual, end_date=str(DATES[2].date()), analysis_mode="period")
    assert result["period_return_pct"] == pytest.approx(-9.5)
    assert result["contributors"][0]["contribution_pct_points"] == pytest.approx(-9.5)
    assert result["cash_and_other_contribution_pct_points"] == pytest.approx(0)
    assert result["reconciliation_error_pct_points"] == pytest.approx(0)
    drawdown = historical_attribution(actual)
    assert drawdown["attribution_return_pct"] == pytest.approx(-9.5)
    assert drawdown["period_return_pct"] == pytest.approx(0)


def test_attribution_does_not_include_return_before_start_valuation():
    s = state({"Ticker": ["AAA"], "Quantity": [1], "Current Price": [81],
               "Average Entry Price": [100], "Purchase Date": [DATES[0]]})
    actual = run_account_performance(s, pd.DataFrame({"AAA": [100, 90, 81, 81]}, index=DATES))
    result = historical_attribution(actual, start_date=str(DATES[1].date()), analysis_mode="period")
    assert result["period_return_pct"] == pytest.approx(-10)
    assert result["contributors"][0]["contribution_pct_points"] == pytest.approx(-10)


def test_attribution_excludes_added_lot_capital_and_includes_existing_lot_return():
    s = state({"Ticker": ["AAA", "AAA"], "Quantity": [1, 2], "Current Price": [121, 121],
               "Average Entry Price": [100, 110], "Purchase Date": [DATES[0], DATES[1]]})
    actual = run_account_performance(s, pd.DataFrame({"AAA": [100, 110, 121, 121]}, index=DATES))
    result = historical_attribution(actual, analysis_mode="period")
    assert result["attribution_return_pct"] == pytest.approx(21)
    assert result["contributors"][0]["contribution_pct_points"] == pytest.approx(21)


def test_mixed_currency_valuation_pence_fx_returns_and_scenario_cash():
    fx = pd.DataFrame({"GBP": [1.25, 1.3, 1.35, 1.4]}, index=DATES)
    s = state({"Ticker": ["UK", "US", "CASH"], "Quantity": [100, 1, 100],
               "Currency": ["GBX", "USD", "GBP"], "Current Price": [200, 100, 1]}, fx_history=fx)
    assert s["totals"]["equity"] == pytest.approx(520)
    assert s["positions"][0]["current_price"] == pytest.approx(2.8)
    analytics = run_analytics(s, pd.DataFrame({"UK": [200]*4, "US": [100]*4}, index=DATES), fx_history=fx)
    assert analytics["meta"]["coverage"]["available"]
    assert analytics["engine_data"]["asset_returns"]["UK"][0]["return"] == pytest.approx(1.3/1.25 - 1)
    assert "CASH:GBP" in analytics["engine_data"]["current_signed_weights"]
    result = run_scenario(s, analytics, [{"action": "resize", "ticker": "UK", "target_quantity": 50}])
    assert result["scenario_state"]["totals"]["equity"] == pytest.approx(520)
    assert result["scenario_state"]["cash"]["balances"]["USD"] == pytest.approx(140)


def test_foreign_ledger_costs_and_retained_cash_use_event_and_valuation_fx():
    fx = pd.DataFrame({"GBP": [1.25, 1.3, 1.35, 1.4]}, index=DATES)
    s = state({"Date": [DATES[0], DATES[0]], "Type": ["DEPOSIT", "BUY"],
               "Ticker": [None, "UK"], "Quantity": [None, 1], "Price": [None, 100],
               "Amount": [200, None], "Currency": ["GBP", "GBP"]},
              fx_history=fx, latest_prices={"UK": 100})
    assert s["cash"]["current"] == pytest.approx(140)
    assert s["positions"][0]["average_entry_price"] == pytest.approx(125)
    assert s["totals"]["equity"] == pytest.approx(280)
    actual = run_account_performance(s, pd.DataFrame({"UK": [100]*4}, index=DATES), fx_history=fx)
    assert actual["accounting_summary"]["ending_equity"] == pytest.approx(280)
    assert actual["accounting_summary"]["gain_after_external_flows"] == pytest.approx(30)


def test_foreign_currency_valuation_fails_without_fx():
    with pytest.raises(ValueError, match="GBP/USD"):
        state({"Ticker": ["UK"], "Quantity": [1], "Currency": ["GBP"], "Current Price": [100]})


def test_missing_returns_block_portfolio_and_scenario_risk():
    s = state({"Ticker": ["A", "B"], "Quantity": [1, 1], "Current Price": [100, 100]})
    analytics = run_analytics(s, pd.DataFrame({"A": [100, 101, 99, 100]}, index=DATES))
    assert analytics["risk"]["var_value"] is None
    assert analytics["risk_contribution"] == []
    scenario = run_scenario(s, analytics, [{"action": "price_shock", "ticker": "A", "value": 5}])
    assert scenario["scenario_analytics"]["coverage"]["missing_tickers"] == ["B"]
    assert scenario["scenario_analytics"]["risk"]["annual_volatility"] is None


def test_closed_position_is_replayed_and_missing_history_cannot_be_skipped():
    s = state({"Date": [DATES[0], DATES[1]], "Type": ["BUY", "SELL"],
               "Ticker": ["OLD", "OLD"], "Quantity": [1, 1], "Price": [100, 110]}, cash=100)
    result = run_account_performance(s, pd.DataFrame({"OLD": [100, 110, 110, 110]}, index=DATES))
    assert result["accounting_summary"]["ending_equity"] == 110
    missing = run_account_performance(s, pd.DataFrame({"OTHER": [100]*4}, index=DATES))
    assert not missing["available"] and "OLD" in missing["reason"]


def test_partial_dated_snapshot_is_not_presented_as_full_account_performance():
    s = state({"Ticker": ["A", "B"], "Quantity": [1, 1], "Current Price": [100, 100],
               "Average Entry Price": [100, 100], "Purchase Date": [DATES[0], None]})
    result = run_account_performance(s, pd.DataFrame({"A": [100]*4, "B": [100]*4}, index=DATES))
    assert not result["available"] and "B" in result["reason"]


def test_adjusted_marks_are_rejected_and_supplied_dividend_is_counted_once():
    s = state({"Date": [DATES[0], DATES[1]], "Type": ["BUY", "DIVIDEND"],
               "Ticker": ["A", None], "Quantity": [1, None], "Price": [100, None],
               "Amount": [None, 5]}, cash=100, latest_prices={"A": 95})
    prices = pd.DataFrame({"A": [100, 95, 95, 95]}, index=DATES)
    result = run_account_performance(s, prices)
    assert result["accounting_summary"]["ending_equity"] == 100
    assert result["portfolio_path"][1]["daily_return"] == pytest.approx(0)
    prices.attrs["price_basis"] = "total_return"
    result = run_account_performance(s, prices)
    assert not result["available"] and "total-return" in result["reason"]


def test_split_changes_quantity_and_basis_without_manufacturing_pnl():
    splits = pd.DataFrame({"A": [2.]}, index=DATES[1:2])
    s = state({"Date": [DATES[0]], "Type": ["BUY"], "Ticker": ["A"],
               "Quantity": [1], "Price": [100]}, cash=100, latest_prices={"A": 50}, split_history=splits)
    assert s["positions"][0]["quantity"] == 2
    assert s["positions"][0]["average_entry_price"] == 50
    prices = pd.DataFrame({"A": [100, 50, 50, 50]}, index=DATES)
    prices.attrs.update(price_basis="accounting", splits=splits)
    result = run_account_performance(s, prices)
    assert [row["equity"] for row in result["portfolio_path"]] == [100]*4
    assert result["metrics"]["max_drawdown"] == 0


def test_calendar_and_effective_risk_free_rate_conventions():
    dates = pd.date_range("2025-01-02", periods=7)
    s = state({"Ticker": ["CRYPTO"], "Quantity": [1], "Current Price": [100]})
    analytics = run_analytics(s, pd.DataFrame({"CRYPTO": [100, 110, 120, 130, 140, 150, 160]}, index=dates))
    assert analytics["risk"]["observations"] == 4
    r = pd.Series([.01, -.02, .03, -.01])
    m = _series_metrics(r, risk_free_rate=.05)
    expected = (r.mean() - ((1.05)**(1/252)-1)) / r.std(ddof=1) * np.sqrt(252)
    assert m["sharpe"] == pytest.approx(expected)
    for level in (0, 1, -1, np.nan):
        with pytest.raises(ValueError):
            _series_metrics(r, var_level=level)


def test_non_positive_equity_withholds_investor_risk():
    s = state({"Ticker": ["A"], "Quantity": [-1], "Current Price": [100]})
    result = run_analytics(s, pd.DataFrame({"A": [100, 101, 99, 100]}, index=DATES))
    assert result["risk"]["annual_volatility"] is None
    assert "Positive" in result["meta"]["coverage"]["reason"]


def test_unknown_basis_and_mixed_sign_snapshot_are_not_fabricated():
    s = state({"Ticker": ["A", "A"], "Quantity": [1, 1], "Current Price": [100, 100],
               "Average Entry Price": [80, None]})
    assert s["totals"]["unrealised_pnl"] is None
    with pytest.raises(ValueError, match="Mixed long/short"):
        state({"Ticker": ["A", "A"], "Quantity": [2, -1], "Current Price": [100, 100]})


def test_target_risk_can_increase_unlevered_exposure_using_existing_cash():
    s = state({"Ticker": ["A"], "Quantity": [1], "Current Price": [100]}, cash=900)
    analytics = run_analytics(s, pd.DataFrame({"A": [100, 110, 99, 105]}, index=DATES))
    target = analytics["risk"]["annual_volatility"] * 2
    result, _ = apply_target_risk(s, analytics, target)
    assert result["cash"]["current"] == pytest.approx(800)
    assert result["totals"]["equity"] == pytest.approx(1000)


def test_market_adapter_separates_total_returns_from_split_neutral_accounting(monkeypatch):
    from portfolio_analytics.core import market_data
    class Yahoo:
        @staticmethod
        def download(**kwargs):
            assert kwargs["actions"]
            # Yahoo's split-adjusted Close and dividend-adjusted Adj Close differ.
            return pd.DataFrame({"Close": [50, 50, 55, 55], "Adj Close": [45, 50, 55, 55],
                                 "Stock Splits": [0, 2, 0, 0]}, index=DATES)
    monkeypatch.setattr(market_data, "_import_yfinance", lambda: Yahoo)
    risk, _ = market_data.fetch_price_history(["A"])
    marks, _ = market_data.fetch_price_history(["A"], price_basis="accounting")
    snapshot, _ = market_data.fetch_price_history(["A"], price_basis="split_adjusted_close")
    assert risk.A.tolist() == [45, 50, 55, 55]
    assert marks.A.tolist() == [100, 50, 55, 55]
    assert snapshot.A.tolist() == [50, 50, 55, 55]
    assert marks.attrs["splits"].A.tolist() == [2]


def test_fx_alignment_uses_prior_observation_but_never_future_data():
    from portfolio_analytics.core.fx import aligned_fx, fx_rate
    fx = pd.DataFrame({"GBP": [1.25, 1.4]}, index=[DATES[0], DATES[2]])
    assert fx_rate("GBP", date=DATES[1], history=fx) == 1.25
    before = aligned_fx(fx, pd.DatetimeIndex([DATES[0] - pd.Timedelta(days=1)]))
    assert before.GBP.isna().all()
    assert fx_rate("GBX", date=DATES[1], history=fx) == .0125


def test_foreign_cash_flow_currency_is_required_even_if_all_securities_are_usd():
    s = {"accounting_history": {"available": True, "method": "actual transaction ledger",
         "trades": [{"Date": DATES[0], "Ticker": "A", "Signed Quantity": 1, "Price": 100, "Currency": "USD"}],
         "cashflows": [{"Date": DATES[1], "Type": "DEPOSIT", "Amount": 100, "Currency": "EUR"}]},
         "cash": {"starting": 100}}
    result = run_account_performance(s, pd.DataFrame({"A": [100]*4}, index=DATES))
    assert not result["available"] and "EUR" in result["reason"]


def test_invalid_input_rows_are_not_silently_dropped():
    with pytest.raises(ValueError, match="invalid rows"):
        state({"Ticker": ["A", "B"], "Quantity": [1, None], "Current Price": [100, 100]})
    with pytest.raises(ValueError, match="Unsupported ledger event"):
        state({"Date": [DATES[0], DATES[1]], "Type": ["BUY", "TRANSFER"],
               "Ticker": ["A", "A"], "Quantity": [1, 1], "Price": [100, 100]}, latest_prices={"A": 100})


def test_scenario_chaining_preserves_base_currency_returns():
    s = state({"Ticker": ["A"], "Quantity": [1], "Current Price": [100]}, cash=100)
    analytics = run_analytics(s, pd.DataFrame({"A": [100, 110, 99, 105]}, index=DATES))
    first = run_scenario(s, analytics, [{"action": "resize", "ticker": "A", "target_quantity": 2}])
    second = run_scenario(first["scenario_state"], first["scenario_analytics"], [{"action": "resize", "ticker": "A", "target_quantity": 1}])
    assert second["scenario_analytics"]["risk"]["annual_volatility"] == pytest.approx(analytics["risk"]["annual_volatility"])


def test_future_trade_is_not_valued_using_a_stale_historical_close():
    s = {"accounting_history": {"available": True, "method": "actual transaction ledger",
         "trades": [{"Date": DATES[0], "Ticker": "A", "Signed Quantity": 1, "Price": 100},
                    {"Date": DATES[-1] + pd.offsets.BDay(1), "Ticker": "A", "Signed Quantity": 1, "Price": 100}],
         "cashflows": []}, "cash": {"starting": 300}}
    result = run_account_performance(s, pd.DataFrame({"A": [100]*4}, index=DATES))
    assert not result["available"] and "after" in result["reason"]
