from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pattern_learning import learn_portfolio_patterns, save_pattern_profile
from repair_engine import apply_repairs, diagnose_repairs
from input_parser import standardise_ticker


RECOVERABLE_FIELDS = (
    "Ticker",
    "Asset",
    "Quantity",
    "Price",
    "Gross_Value",
    "Fees",
    "Currency",
)
MANUAL_FIELDS = ("Date", "Type", "Transaction_ID")
ALL_FIELDS = RECOVERABLE_FIELDS + MANUAL_FIELDS

DISPLAY_FIELD = {
    "Ticker": "Ticker",
    "Asset": "Asset",
    "Quantity": "Quantity",
    "Price": "Price",
    "Gross_Value": "Gross Value",
    "Fees": "Fees",
    "Currency": "Currency",
    "Date": "Date",
    "Type": "Action",
    "Transaction_ID": "Transaction ID",
}


@dataclass(frozen=True)
class Scenario:
    scenario_id: int
    row_index: int
    missing_fields: tuple[str, ...]


@dataclass
class DecisionResult:
    scenario_id: int
    row_index: int
    source_row: int
    field: str
    original_value: Any
    final_value: Any
    expected_action: str
    actual_action: str
    repair_type: str | None
    confidence: float | None
    passed: bool
    result: str
    missing_count: int


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return str(value).strip().lower() in {"", "nan", "none", "<na>", "-", "n/a", "na", "?"}


def _value_matches(field: str, expected: Any, actual: Any) -> bool:
    if _missing(actual):
        return False
    if field == "Ticker":
        return standardise_ticker(actual) == standardise_ticker(expected)
    if field in {"Asset", "Currency", "Type", "Transaction_ID", "Date"}:
        return str(actual).strip().upper() == str(expected).strip().upper()
    try:
        exp = float(expected)
        act = float(actual)
    except Exception:
        return str(actual) == str(expected)

    if field == "Fees":
        return abs(act - exp) <= 0.05
    if field == "Gross_Value":
        return abs(act - exp) <= max(0.50, abs(exp) * 0.001)
    return abs(act - exp) <= max(1e-6, abs(exp) * 0.002)


def _field_is_present(row: pd.Series, field: str) -> bool:
    return field in row.index and not _missing(row[field])


def expected_recoverability(row: pd.Series, missing_fields: tuple[str, ...], profile: dict[str, Any]) -> dict[str, str]:
    """Oracle for what *should* be safely recoverable after iterative repair.

    It mirrors financial identities and learned mappings, not the repair engine's
    implementation. That separation lets the benchmark catch real regressions.
    """
    known = {field: _field_is_present(row, field) and field not in missing_fields for field in ALL_FIELDS}

    mappings = profile.get("mappings", {})
    patterns = profile.get("patterns", {})
    has_asset_to_ticker = bool(mappings.get("asset_to_ticker"))
    has_ticker_to_asset = bool(mappings.get("ticker_to_asset"))
    has_fee_model = bool(patterns.get("fee_model"))
    currency = patterns.get("dominant_currency") or {}
    has_currency = float(currency.get("share", 0.0)) >= 0.95

    changed = True
    while changed:
        changed = False
        if not known.get("Ticker", False) and known.get("Asset", False) and has_asset_to_ticker:
            known["Ticker"] = True; changed = True
        if not known.get("Asset", False) and known.get("Ticker", False) and has_ticker_to_asset:
            known["Asset"] = True; changed = True

        q, p, g = known.get("Quantity", False), known.get("Price", False), known.get("Gross_Value", False)
        if not q and p and g:
            known["Quantity"] = True; changed = True
        if not p and q and g:
            known["Price"] = True; changed = True
        if not g and q and p:
            known["Gross_Value"] = True; changed = True

        if not known.get("Fees", False) and has_fee_model and (
            known.get("Gross_Value", False)
            or (known.get("Quantity", False) and known.get("Price", False))
        ):
            known["Fees"] = True; changed = True
        if not known.get("Currency", False) and has_currency:
            known["Currency"] = True; changed = True

    expected: dict[str, str] = {}
    for field in missing_fields:
        if field in MANUAL_FIELDS:
            expected[field] = "ESCALATE"
        else:
            expected[field] = "REPAIR" if known.get(field, False) else "ESCALATE"
    return expected


