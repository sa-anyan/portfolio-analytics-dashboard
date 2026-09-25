import pandas as pd

from portfolio_analytics.input_engine.parser import parse_dataframe
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.analytics.engine import run_account_performance, run_analytics


def test_dated_holdings_become_accounting_events_and_cash_stays_cash():
    frame = pd.DataFrame([
        {"Ticker": "AAA", "Name": "Alpha", "Asset Class": "Equity", "Purchase Date": "2024-01-02", "Quantity": 2, "Avg Cost (local)": 100, "Current Price (local)": 120},
        {"Ticker": "BBB", "Name": "Beta", "Asset Class": "Equity", "Purchase Date": "2024-01-04", "Quantity": 1, "Avg Cost (local)": 50, "Current Price (local)": 60},
        {"Ticker": "CASH", "Name": "Cash (GBP)", "Asset Class": "Cash", "Purchase Date": None, "Quantity": 500, "Avg Cost (local)": 1, "Current Price (local)": 1},
    ])
    parsed = parse_dataframe(frame)
    assert parsed["variables"]["history_capability"]["available"] is True
    assert parsed["variables"]["history_capability"]["dated_positions"] == 2
    state = build_portfolio_state(parsed, latest_prices={"AAA": 120, "BBB": 60, "CASH": 73.5})
    assert len(state["accounting_history"]["trades"]) == 2
    assert state["accounting_history"]["trades"][0]["Price"] == 100
    assert state["cash"]["current"] == 500.0
    assert state["cash"]["explicit_snapshot_cash"] == 500.0
    assert all(row["ticker"] != "CASH" for row in state["positions"])


def test_account_performance_uses_purchase_dates_and_prices():
    frame = pd.DataFrame([
        {"Ticker": "AAA", "Asset Class": "Equity", "Purchase Date": "2024-01-02", "Quantity": 2, "Avg Cost (local)": 100, "Current Price (local)": 120},
        {"Ticker": "BBB", "Asset Class": "Equity", "Purchase Date": "2024-01-04", "Quantity": 1, "Avg Cost (local)": 50, "Current Price (local)": 60},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 120, "BBB": 60})
    idx = pd.date_range("2024-01-02", periods=8, freq="D")
    prices = pd.DataFrame({"AAA": [100, 101, 102, 103, 104, 105, 106, 107], "BBB": [48, 49, 50, 51, 52, 53, 54, 55]}, index=idx)
    result = run_account_performance(state, prices)
    assert result["available"] is True
    assert result["method"] == "reconstructed dated holdings"
    assert len(result["portfolio_path"]) >= 2


def test_static_history_ignores_columns_with_no_return_observations():
    frame = pd.DataFrame([
        {"Ticker": "AAA", "Quantity": 1, "Current Price": 100},
        {"Ticker": "BAD", "Quantity": 1, "Current Price": 100},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 100, "BAD": 100})
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    prices = pd.DataFrame({"AAA": [100, 101, 102, 103, 104], "BAD": [None]*5}, index=idx)
    analytics = run_analytics(state, prices)
    assert analytics["performance"]["observations"] > 0
    assert analytics["series"]["portfolio_path"]


