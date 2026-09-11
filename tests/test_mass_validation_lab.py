from __future__ import annotations

from mass_validation_lab import DEFAULT_LEVELS, run_mass_validation


def test_mass_validation_supports_catastrophic_missing_cell_scenarios(clean_transactions):
    summary, evidence, artefacts = run_mass_validation(
        clean_transactions,
        scenarios=len(DEFAULT_LEVELS),
        batch_size=3,
        progress_every=0,
    )
    assert summary["scenarios"] == len(DEFAULT_LEVELS)
    assert summary["max_errors_in_one_scenario"] >= 1000
    assert summary["holdout_rows"] > 0
    assert summary["held_out_decisions_scored"] > 0
    assert "methods" in artefacts["policy"]


def test_policy_contains_method_level_validation_not_training_values(clean_transactions):
    _, _, artefacts = run_mass_validation(
        clean_transactions,
        scenarios=20,
        batch_size=4,
        progress_every=0,
    )
    policy = artefacts["policy"]
    text = str(policy)
    assert "validated_accuracy" in text
    assert "fee_model" not in text
    assert "dominant_currency" not in text
    assert "asset_to_ticker" not in text
