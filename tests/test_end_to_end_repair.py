from __future__ import annotations

import numpy as np
import pandas as pd

from auto_clean import auto_clean_ledger
from repair_engine import diagnose_repairs, apply_repairs


def _map(table: pd.DataFrame):
    return table.set_index("ticker")[["net_shares", "last_price_used"]].sort_index()


def test_repair_before_clean_restores_recoverable_missing_values(clean_transactions):
    baseline_df = clean_transactions.copy()
    _, baseline_report = auto_clean_ledger(baseline_df)

    corrupted = baseline_df.copy()
    # Pick rows where the identities are available.
    corrupted.at[10, "Quantity"] = np.nan
    corrupted.at[20, "Price"] = np.nan
    corrupted.at[30, "Gross_Value"] = np.nan
    corrupted.at[1, "Ticker"] = np.nan

    suggestions, _ = diagnose_repairs(corrupted)
    high_conf = suggestions.index[
        suggestions["Repair Type"].isin(["RECONSTRUCTED", "INFERRED"])
        & suggestions["Confidence"].ge(0.98)
    ].tolist()
    repaired, _ = apply_repairs(corrupted, suggestions, accepted_indices=high_conf)
    _, repaired_report = auto_clean_ledger(repaired)

    pd.testing.assert_frame_equal(
        _map(repaired_report["price_table"]),
        _map(baseline_report["price_table"]),
        check_exact=False,
        rtol=2e-3,
        atol=1e-6,
    )