def test_dated_holdings_external_funding_is_not_counted_as_return():
    """A later purchase adds capital, not investment performance."""
    import pandas as pd
    from portfolio_analytics.input_engine.parser import parse_dataframe
    from portfolio_analytics.core.portfolio_state import build_portfolio_state
    from portfolio_analytics.analytics.engine import run_account_performance

    frame = pd.DataFrame([
        {"Ticker": "AAA", "Quantity": 10, "Purchase Date": "2024-01-02", "Purchase Price": 100, "Current Price": 120},
        {"Ticker": "BBB", "Quantity": 10, "Purchase Date": "2024-01-03", "Purchase Price": 50, "Current Price": 50},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 120, "BBB": 50})
    prices = pd.DataFrame(
        {"AAA": [100.0, 100.0, 100.0], "BBB": [50.0, 50.0, 50.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    result = run_account_performance(state, prices)
    assert result["available"] is True
    path = pd.DataFrame(result["portfolio_path"])
    # The £500 inferred funding for BBB must not create a 50% return on 3 Jan.
    jan3 = path.loc[path["date"].str.startswith("2024-01-03"), "daily_return"].iloc[0]
    assert abs(float(jan3)) < 1e-12
    assert result["accounting_summary"]["external_contributions"] == 1500.0


def test_position_is_absent_before_its_purchase_date():
    """Dated holdings must not contribute to equity before acquisition."""
    import pandas as pd
    from portfolio_analytics.input_engine.parser import parse_dataframe
    from portfolio_analytics.core.portfolio_state import build_portfolio_state
    from portfolio_analytics.analytics.engine import run_account_performance

    frame = pd.DataFrame([
        {"Ticker": "AAA", "Quantity": 10, "Purchase Date": "2024-01-03", "Purchase Price": 100, "Current Price": 100},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 100})
    prices = pd.DataFrame(
        {"AAA": [90.0, 95.0, 100.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    result = run_account_performance(state, prices)
    assert result["available"] is False or result.get("accounting_summary", {}).get("history_start", "").startswith("2024-01-03")


def test_dated_accounting_converts_mixed_currencies_to_usd_without_counting_contribution_as_return():
    from portfolio_analytics.analytics.engine import run_account_performance
    state = {
        "accounting_history": {
            "available": True,
            "method": "reconstructed dated holdings",
            "trades": [
                {"Date": "2025-01-02", "Ticker": "US", "Signed Quantity": 1, "Price": 100, "Fees": 0, "Currency": "USD"},
                {"Date": "2025-01-03", "Ticker": "UK", "Signed Quantity": 1, "Price": 100, "Fees": 0, "Currency": "GBP"},
            ],
            "cashflows": [
                {"Date": "2025-01-02", "Type": "DEPOSIT", "Amount": 100, "Currency": "USD"},
                {"Date": "2025-01-03", "Type": "DEPOSIT", "Amount": 100, "Currency": "GBP"},
            ],
        },
        "cash": {"starting": 0.0},
    }
    prices = pd.DataFrame({"US": [100, 100, 110], "UK": [100, 100, 100]}, index=pd.to_datetime(["2025-01-02","2025-01-03","2025-01-06"]))
    fx = pd.DataFrame({"USD": [1,1,1], "GBP": [1.25,1.25,1.25]}, index=prices.index)
    result = run_account_performance(state, prices, fx_history=fx, base_currency="USD")
    assert result["available"] is True
    assert result["base_currency"] == "USD"
    path = pd.DataFrame(result["portfolio_path"])
    # Second purchase is an external contribution, not investment return.
    assert abs(float(path.iloc[1]["daily_return"])) < 1e-12
    assert round(float(path.iloc[-1]["equity"]), 2) == 235.00


def test_unresolved_fx_gap_is_not_silently_treated_as_one():
    from portfolio_analytics.analytics.engine import _ledger_equity_curve
    state = {
        "accounting_history": {"available": True, "trades": [{"Date":"2025-01-02","Ticker":"UK","Signed Quantity":1,"Price":100,"Fees":0,"Currency":"GBP"}], "cashflows": [{"Date":"2025-01-02","Type":"DEPOSIT","Amount":100,"Currency":"GBP"}]},
        "cash": {"starting": 0.0},
    }
    prices = pd.DataFrame({"UK":[100,101]}, index=pd.to_datetime(["2025-01-02","2025-01-03"]))
    curve = _ledger_equity_curve(state, prices, fx_history=pd.DataFrame({"USD":[1,1]}, index=prices.index), base_currency="USD")
    assert curve["equity"].isna().all()


def test_holdings_snapshot_uses_first_market_mark_not_purchase_price_for_entry_return():
    """Execution-vs-daily-close differences must not create an instant loss."""
    frame = pd.DataFrame([
        {"Ticker": "AAA", "Quantity": 10, "Purchase Date": "2024-01-02", "Purchase Price": 100, "Current Price": 90, "Currency": "USD"},
        {"Ticker": "BBB", "Quantity": 10, "Purchase Date": "2024-01-03", "Purchase Price": 200, "Current Price": 80, "Currency": "USD"},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 90, "BBB": 80})
    prices = pd.DataFrame(
        {"AAA": [70.0, 70.0, 77.0], "BBB": [80.0, 80.0, 80.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    result = run_account_performance(state, prices)
    path = pd.DataFrame(result["portfolio_path"])
    # BBB enters at its first market mark ($800). Its supplied $2,000 cost basis
    # must not manufacture a loss in the performance series.
    jan3 = float(path.loc[path["date"].str.startswith("2024-01-03"), "daily_return"].iloc[0])
    assert abs(jan3) < 1e-12
    # The reconstructed account value is the market value of positions actually
    # present on that date: 10*70 + 10*80.
    jan3_equity = float(path.loc[path["date"].str.startswith("2024-01-03"), "equity"].iloc[0])
    assert jan3_equity == 1500.0


def test_holdings_snapshot_value_steps_when_a_new_position_enters_but_growth_does_not_jump():
    frame = pd.DataFrame([
        {"Ticker": "AAA", "Quantity": 1, "Purchase Date": "2024-01-02", "Purchase Price": 100, "Current Price": 100, "Currency": "USD"},
        {"Ticker": "BBB", "Quantity": 1, "Purchase Date": "2024-01-04", "Purchase Price": 50, "Current Price": 50, "Currency": "USD"},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 100, "BBB": 50})
    prices = pd.DataFrame(
        {"AAA": [100, 100, 100, 100], "BBB": [50, 50, 50, 50]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]),
    )
    result = run_account_performance(state, prices)
    path = pd.DataFrame(result["portfolio_path"])
    assert list(path["equity"].astype(float)) == [100.0, 100.0, 150.0, 150.0]
    assert abs(float(path.iloc[2]["daily_return"])) < 1e-12
    assert abs(float(path.iloc[2]["cumulative_return"])) < 1e-12
