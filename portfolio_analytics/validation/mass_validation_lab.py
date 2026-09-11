from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from portfolio_analytics.validation.automated_validation_lab import (
    ALL_FIELDS,
    DISPLAY_FIELD,
    _missing,
    _value_matches,
    expected_recoverability,
    _load_ground_truth,
)
from portfolio_analytics.quality.pattern_learning import learn_portfolio_patterns, save_pattern_profile
from portfolio_analytics.quality.repair_engine import diagnose_repairs, apply_repairs


DEFAULT_LEVELS = (1, 2, 3, 5, 10, 25, 50, 100, 250, 500, 750, 1000, 1200)


def _split_train_holdout(df: pd.DataFrame, holdout_share: float, seed: int) -> tuple[pd.DataFrame, set[int]]:
    """Deterministically reserve rows that the pattern learner never sees."""
    if not 0 < holdout_share < 1:
        raise ValueError("holdout_share must be between 0 and 1")
    rng = np.random.default_rng(seed)
    indices = np.asarray(df.index, dtype=int)
    n_holdout = max(1, int(round(len(indices) * holdout_share)))
    holdout = set(int(x) for x in rng.choice(indices, size=n_holdout, replace=False))
    train = df.loc[[i for i in indices if int(i) not in holdout]].copy()
    return train, holdout


def _eligible_cells(df: pd.DataFrame) -> list[tuple[int, str]]:
    available = [field for field in ALL_FIELDS if field in df.columns]
    cells: list[tuple[int, str]] = []
    for row_index, row in df.iterrows():
        for field in available:
            if not _missing(row[field]):
                cells.append((int(row_index), field))
    return cells


def _scenario_error_count(scenario_id: int, levels: tuple[int, ...], max_cells: int) -> int:
    # Deterministic round-robin guarantees every severity is exercised evenly.
    requested = levels[(scenario_id - 1) % len(levels)]
    return min(int(requested), max_cells)


def _scenario_cells(
    eligible: list[tuple[int, str]],
    holdout_cells: list[tuple[int, str]],
    *,
    scenario_id: int,
    errors: int,
    base_seed: int,
) -> list[tuple[int, str]]:
    """Choose deterministic missing cells, always including a held-out decision."""
    seed = np.random.SeedSequence([base_seed, scenario_id])
    rng = np.random.default_rng(seed)
    if errors >= len(eligible):
        return list(eligible)

    chosen_idx = rng.choice(len(eligible), size=errors, replace=False)
    chosen = [eligible[int(i)] for i in chosen_idx]

    # Ensure every scenario contributes at least one genuinely unseen scored cell.
    if holdout_cells and not any(cell in set(holdout_cells) for cell in chosen):
        replacement = holdout_cells[int(rng.integers(0, len(holdout_cells)))]
        chosen[-1] = replacement
    return chosen


def _repair_frame(frame: pd.DataFrame, profile: dict[str, Any], max_passes: int = 5):
    current = frame.copy().reset_index(drop=True)
    meta: dict[tuple[int, str], dict[str, Any]] = {}
    manual: set[tuple[int, str]] = set()

    for _ in range(max_passes):
        suggestions, diagnostics = diagnose_repairs(
            current,
            reference_profile=profile,
            learn_from_current=False,
        )
        questions = diagnostics.get("manual_questions", pd.DataFrame())
        if isinstance(questions, pd.DataFrame) and not questions.empty:
            for _, q in questions.iterrows():
                pos = int(q["Source Row"]) - 2
                manual.add((pos, str(q["Field"])))

        if suggestions is None or suggestions.empty:
            break

        for _, s in suggestions.iterrows():
            pos = int(s["Source Row"]) - 2
            meta[(pos, str(s["Source Column"]))] = {
                "repair_type": str(s["Repair Type"]),
                "confidence": float(s["Confidence"]),
            }
        updated, _ = apply_repairs(current, suggestions, accepted_indices=list(suggestions.index))
        if updated.equals(current):
            break
        current = updated
    return current, meta, manual


