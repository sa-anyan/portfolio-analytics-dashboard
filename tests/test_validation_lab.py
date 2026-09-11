from __future__ import annotations

from validation_lab import run_single_cell_repair_benchmark


def test_validation_lab_runs_ground_truth_cases(clean_transactions):
    # Use a small slice in unit tests for speed. The full 150-row benchmark is
    # run manually/CI as a validation job.
    details, summary = run_single_cell_repair_benchmark(clean_transactions.head(20).copy())
    assert summary["cases"] > 50
    assert summary["accuracy"] >= 0.90
    assert not details.empty
