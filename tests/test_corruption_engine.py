from __future__ import annotations

import pandas as pd

from tests.corruption_engine import generate_corrupted_dataset, rename_headers


def test_corruption_engine_is_reproducible(clean_transactions):
    a, log_a = generate_corrupted_dataset(clean_transactions, seed=123)
    b, log_b = generate_corrupted_dataset(clean_transactions, seed=123)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(log_a, log_b)
    assert len(log_a) > 0


def test_different_seed_changes_random_corruptions(clean_transactions):
    _, log_a = generate_corrupted_dataset(clean_transactions, seed=123)
    _, log_b = generate_corrupted_dataset(clean_transactions, seed=456)
    assert not log_a.equals(log_b)


def test_header_corruption_is_explicit(clean_transactions):
    corrupted, mapping = rename_headers(clean_transactions)
    assert mapping["Ticker"] == "tickerest_2"
    assert "tickerest_2" in corrupted.columns
    assert "Ticker" not in corrupted.columns