def run_mass_validation(
    clean_df: pd.DataFrame,
    *,
    scenarios: int = 20_000,
    seed: int = 20260909,
    holdout_share: float = 0.20,
    batch_size: int = 20,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    progress_every: int = 1000,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Stress-test repair intelligence without materialising millions of decisions.

    Pattern values are learned only from training rows. Accuracy is scored only on
    corrupted held-out rows, which prevents the cleaner from being rewarded for
    memorising the exact row it is asked to reconstruct.
    """
    clean = clean_df.copy().reset_index(drop=True)
    train, holdout_rows = _split_train_holdout(clean, holdout_share, seed)
    profile = learn_portfolio_patterns(train, profile_name="mass_validation_training_profile")

    eligible = _eligible_cells(clean)
    holdout_cells = [cell for cell in eligible if cell[0] in holdout_rows]
    max_cells = len(eligible)
    levels = tuple(sorted(set(min(int(x), max_cells) for x in levels if int(x) > 0)))

    totals = Counter()
    by_field: dict[str, Counter] = defaultdict(Counter)
    by_method: dict[tuple[str, str], Counter] = defaultdict(Counter)
    by_severity: dict[int, Counter] = defaultdict(Counter)
    failure_samples: list[dict[str, Any]] = []
    max_failure_samples = 5000

    start = time.time()
    completed = 0

    while completed < scenarios:
        this_batch = min(batch_size, scenarios - completed)
        batch_frames: list[pd.DataFrame] = []
        batch_specs: list[dict[str, Any]] = []

        for offset in range(this_batch):
            scenario_id = completed + offset + 1
            error_count = _scenario_error_count(scenario_id, levels, max_cells)
            cells = _scenario_cells(
                eligible,
                holdout_cells,
                scenario_id=scenario_id,
                errors=error_count,
                base_seed=seed,
            )
            damaged = clean.copy()
            for row_index, field in cells:
                damaged.at[row_index, field] = np.nan
            damaged["__scenario_id"] = scenario_id
            damaged["__original_row"] = np.arange(len(damaged), dtype=int)
            batch_frames.append(damaged)
            batch_specs.append({
                "scenario_id": scenario_id,
                "errors": error_count,
                "cells": cells,
            })

        joined = pd.concat(batch_frames, ignore_index=True)
        repaired, meta, manual = _repair_frame(joined, profile)

        rows_per_scenario = len(clean)
        for batch_pos, spec in enumerate(batch_specs):
            scenario_id = spec["scenario_id"]
            error_count = spec["errors"]
            start_pos = batch_pos * rows_per_scenario
            corrupted_by_row: dict[int, list[str]] = defaultdict(list)
            for row_idx, field in spec["cells"]:
                corrupted_by_row[row_idx].append(field)

            # Only held-out corrupted cells are scoring decisions.
            scored_cells = [(r, f) for r, f in spec["cells"] if r in holdout_rows]
            totals["scenarios"] += 1
            totals["corruptions"] += error_count
            totals["scored_decisions"] += len(scored_cells)
            by_severity[error_count]["scenarios"] += 1
            by_severity[error_count]["corruptions"] += error_count

            for row_idx, field in scored_cells:
                global_pos = start_pos + row_idx
                truth = clean.loc[row_idx]
                missing_fields = tuple(corrupted_by_row[row_idx])
                expected = expected_recoverability(truth, missing_fields, profile)[field]
                final_value = repaired.at[global_pos, field]
                repaired_present = not _missing(final_value)
                actual = "REPAIR" if repaired_present else "ESCALATE"

                method_info = meta.get((global_pos, field), {})
                method = str(method_info.get("repair_type") or ("ESCALATE" if actual == "ESCALATE" else "UNKNOWN"))

                if expected == "REPAIR":
                    if actual == "REPAIR" and _value_matches(field, truth[field], final_value):
                        result = "CORRECT_REPAIR"
                        passed = True
                    elif actual == "REPAIR":
                        result = "WRONG_REPAIR"
                        passed = False
                    else:
                        result = "MISSED_REPAIR"
                        passed = False
                else:
                    if actual == "ESCALATE":
                        result = "CORRECT_ESCALATION"
                        passed = True
                    else:
                        result = "UNSAFE_GUESS"
                        passed = False

                display_field = DISPLAY_FIELD[field]
                totals["passed"] += int(passed)
                totals["failed"] += int(not passed)
                totals[result.lower()] += 1
                by_field[display_field]["decisions"] += 1
                by_field[display_field]["passed"] += int(passed)
                by_field[display_field][result.lower()] += 1
                by_method[(display_field, method)]["decisions"] += 1
                by_method[(display_field, method)]["passed"] += int(passed)
                by_severity[error_count]["decisions"] += 1
                by_severity[error_count]["passed"] += int(passed)

                if not passed and len(failure_samples) < max_failure_samples:
                    failure_samples.append({
                        "scenario_id": scenario_id,
                        "errors_in_scenario": error_count,
                        "row_index": row_idx,
                        "source_row": row_idx + 2,
                        "field": display_field,
                        "expected_action": expected,
                        "actual_action": actual,
                        "result": result,
                        "repair_type": method,
                        "confidence": method_info.get("confidence"),
                        "original_value": truth[field],
                        "final_value": final_value,
                    })

        completed += this_batch
        if progress_every and (completed % progress_every == 0 or completed == scenarios):
            elapsed = time.time() - start
            rate = completed / elapsed if elapsed else 0
            accuracy = totals["passed"] / max(1, totals["scored_decisions"])
            eta = (scenarios - completed) / rate if rate else 0
            print(
                f"[{completed:>7,}/{scenarios:,}] "
                f"accuracy={accuracy:7.3%}  "
                f"scored={totals['scored_decisions']:,}  "
                f"rate={rate:,.1f} scenarios/s  ETA={eta/60:,.1f} min",
                flush=True,
            )

    elapsed = time.time() - start
    decisions = int(totals["scored_decisions"])
    summary = {
        "scenarios": int(totals["scenarios"]),
        "total_corruptions_generated": int(totals["corruptions"]),
        "held_out_decisions_scored": decisions,
        "passed": int(totals["passed"]),
        "failed": int(totals["failed"]),
        "accuracy": float(totals["passed"] / decisions) if decisions else 0.0,
        "correct_repairs": int(totals["correct_repair"]),
        "correct_escalations": int(totals["correct_escalation"]),
        "wrong_repairs": int(totals["wrong_repair"]),
        "missed_repairs": int(totals["missed_repair"]),
        "unsafe_guesses": int(totals["unsafe_guess"]),
        "training_rows": int(len(train)),
        "holdout_rows": int(len(holdout_rows)),
        "holdout_share": float(holdout_share),
        "eligible_cells_per_portfolio": int(max_cells),
        "severity_levels": list(levels),
        "max_errors_in_one_scenario": int(max(levels)),
        "seed": int(seed),
        "elapsed_seconds": float(elapsed),
    }

    def serialise_counter_map(mapping):
        out = []
        for key, counts in mapping.items():
            row = {}
            if isinstance(key, tuple):
                row["field"], row["method"] = key
            elif isinstance(key, int):
                row["errors_in_scenario"] = key
            else:
                row["field"] = key
            row.update({k: int(v) for k, v in counts.items()})
            if row.get("decisions", 0):
                row["accuracy"] = row.get("passed", 0) / row["decisions"]
            out.append(row)
        return out

    evidence = {
        "by_field": serialise_counter_map(by_field),
        "by_method": serialise_counter_map(by_method),
        "by_severity": serialise_counter_map(by_severity),
        "failure_samples": failure_samples,
    }

    # This is the reusable part the product consumes. It contains method
    # reliability, not portfolio-specific fee/currency/ticker values.
    policy_methods = {}
    for (field, method), counts in by_method.items():
        decisions_count = int(counts.get("decisions", 0))
        if not decisions_count:
            continue
        accuracy = float(counts.get("passed", 0) / decisions_count)
        policy_methods[f"{field}|{method}"] = {
            "field": field,
            "method": method,
            "validated_decisions": decisions_count,
            "validated_accuracy": accuracy,
            "auto_accept_recommended": bool(
                method == "RECONSTRUCTED" and decisions_count >= 100 and accuracy >= 0.999
            ),
        }
    policy = {
        "policy_version": "3.23",
        "generated_from": "held-out synthetic corruption validation",
        "scenarios": int(totals["scenarios"]),
        "overall_accuracy": summary["accuracy"],
        "training_rows": int(len(train)),
        "holdout_rows": int(len(holdout_rows)),
        "methods": policy_methods,
        "important_note": (
            "Validation accuracy applies only to the synthetic missing-cell corruption "
            "regime represented by this run. Portfolio-specific values must still be "
            "learned from each new upload."
        ),
    }
    return summary, evidence, {"profile": profile, "policy": policy}


def save_outputs(summary, evidence, artefacts, output_dir: str | Path):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mass_validation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "validated_cleaning_policy.json").write_text(json.dumps(artefacts["policy"], indent=2), encoding="utf-8")
    save_pattern_profile(artefacts["profile"], out / "training_pattern_profile.json")
    pd.DataFrame(evidence["by_field"]).to_csv(out / "accuracy_by_field.csv", index=False)
    pd.DataFrame(evidence["by_method"]).to_csv(out / "accuracy_by_method.csv", index=False)
    pd.DataFrame(evidence["by_severity"]).to_csv(out / "accuracy_by_severity.csv", index=False)
    pd.DataFrame(evidence["failure_samples"]).to_csv(out / "failure_samples.csv", index=False)
    return out


def main():
    parser = argparse.ArgumentParser(description="Run 20k portfolio-wide missing-cell stress validation.")
    parser.add_argument("--ground-truth", default="tests/fixtures/clean_transactions.csv")
    parser.add_argument("--scenarios", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--holdout-share", type=float, default=0.20)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--output", default="validation_outputs")
    args = parser.parse_args()

    clean = _load_ground_truth(args.ground_truth)
    print("MASS VALIDATION LAB", flush=True)
    print(f"Ground truth rows: {len(clean):,}", flush=True)
    print(f"Scenarios: {args.scenarios:,}", flush=True)
    print("Severity includes catastrophic 1,000-1,200 missing-cell portfolios.", flush=True)
    print("Scoring is held-out: those rows are not used to learn the reference profile.\n", flush=True)

    summary, evidence, artefacts = run_mass_validation(
        clean,
        scenarios=args.scenarios,
        seed=args.seed,
        holdout_share=args.holdout_share,
        batch_size=args.batch_size,
        progress_every=args.progress_every,
    )
    out = save_outputs(summary, evidence, artefacts, args.output)

    print("\nMASS VALIDATION COMPLETE")
    print(f"Scenarios:              {summary['scenarios']:,}")
    print(f"Corruptions generated:  {summary['total_corruptions_generated']:,}")
    print(f"Held-out decisions:     {summary['held_out_decisions_scored']:,}")
    print(f"Decision accuracy:      {summary['accuracy']:.3%}")
    print(f"Wrong repairs:          {summary['wrong_repairs']:,}")
    print(f"Missed repairs:         {summary['missed_repairs']:,}")
    print(f"Unsafe guesses:         {summary['unsafe_guesses']:,}")
    print(f"Max errors in scenario: {summary['max_errors_in_one_scenario']:,}")
    print(f"Reusable policy:        {out / 'validated_cleaning_policy.json'}")


if __name__ == "__main__":
    main()
