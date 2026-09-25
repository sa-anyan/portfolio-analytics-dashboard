from __future__ import annotations

import pandas as pd

from portfolio_analytics.input_engine.parser import parse_dataframe
from portfolio_analytics.input_engine.manual import parse_manual_holdings


def test_holdings_detect_and_normalise():
    frame = pd.DataFrame({
        "Symbol": ["BTC", "NVDA"],
        "Units": [-2, 10],
        "Avg Entry Price": [100000, 100],
        "Current Price": [90000, 120],
    })
    parsed = parse_dataframe(frame, starting_cash=1000)
    assert parsed["classification"] == "holdings"
    assert parsed["normalised_dataset"]["holdings"][0]["Ticker"] == "BTC-USD"
    assert parsed["normalised_dataset"]["holdings"][0]["Quantity"] == -2
    assert parsed["variables"]["provided_prices"]["NVDA"] == 120


def test_ledger_detects_signed_trade_quantities():
    frame = pd.DataFrame({
        "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "Type": ["SELL", "BUY", "DEPOSIT"],
        "Ticker": ["BTC", "BTC", ""],
        "Quantity": [2, 0.5, None],
        "Price": [100, 80, None],
        "Amount": [None, None, 500],
    })
    parsed = parse_dataframe(frame)
    assert parsed["classification"] == "ledger"
    trades = parsed["normalised_dataset"]["ledger"]
    assert trades[0]["Signed Quantity"] == -2
    assert trades[1]["Signed Quantity"] == 0.5
    assert parsed["normalised_dataset"]["cashflows"][0]["Amount"] == 500


def test_manual_entry_uses_same_holdings_parser():
    parsed = parse_manual_holdings([
        {"Ticker": "ETH", "Quantity": 3, "Current Price": 2500},
    ])
    assert parsed["source"]["kind"] == "manual"
    assert parsed["classification"] == "holdings"
    assert parsed["normalised_dataset"]["holdings"][0]["Ticker"] == "ETH-USD"