def generate_scenarios(clean_df: pd.DataFrame, count: int = 20_000, seed: int = 20260909, max_missing: int = 5) -> list[Scenario]:
    """Generate unique deterministic mixed-missing scenarios.

    A scenario removes 1..max_missing fields from one known-good transaction.
    With 150 rows and 10 candidate fields there are ample unique combinations
    for 20,000 scenarios without fabricating new ground-truth values.
    """
    if clean_df is None or clean_df.empty:
        return []
    available = [f for f in ALL_FIELDS if f in clean_df.columns]
    if not available:
        return []

    max_missing = max(1, min(max_missing, len(available)))
    rng = np.random.default_rng(seed)
    seen: set[tuple[int, tuple[str, ...]]] = set()
    scenarios: list[Scenario] = []

    # Force broad coverage first: every available field on every row.
    for idx in clean_df.index:
        for field in available:
            if len(scenarios) >= count:
                break
            sig = (int(idx), (field,))
            if sig in seen:
                continue
            seen.add(sig)
            scenarios.append(Scenario(len(scenarios) + 1, int(idx), (field,)))
        if len(scenarios) >= count:
            break

    attempts = 0
    max_attempts = max(count * 100, 100_000)
    while len(scenarios) < count and attempts < max_attempts:
        attempts += 1
        idx = int(rng.choice(clean_df.index.to_numpy()))
        # Bias toward 2-4 simultaneous missing cells while still testing 1 and 5.
        sizes = np.arange(1, max_missing + 1)
        base = np.array([0.08, 0.26, 0.32, 0.24, 0.10], dtype=float)[:max_missing]
        probs = base / base.sum()
        size = int(rng.choice(sizes, p=probs))
        chosen = tuple(sorted(rng.choice(available, size=size, replace=False).tolist()))
        sig = (idx, chosen)
        if sig in seen:
            continue
        seen.add(sig)
        scenarios.append(Scenario(len(scenarios) + 1, idx, chosen))

    if len(scenarios) < count:
        raise RuntimeError(f"Could only generate {len(scenarios):,} unique scenarios; requested {count:,}.")
    return scenarios


def _run_iterative_repair(single_row: pd.DataFrame, profile: dict[str, Any], max_passes: int = 5) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], set[str]]:
    current = single_row.copy().reset_index(drop=True)
    repaired_meta: dict[str, dict[str, Any]] = {}
    escalated: set[str] = set()

    for _ in range(max_passes):
        suggestions, diagnostics = diagnose_repairs(
            current,
            reference_profile=profile,
            learn_from_current=False,
        )
        manual = diagnostics.get("manual_questions", pd.DataFrame())
        if isinstance(manual, pd.DataFrame) and not manual.empty:
            for field in manual["Field"].astype(str):
                escalated.add(field)

        if suggestions is None or suggestions.empty:
            break

        # This is a lab: accept every production suggestion so wrong suggestions
        # are exposed rather than hidden behind UI defaults.
        accepted = list(suggestions.index)
        for sidx, s in suggestions.iterrows():
            source_col = str(s["Source Column"])
            repaired_meta[source_col] = {
                "repair_type": str(s["Repair Type"]),
                "confidence": float(s["Confidence"]),
            }
        updated, _ = apply_repairs(current, suggestions, accepted_indices=accepted)
        if updated.equals(current):
            break
        current = updated

    return current, repaired_meta, escalated


def evaluate_scenario(clean_df: pd.DataFrame, scenario: Scenario, profile: dict[str, Any]) -> list[DecisionResult]:
    truth = clean_df.loc[scenario.row_index].copy()
    one = clean_df.loc[[scenario.row_index]].copy().reset_index(drop=True)
    for field in scenario.missing_fields:
        one.at[0, field] = np.nan

    expected = expected_recoverability(truth, scenario.missing_fields, profile)
    repaired, meta, escalated_labels = _run_iterative_repair(one, profile)

    results: list[DecisionResult] = []
    for field in scenario.missing_fields:
        expected_action = expected[field]
        final_value = repaired.at[0, field]
        display = DISPLAY_FIELD[field]
        repaired_value_present = not _missing(final_value)
        was_escalated = display in escalated_labels

        if repaired_value_present:
            actual_action = "REPAIR"
        elif was_escalated or not repaired_value_present:
            actual_action = "ESCALATE"
        else:
            actual_action = "NONE"

        if expected_action == "REPAIR":
            if actual_action == "REPAIR" and _value_matches(field, truth[field], final_value):
                passed, result = True, "CORRECT_REPAIR"
            elif actual_action == "REPAIR":
                passed, result = False, "WRONG_REPAIR"
            else:
                passed, result = False, "MISSED_REPAIR"
        else:
            if actual_action == "ESCALATE":
                passed, result = True, "CORRECT_ESCALATION"
            else:
                passed, result = False, "UNSAFE_GUESS"

        m = meta.get(field, {})
        results.append(DecisionResult(
            scenario_id=scenario.scenario_id,
            row_index=scenario.row_index,
            source_row=scenario.row_index + 2,
            field=display,
            original_value=truth[field],
            final_value=final_value,
            expected_action=expected_action,
            actual_action=actual_action,
            repair_type=m.get("repair_type"),
            confidence=m.get("confidence"),
            passed=passed,
            result=result,
            missing_count=len(scenario.missing_fields),
        ))
    return results


