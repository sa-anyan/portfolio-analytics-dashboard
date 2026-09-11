from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.quality.repair_engine import diagnose_repairs, apply_repairs


def _suggestion_for(table: pd.DataFrame, source_row: int, field: str) -> pd.Series:
    match = table[(table["Source Row"] == source_row) & (table["Field"] == field)]
    assert len(match) == 1, match
    return match.iloc[0]


def test_reconstruct_quantity_price_and_gross(clean_transactions):
    df = clean_transactions.copy()
    # Source Row = dataframe index + 2
    qty_idx, price_idx, gross_idx = 10, 20, 30
    expected_qty = float(df.at[qty_idx, "Quantity"])
    expected_price = float(df.at[price_idx, "Price"])
    expected_gross_base = abs(float(df.at[gross_idx, "Quantity"]) * float(df.at[gross_idx, "Price"]))

    df.at[qty_idx, "Quantity"] = np.nan
    df.at[price_idx, "Price"] = np.nan
    df.at[gross_idx, "Gross_Value"] = np.nan

    suggestions, _ = diagnose_repairs(df)

    q = _suggestion_for(suggestions, qty_idx + 2, "Quantity")
    p = _suggestion_for(suggestions, price_idx + 2, "Price")
    g = _suggestion_for(suggestions, gross_idx + 2, "Gross Value")

    assert q["Repair Type"] == "RECONSTRUCTED"
    assert float(q["Proposed Value"]) == pytest.approx(expected_qty, rel=2e-3)
    assert float(p["Proposed Value"]) == pytest.approx(expected_price, rel=2e-3)
    assert float(g["Proposed Value"]) == pytest.approx(expected_gross_base, rel=1e-9)


def test_contextual_ticker_asset_and_currency_inference(clean_transactions):
    df = clean_transactions.copy()
    ticker_idx, asset_idx, currency_idx = 1, 4, 8
    expected_ticker = str(df.at[ticker_idx, "Ticker"])
    expected_asset = str(df.at[asset_idx, "Asset"])
    expected_currency = str(df.at[currency_idx, "Currency"])

    df.at[ticker_idx, "Ticker"] = np.nan
    df.at[asset_idx, "Asset"] = np.nan
    df.at[currency_idx, "Currency"] = np.nan

    suggestions, diagnostics = diagnose_repairs(df)

    t = _suggestion_for(suggestions, ticker_idx + 2, "Ticker")
    a = _suggestion_for(suggestions, asset_idx + 2, "Asset")
    c = _suggestion_for(suggestions, currency_idx + 2, "Currency")

    assert str(t["Proposed Value"]) == expected_ticker
    assert str(a["Proposed Value"]) == expected_asset
    assert str(c["Proposed Value"]) == expected_currency
    assert diagnostics["dominant_currency"] == "USD"


def test_date_action_and_transaction_id_are_not_fabricated(clean_transactions):
    df = clean_transactions.copy()
    df.at[2, "Date"] = np.nan
    df.at[3, "Type"] = np.nan
    df.at[4, "Transaction_ID"] = np.nan

    suggestions, diagnostics = diagnose_repairs(df)
    fields = set(suggestions["Field"].tolist()) if not suggestions.empty else set()
    assert "Date" not in fields
    assert "Action" not in fields
    assert "Transaction ID" not in fields

    questions = diagnostics["manual_questions"]
    assert set(questions["Field"]) >= {"Date", "Action"}
    warnings = diagnostics.get("audit_warnings")
    assert warnings is not None and "Transaction ID" in set(warnings["Field"])


def test_apply_repairs_only_changes_explicitly_accepted_rows(clean_transactions):
    df = clean_transactions.copy()
    idx = 10
    df.at[idx, "Quantity"] = np.nan
    suggestions, _ = diagnose_repairs(df)
    target_index = suggestions.index[(suggestions["Source Row"] == idx + 2) & (suggestions["Field"] == "Quantity")][0]

    unchanged, audit = apply_repairs(df, suggestions, accepted_indices=[])
    assert pd.isna(unchanged.at[idx, "Quantity"])
    assert audit.empty

    repaired, audit = apply_repairs(df, suggestions, accepted_indices=[target_index])
    assert pd.notna(repaired.at[idx, "Quantity"])
    assert len(audit) == 1
