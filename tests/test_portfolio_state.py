from __future__ import annotations

import pandas as pd

from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


def test_holdings_state_values_long_and_short():
    frame = pd.DataFrame({
        "Ticker": ["AAA", "BBB"],
        "Quantity": [10, -5],
        "Average Entry Price": [8, 12],
        "Current Price": [10, 10],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    state = build_portfolio_state(parsed)
    totals = state["totals"]
    assert totals["long_exposure"] == 100
    assert totals["short_exposure"] == 50
    assert totals["gross_exposure"] == 150
    assert totals["net_exposure"] == 50
    assert totals["equity"] == 1050
    # Long gains 20, short gains 10 when price is below entry.
    assert totals["unrealised_pnl"] == 30


def test_ledger_short_accounting_cash_and_realised_pnl():
    frame = pd.DataFrame({
        "Date": ["2026-01-01", "2026-01-02"],
        "Type": ["SELL", "BUY"],
        "Ticker": ["AAA", "AAA"],
        "Quantity": [10, 4],
        "Price": [100, 80],
    })
    parsed = parse_dataframe(frame, starting_cash=0)
    state = build_portfolio_state(parsed, latest_prices={"AAA": 70})
    pos = state["positions"][0]
    assert pos["quantity"] == -6
    assert pos["average_entry_price"] == 100
    assert pos["realised_pnl"] == 80
    assert pos["unrealised_pnl"] == 180
    # Sell 10 @100 gives +1000 cash; cover 4 @80 costs 320.
    assert state["cash"]["current"] == 680
    assert state["totals"]["equity"] == 260


def test_closed_ledger_position_keeps_realised_pnl_in_portfolio_totals():
    frame = pd.DataFrame({
        "Date": ["2026-01-01", "2026-01-02"],
        "Type": ["BUY", "SELL"],
        "Ticker": ["AAA", "AAA"],
        "Quantity": [10, 10],
        "Price": [100, 120],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    state = build_portfolio_state(parsed, latest_prices={})
    assert state["positions"] == []
    assert state["totals"]["realised_pnl"] == 200
    assert state["cash"]["current"] == 1200
    assert state["totals"]["equity"] == 1200


def test_cash_is_reserved_and_cannot_be_overridden_by_market_price():
    frame = pd.DataFrame({
        "Ticker": ["CASH", "AAA"],
        "Asset Name": ["Cash (GBP)", "Asset A"],
        "Asset Class": ["Cash", "Equity"],
        "Quantity": [18500, 10],
        "Current Price": [1.0, 100.0],
    })
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"CASH": 73.5, "AAA": 110.0})
    rows = {row["ticker"]: row for row in state["positions"]}

    assert "CASH" not in rows
    assert state["cash"]["current"] == 18500.0
    assert state["cash"]["explicit_snapshot_cash"] == 18500.0
    assert state["totals"]["gross_exposure"] == 1100.0
    assert state["totals"]["equity"] == 19600.0
    assert rows["AAA"]["current_price"] == 110.0


def test_holdings_explicit_cash_is_cash_not_market_exposure():
    import pandas as pd
    from portfolio_analytics.input_engine.parser import parse_dataframe
    from portfolio_analytics.core.portfolio_state import build_portfolio_state

    frame = pd.DataFrame([
        {"Ticker": "AAPL", "Quantity": 2, "Current Price": 100.0, "Asset Class": "Equity"},
        {"Ticker": "CASH", "Quantity": 18500, "Current Price": 1.0, "Asset Class": "Cash", "Currency": "GBP"},
    ])
    parsed = parse_dataframe(frame)
    state = build_portfolio_state(parsed, latest_prices={"AAPL": 100.0, "CASH": 73.5})

    assert state["cash"]["current"] == 18500.0
    assert state["cash"]["explicit_snapshot_cash"] == 18500.0
    assert state["totals"]["cash"] == 18500.0
    assert state["totals"]["gross_exposure"] == 200.0
    assert state["totals"]["net_exposure"] == 200.0
    assert state["totals"]["equity"] == 18700.0
    assert all(row["ticker"] != "CASH" for row in state["positions"])