def _run_batch_iterative_repair(
    damaged: pd.DataFrame,
    profile: dict[str, Any],
    *,
    max_passes: int = 5,
) -> tuple[pd.DataFrame, dict[tuple[int, str], dict[str, Any]], set[tuple[int, str]]]:
    """Run the production repair engine across all scenarios in vector-sized batches.

    Each row in ``damaged`` is one independent synthetic scenario. This is much
    faster than invoking the repair engine 20,000 times while still exercising
    the same production diagnosis/apply functions.
    """
    current = damaged.copy().reset_index(drop=True)
    repair_meta: dict[tuple[int, str], dict[str, Any]] = {}
    escalated: set[tuple[int, str]] = set()

    for _ in range(max_passes):
        suggestions, diagnostics = diagnose_repairs(
            current,
            reference_profile=profile,
            learn_from_current=False,
        )

        manual = diagnostics.get("manual_questions", pd.DataFrame())
        if isinstance(manual, pd.DataFrame) and not manual.empty:
            for _, q in manual.iterrows():
                scenario_pos = int(q["Source Row"]) - 2
                escalated.add((scenario_pos, str(q["Field"])))

        if suggestions is None or suggestions.empty:
            break

        for _, suggestion in suggestions.iterrows():
            scenario_pos = int(suggestion["Source Row"]) - 2
            source_col = str(suggestion["Source Column"])
            repair_meta[(scenario_pos, source_col)] = {
                "repair_type": str(suggestion["Repair Type"]),
                "confidence": float(suggestion["Confidence"]),
            }

        updated, _ = apply_repairs(
            current,
            suggestions,
            accepted_indices=list(suggestions.index),
        )
        if updated.equals(current):
            break
        current = updated

    return current, repair_meta, escalated


