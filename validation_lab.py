from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from pattern_learning import learn_portfolio_patterns
from repair_engine import diagnose_repairs
from input_parser import standardise_ticker


@dataclass
class ValidationCase:
    row_index: int
    source_row: int
    field: str
    original_value: Any
    proposed_value: Any
    repair_type: str | None
    confidence: float | None
    passed: bool
    result: str


def _is_close(field: str, original: Any, proposed: Any) -> bool:
    if proposed is None:
        return False
    if field == "Ticker":
        return standardise_ticker(proposed) == standardise_ticker(original)
    if field in {"Asset", "Currency"}:
        return str(proposed).strip().upper() == str(original).strip().upper()
    try:
        o = float(original)
        p = float(proposed)
    except Exception:
        return str(proposed) == str(original)

    if field == "Fees":
        return abs(p - o) <= 0.05
    if field == "Gross Value":
        return abs(p - o) <= max(0.50, abs(o) * 0.001)
    return abs(p - o) <= max(1e-6, abs(o) * 0.002)


def run_single_cell_repair_benchmark(clean_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Corrupt one recoverable cell at a time across the whole perfect ledger.

    The known-good dataframe is the answer key. The same learned reference profile
    is used for every damaged copy, which makes the test reproducible and auditable.
    """
    profile = learn_portfolio_patterns(clean_df, profile_name="validation_ground_truth")
    field_map = {
        "Ticker": "Ticker",
        "Asset": "Asset",
        "Quantity": "Quantity",
        "Price": "Price",
        "Gross_Value": "Gross Value",
        "Fees": "Fees",
        "Currency": "Currency",
    }

    cases: list[ValidationCase] = []
    for column, repair_field in field_map.items():
        if column not in clean_df.columns:
            continue
        for idx in clean_df.index:
            original = clean_df.at[idx, column]
            if pd.isna(original):
                continue

            damaged = clean_df.copy()
            damaged.at[idx, column] = np.nan
            suggestions, _ = diagnose_repairs(damaged, reference_profile=profile, learn_from_current=False)
            match = suggestions[
                (suggestions["Source Row"] == idx + 2)
                & (suggestions["Field"] == repair_field)
            ] if not suggestions.empty else pd.DataFrame()

            if match.empty:
                cases.append(ValidationCase(
                    row_index=int(idx), source_row=int(idx + 2), field=repair_field,
                    original_value=original, proposed_value=None, repair_type=None,
                    confidence=None, passed=False, result="NO_SUGGESTION",
                ))
                continue

            suggestion = match.iloc[0]
            proposed = suggestion["Proposed Value"]
            passed = _is_close(repair_field, original, proposed)
            cases.append(ValidationCase(
                row_index=int(idx), source_row=int(idx + 2), field=repair_field,
                original_value=original, proposed_value=proposed,
                repair_type=str(suggestion["Repair Type"]),
                confidence=float(suggestion["Confidence"]),
                passed=bool(passed), result="PASS" if passed else "WRONG_REPAIR",
            ))

    details = pd.DataFrame([asdict(c) for c in cases])
    if details.empty:
        return details, {"cases": 0, "passed": 0, "accuracy": 0.0}

    by_field = (
        details.groupby("field", dropna=False)
        .agg(cases=("passed", "size"), passed=("passed", "sum"))
        .reset_index()
    )
    by_field["accuracy"] = by_field["passed"] / by_field["cases"]

    summary = {
        "cases": int(len(details)),
        "passed": int(details["passed"].sum()),
        "failed": int((~details["passed"]).sum()),
        "accuracy": float(details["passed"].mean()),
        "by_field": by_field,
        "profile": profile,
    }
    return details, summary
