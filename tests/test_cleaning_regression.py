from __future__ import annotations

import pandas as pd

from portfolio_analytics.quality.auto_clean import auto_clean_ledger
from tests.corruption_engine import (
    corrupt_action_aliases,
    corrupt_date_formats,
    corrupt_numeric_strings,
    corrupt_ticker_formats,
)


def _price_table_map(table: pd.DataFrame) -> dict[str, tuple[float, float]]:
    return {
        str(r["ticker"]): (float(r["net_shares"]), float(r["last_price_used"]))
        for _, r in table.iterrows()
    }


def test_format_noise_does_not_change_clean_price_table(clean_transactions):
    _, baseline_report = auto_clean_ledger(clean_transactions)
    baseline = _price_table_map(baseline_report["price_table"])

    corrupted = clean_transactions.copy()
    for corruption in (corrupt_ticker_formats, corrupt_numeric_strings, corrupt_action_aliases, corrupt_date_formats):
        corrupted, _ = corruption(corrupted)

    _, dirty_report = auto_clean_ledger(corrupted)
    dirty = _price_table_map(dirty_report["price_table"])

    assert dirty == baseline
    assert dirty_report["silently_dropped_rows"] == 0


def test_every_source_row_is_reconciled(clean_transactions):
    _, report = auto_clean_ledger(clean_transactions)
    assert len(report["audit_table"]) == len(clean_transactions)
    assert report["silently_dropped_rows"] == 0