def run_automated_validation(
    clean_df: pd.DataFrame,
    *,
    scenarios: int = 20_000,
    seed: int = 20260909,
    max_missing: int = 5,
    progress_every: int = 0,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Learn once, corrupt 20k scenarios, run production repair, and self-score."""
    profile = learn_portfolio_patterns(clean_df, profile_name="automated_validation_ground_truth")
    scenario_list = generate_scenarios(clean_df, count=scenarios, seed=seed, max_missing=max_missing)

    # Build one dataframe whose rows are independent damaged scenarios.
    damaged_rows: list[pd.Series] = []
    expected_by_scenario: list[dict[str, str]] = []
    truth_rows: list[pd.Series] = []
    for scenario in scenario_list:
        truth = clean_df.loc[scenario.row_index].copy()
        damaged = truth.copy()
        for field in scenario.missing_fields:
            damaged[field] = np.nan
        damaged_rows.append(damaged)
        truth_rows.append(truth)
        expected_by_scenario.append(expected_recoverability(truth, scenario.missing_fields, profile))

    damaged_frame = pd.DataFrame(damaged_rows).reset_index(drop=True)
    repaired_frame, repair_meta, escalated = _run_batch_iterative_repair(damaged_frame, profile)

    decisions: list[DecisionResult] = []
    for scenario_pos, scenario in enumerate(scenario_list):
        truth = truth_rows[scenario_pos]
        expected = expected_by_scenario[scenario_pos]
        for field in scenario.missing_fields:
            expected_action = expected[field]
            final_value = repaired_frame.at[scenario_pos, field]
            display = DISPLAY_FIELD[field]
            repaired_value_present = not _missing(final_value)
            was_escalated = (scenario_pos, display) in escalated
            actual_action = "REPAIR" if repaired_value_present else "ESCALATE"

            if expected_action == "REPAIR":
                if actual_action == "REPAIR" and _value_matches(field, truth[field], final_value):
                    passed, result = True, "CORRECT_REPAIR"
                elif actual_action == "REPAIR":
                    passed, result = False, "WRONG_REPAIR"
                else:
                    passed, result = False, "MISSED_REPAIR"
            else:
                if actual_action == "ESCALATE":
                    passed, result = True, "CORRECT_ESCALATION"
                else:
                    passed, result = False, "UNSAFE_GUESS"

            m = repair_meta.get((scenario_pos, field), {})
            decisions.append(DecisionResult(
                scenario_id=scenario.scenario_id,
                row_index=scenario.row_index,
                source_row=scenario.row_index + 2,
                field=display,
                original_value=truth[field],
                final_value=final_value,
                expected_action=expected_action,
                actual_action=actual_action,
                repair_type=m.get("repair_type"),
                confidence=m.get("confidence"),
                passed=passed,
                result=result,
                missing_count=len(scenario.missing_fields),
            ))

    details = pd.DataFrame([asdict(r) for r in decisions])
    if details.empty:
        return details, {"scenarios": 0, "decisions": 0, "accuracy": 0.0}, profile

    by_field = (
        details.groupby("field", dropna=False)
        .agg(decisions=("passed", "size"), passed=("passed", "sum"))
        .reset_index()
    )
    by_field["accuracy"] = by_field["passed"] / by_field["decisions"]

    by_complexity = (
        details.groupby("missing_count", dropna=False)
        .agg(decisions=("passed", "size"), passed=("passed", "sum"))
        .reset_index()
    )
    by_complexity["accuracy"] = by_complexity["passed"] / by_complexity["decisions"]

    result_counts = details["result"].value_counts().to_dict()
    summary = {
        "scenarios": int(scenarios),
        "decisions": int(len(details)),
        "passed": int(details["passed"].sum()),
        "failed": int((~details["passed"]).sum()),
        "accuracy": float(details["passed"].mean()),
        "correct_repairs": int(result_counts.get("CORRECT_REPAIR", 0)),
        "correct_escalations": int(result_counts.get("CORRECT_ESCALATION", 0)),
        "wrong_repairs": int(result_counts.get("WRONG_REPAIR", 0)),
        "missed_repairs": int(result_counts.get("MISSED_REPAIR", 0)),
        "unsafe_guesses": int(result_counts.get("UNSAFE_GUESS", 0)),
        "seed": int(seed),
        "max_missing": int(max_missing),
        "by_field": by_field,
        "by_complexity": by_complexity,
    }
    return details, summary, profile

def _json_safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    for key in ("by_field", "by_complexity"):
        value = out.get(key)
        if isinstance(value, pd.DataFrame):
            out[key] = value.to_dict(orient="records")
    return out


def save_validation_outputs(details: pd.DataFrame, summary: dict[str, Any], profile: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    details_path = out / "validation_decisions.csv"
    failures_path = out / "validation_failures.csv"
    summary_path = out / "validation_summary.json"
    profile_path = out / "learned_pattern_profile.json"
    field_path = out / "accuracy_by_field.csv"
    complexity_path = out / "accuracy_by_complexity.csv"

    details.to_csv(details_path, index=False)
    details.loc[~details["passed"]].to_csv(failures_path, index=False)
    summary_path.write_text(json.dumps(_json_safe_summary(summary), indent=2), encoding="utf-8")
    save_pattern_profile(profile, profile_path)
    if isinstance(summary.get("by_field"), pd.DataFrame):
        summary["by_field"].to_csv(field_path, index=False)
    if isinstance(summary.get("by_complexity"), pd.DataFrame):
        summary["by_complexity"].to_csv(complexity_path, index=False)

    return {
        "decisions": details_path,
        "failures": failures_path,
        "summary": summary_path,
        "profile": profile_path,
        "by_field": field_path,
        "by_complexity": complexity_path,
    }


def _load_ground_truth(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError("Ground truth must be CSV or Excel.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated synthetic portfolio cleaning validation.")
    parser.add_argument("--ground-truth", default="tests/fixtures/clean_transactions.csv")
    parser.add_argument("--scenarios", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--max-missing", type=int, default=5)
    parser.add_argument("--output", default="validation_outputs")
    parser.add_argument("--progress-every", type=int, default=1000)
    args = parser.parse_args()

    clean = _load_ground_truth(args.ground_truth)
    details, summary, profile = run_automated_validation(
        clean,
        scenarios=args.scenarios,
        seed=args.seed,
        max_missing=args.max_missing,
        progress_every=args.progress_every,
    )
    paths = save_validation_outputs(details, summary, profile, args.output)

    print("\nAUTOMATED VALIDATION COMPLETE")
    print(f"Scenarios:          {summary['scenarios']:,}")
    print(f"Field decisions:    {summary['decisions']:,}")
    print(f"Decision accuracy:  {summary['accuracy']:.2%}")
    print(f"Correct repairs:    {summary['correct_repairs']:,}")
    print(f"Correct escalations:{summary['correct_escalations']:,}")
    print(f"Wrong repairs:      {summary['wrong_repairs']:,}")
    print(f"Missed repairs:     {summary['missed_repairs']:,}")
    print(f"Unsafe guesses:     {summary['unsafe_guesses']:,}")
    print(f"Outputs:            {Path(args.output).resolve()}")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
