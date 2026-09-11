from __future__ import annotations

import sys
import types

import pandas as pd

# Keep this smoke test network-free even in environments without yfinance.
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))

from input_parser import extract_frozen_prices
from today_engine import build_current_account, value_current_positions, build_historical_account_equity
from valuation_engine import (
    FROZEN_SNAPSHOT,
    frozen_snapshot_as_of,
    get_valuation_prices,
)


def assert_close(actual, expected, tolerance=1e-9):
    assert abs(actual - expected) <= tolerance, (actual, expected)



def test_historical_account_equity_handles_long_short_and_external_flows():
    transactions = pd.DataFrame([
        {"Date": "2024-01-02", "Type": "BUY", "Ticker": "ABC", "Quantity": 10, "Price": 100, "Fees": 0},
        {"Date": "2024-01-03", "Type": "SELL", "Ticker": "ABC", "Quantity": 15, "Price": 110, "Fees": 0},
    ])
    cashflows = pd.DataFrame([
        {"Date": "2024-01-02", "Type": "DEPOSIT", "Amount": 2000},
        {"Date": "2024-01-04", "Type": "WITHDRAWAL", "Amount": 100},
    ])
    prices = pd.DataFrame(
        {"ABC": [100.0, 110.0, 90.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )

    curve, missing = build_historical_account_equity(
        transactions,
        cashflows,
        prices,
        starting_free_cash=0.0,
    )

    assert missing == []
    assert_close(curve.loc[pd.Timestamp("2024-01-02"), "Equity"], 2000.0)
    # After selling 15 while holding 10, the account is short 5 shares.
    assert_close(curve.loc[pd.Timestamp("2024-01-03"), "Equity"], 2100.0)
    # Price falls from 110 to 90: the short gains 100. Withdrawal is external
    # and therefore does not count as investment performance.
    assert_close(curve.loc[pd.Timestamp("2024-01-04"), "Equity"], 2100.0)
    assert curve.loc[pd.Timestamp("2024-01-04"), "Daily Return"] > 0


def run_tests():
    transactions = pd.DataFrame([
        {"Date": "2024-01-05", "Type": "BUY", "Ticker": "ABC", "Quantity": 10, "Price": 100, "Fees": 0},
        {"Date": "2024-02-01", "Type": "SELL", "Ticker": "ABC", "Quantity": 15, "Price": 120, "Fees": 0},
        {"Date": "2024-03-01", "Type": "BUY", "Ticker": "ABC", "Quantity": 2, "Price": 90, "Fees": 0},
    ])

    cashflows = pd.DataFrame([
        {"Date": "2024-01-01", "Type": "DEPOSIT", "Amount": 10000},
    ])

    positions, ledger, summary = build_current_account(
        transactions,
        cashflows,
    )

    assert len(ledger) == 4
    assert_close(summary["Cash Balance"], 10620.0)
    assert_close(positions.loc[0, "Quantity"], -3.0)
    assert_close(positions.loc[0, "Average Entry Price"], 120.0)
    assert_close(positions.loc[0, "Realised P&L"], 260.0)

    historical_positions, _, historical_summary = build_current_account(
        transactions,
        cashflows,
        as_of_date="2024-01-15 23:59:59",
    )

    assert_close(historical_summary["Cash Balance"], 9000.0)
    assert_close(historical_positions.loc[0, "Quantity"], 10.0)
    assert_close(historical_positions.loc[0, "Average Entry Price"], 100.0)

    wide_prices = pd.DataFrame({
        "Date": ["2024-01-31", "2024-02-29"],
        "ABC": [85.0, 80.0],
        "BTC": [138755.0, 143378.0],
    })

    frozen = extract_frozen_prices(
        wide_prices,
        "Synthetic_Prices",
    )

    assert set(frozen["Ticker"]) == {"ABC", "BTC-USD"}
    assert_close(
        float(frozen.loc[frozen["Ticker"] == "ABC", "Price"].iloc[0]),
        80.0,
    )
    assert frozen_snapshot_as_of(frozen) == pd.Timestamp("2024-02-29")

    prices, missing, metadata = get_valuation_prices(
        ["ABC"],
        FROZEN_SNAPSHOT,
        frozen_prices=frozen,
    )

    assert missing == []
    assert metadata["Strict"] is True
    assert_close(prices["ABC"], 80.0)

    valued, exposure = value_current_positions(
        positions,
        prices,
    )

    assert_close(valued.loc[0, "Signed Market Value"], -240.0)
    assert_close(valued.loc[0, "Unrealised P&L"], 120.0)
    assert_close(exposure["Short Exposure"], 240.0)
    assert_close(summary["Cash Balance"] + valued["Signed Market Value"].sum(), 10380.0)

    _, missing, metadata = get_valuation_prices(
        ["ABC", "MISSING"],
        FROZEN_SNAPSHOT,
        frozen_prices=frozen,
    )

    assert missing == ["MISSING"]
    assert metadata["Strict"] is True

    test_historical_account_equity_handles_long_short_and_external_flows()
    print("All valuation/accounting tests passed.")


if __name__ == "__main__":
    run_tests()
