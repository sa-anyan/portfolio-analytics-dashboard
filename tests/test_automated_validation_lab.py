from __future__ import annotations

from automated_validation_lab import (
    expected_recoverability,
    generate_scenarios,
    run_automated_validation,
)
from pattern_learning import learn_portfolio_patterns


def test_scenarios_are_reproducible_and_unique(clean_transactions):
    a = generate_scenarios(clean_transactions, count=500, seed=123, max_missing=5)
    b = generate_scenarios(clean_transactions, count=500, seed=123, max_missing=5)
    assert a == b
    signatures = {(s.row_index, s.missing_fields) for s in a}
    assert len(signatures) == 500


def test_recoverability_oracle_handles_dependency_cycles(clean_transactions):
    profile = learn_portfolio_patterns(clean_transactions)
    row = clean_transactions.iloc[0]

    expected = expected_recoverability(row, ("Quantity",), profile)
    assert expected["Quantity"] == "REPAIR"

    expected = expected_recoverability(row, ("Quantity", "Gross_Value"), profile)
    assert expected["Quantity"] == "ESCALATE"
    assert expected["Gross_Value"] == "ESCALATE"

    expected = expected_recoverability(row, ("Date", "Type", "Transaction_ID"), profile)
    assert set(expected.values()) == {"ESCALATE"}


def test_automated_lab_runs_mixed_scenarios(clean_transactions):
    details, summary, _ = run_automated_validation(
        clean_transactions,
        scenarios=300,
        seed=77,
        max_missing=5,
    )
    assert summary["scenarios"] == 300
    assert summary["decisions"] >= 300
    assert summary["unsafe_guesses"] == 0
    assert summary["accuracy"] >= 0.90
    assert not details.empty
