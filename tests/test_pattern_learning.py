from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pattern_learning import learn_portfolio_patterns
from repair_engine import diagnose_repairs


def test_perfect_portfolio_learns_core_relationships(clean_transactions):
    profile = learn_portfolio_patterns(clean_transactions, profile_name="perfect")

    gross = profile["patterns"]["gross_identity"]
    fee = profile["patterns"]["fee_model"]
    currency = profile["patterns"]["dominant_currency"]

    assert gross["support"] == len(clean_transactions)
    assert gross["within_0_1pct_share"] >= 0.99
    assert fee["support"] == len(clean_transactions)
    assert fee["rate"] == pytest.approx(0.0008, rel=5e-3)
    assert fee["minimum"] == pytest.approx(0.99, abs=0.01)
    assert fee["within_0_05_share"] >= 0.95
    assert currency["value"] == "USD"
    assert currency["share"] == pytest.approx(1.0)
    assert len(profile["mappings"]["asset_to_ticker"]) >= 5


def test_reference_profile_repairs_values_removed_from_copy(clean_transactions):
    profile = learn_portfolio_patterns(clean_transactions, profile_name="perfect")
    damaged = clean_transactions.copy()

    # Remove values whose answers are recoverable from deterministic or learned evidence.
    expected_ticker = damaged.at[1, "Ticker"]
    expected_qty = float(damaged.at[10, "Quantity"])
    expected_fee = float(damaged.at[25, "Fees"])
    expected_currency = damaged.at[30, "Currency"]

    damaged.at[1, "Ticker"] = np.nan
    damaged.at[10, "Quantity"] = np.nan
    damaged.at[25, "Fees"] = np.nan
    damaged.at[30, "Currency"] = np.nan

    suggestions, diagnostics = diagnose_repairs(damaged, reference_profile=profile)

    def suggestion(row, field):
        out = suggestions[(suggestions["Source Row"] == row + 2) & (suggestions["Field"] == field)]
        assert len(out) == 1
        return out.iloc[0]

    assert suggestion(1, "Ticker")["Proposed Value"] == expected_ticker
    assert float(suggestion(10, "Quantity")["Proposed Value"]) == pytest.approx(expected_qty, rel=2e-3)
    assert float(suggestion(25, "Fees")["Proposed Value"]) == pytest.approx(expected_fee, abs=0.05)
    assert suggestion(30, "Currency")["Proposed Value"] == expected_currency
    assert diagnostics["reference_profile_used"] is True


def test_reference_profile_still_refuses_to_invent_nonrecoverable_fields(clean_transactions):
    profile = learn_portfolio_patterns(clean_transactions, profile_name="perfect")
    damaged = clean_transactions.copy()
    damaged.at[2, "Date"] = np.nan
    damaged.at[3, "Type"] = np.nan
    damaged.at[4, "Transaction_ID"] = np.nan

    suggestions, diagnostics = diagnose_repairs(damaged, reference_profile=profile)
    fields = set(suggestions["Field"].tolist()) if not suggestions.empty else set()
    assert not {"Date", "Action", "Transaction ID"}.intersection(fields)
    questions = diagnostics["manual_questions"]
    assert set(questions["Field"]) >= {"Date", "Action"}
    warnings = diagnostics.get("audit_warnings")
    assert warnings is not None and "Transaction ID" in set(warnings["Field"])
