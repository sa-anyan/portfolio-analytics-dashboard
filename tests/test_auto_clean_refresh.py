from __future__ import annotations

import sys
import types

import pandas as pd

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))

from portfolio_analytics.quality.auto_clean import (
    auto_clean_ledger,
    combine_cash_events,
    extract_cash_events,
)
from portfolio_analytics.ingestion.input_parser import (
    clean_transaction_ledger,
    flag_potential_duplicates,
    standardise_ledger_for_accounting,
)
from portfolio_analytics.accounting.today_engine import build_current_account


def test_auto_clean_updates_positions_and_cash() -> None:
    raw = pd.DataFrame([
        {
            "Date": "2026-01-02",
            "Type": "buy",
            "Ticker": "HD",
            "Quantity": "100",
            "Price": "10",
            "Gross Value": None,
        },
        {
            "Date": "2026-01-03",
            "Type": "sell",
            "Ticker": "HD.US",
            "Quantity": "20",
            "Price": "12",
            "Gross Value": None,
        },
        {
            "Date": "2026-01-04",
            "Type": "DIV",
            "Ticker": "HD-US",
            "Quantity": None,
            "Price": None,
            "Gross Value": "$50",
        },
        {
            "Date": "2026-01-05",
            "Type": "Transfer",
            "Ticker": None,
            "Quantity": None,
            "Price": None,
            "Gross Value": "100",
        },
    ])

    cleaned, _ = auto_clean_ledger(raw)
    cash_events = extract_cash_events(raw)

    flagged = flag_potential_duplicates(cleaned)
    trades, issues = clean_transaction_ledger(flagged)

    assert issues.empty
    assert list(trades["Ticker"]) == ["HD", "HD"]

    accounting_trades = standardise_ledger_for_accounting(trades)

    positions, _, summary = build_current_account(
        accounting_trades,
        cash_events,
        starting_free_cash=0.0,
    )

    assert len(positions) == 1
    assert positions.iloc[0]["Ticker"] == "HD"
    assert positions.iloc[0]["Quantity"] == 80.0

    # -1000 BUY + 240 SELL + 50 dividend = -710 cash.
    assert summary["Cash Balance"] == -710.0

    # Generic TRANSFER is intentionally not guessed into accounting cash.
    assert set(cash_events["Type"]) == {"DIVIDEND"}


def test_cash_event_combination_deduplicates_exact_rows() -> None:
    a = pd.DataFrame([
        {"Date": pd.Timestamp("2026-01-01"), "Type": "DEPOSIT", "Amount": 500.0},
    ])
    b = pd.DataFrame([
        {"Date": pd.Timestamp("2026-01-01"), "Type": "DEPOSIT", "Amount": 500.0},
        {"Date": pd.Timestamp("2026-01-02"), "Type": "DIVIDEND", "Amount": 25.0},
    ])

    combined = combine_cash_events(a, b)

    assert combined is not None
    assert len(combined) == 2
    assert combined["Amount"].sum() == 525.0


if __name__ == "__main__":
    test_auto_clean_updates_positions_and_cash()
    test_cash_event_combination_deduplicates_exact_rows()
    print("Auto-Clean refresh regression tests passed.")
