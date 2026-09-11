from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from portfolio_analytics.validation.automated_validation_lab import ALL_FIELDS, DISPLAY_FIELD, _load_ground_truth, _missing
from portfolio_analytics.quality.auto_clean import auto_clean_ledger
from portfolio_analytics.ingestion.input_parser import clean_number, standardise_ticker
from portfolio_analytics.quality.pattern_learning import learn_portfolio_patterns, save_pattern_profile
from portfolio_analytics.quality.repair_engine import apply_repairs, diagnose_repairs


DEFAULT_LEVELS = (1, 2, 3, 5, 10, 25, 50, 100, 250, 500, 750, 1000, 1200)
CORE_NUMERIC = {"Quantity", "Price", "Gross_Value"}


def _split_train_holdout(df: pd.DataFrame, share: float, seed: int) -> tuple[pd.DataFrame, set[int]]:
    rng = np.random.default_rng(seed)
    idx = np.asarray(df.index, dtype=int)
    n = max(1, int(round(len(idx) * share)))
    holdout = set(int(x) for x in rng.choice(idx, size=n, replace=False))
    train = df.loc[[i for i in idx if int(i) not in holdout]].copy()
    return train, holdout


def _eligible_cells(df: pd.DataFrame) -> list[tuple[int, str]]:
    cells: list[tuple[int, str]] = []
    for i, row in df.iterrows():
        for field in ALL_FIELDS:
            if field in df.columns and not _missing(row[field]):
                cells.append((int(i), field))
    return cells


def _typo(text: str, rng: np.random.Generator) -> str:
    text = str(text)
    if len(text) < 2:
        return text + "X"
    pos = int(rng.integers(0, len(text) - 1))
    chars = list(text)
    chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
    out = "".join(chars)
    return out if out != text else text + "X"


def _numeric_string(value: Any, field: str) -> str:
    v = float(value)
    if field == "Quantity":
        return f" {v:,.0f} "
    return f"${v:,.2f}"


def _choose_corruption(field: str, rng: np.random.Generator) -> str:
    choices: dict[str, tuple[str, ...]] = {
        "Ticker": ("MISSING", "TICKER_TYPO", "TICKER_SUFFIX", "TEXT_NOISE"),
        "Asset": ("MISSING", "ASSET_TYPO", "TEXT_NOISE"),
        "Quantity": ("MISSING", "NUMERIC_SCALE", "NUMERIC_OFFSET", "NUMERIC_STRING"),
        "Price": ("MISSING", "NUMERIC_SCALE", "NUMERIC_OFFSET", "NUMERIC_STRING"),
        "Gross_Value": ("MISSING", "NUMERIC_SCALE", "NUMERIC_OFFSET", "NUMERIC_STRING"),
        "Fees": ("MISSING", "FEE_SPIKE", "NUMERIC_SCALE", "NUMERIC_STRING"),
        "Currency": ("MISSING", "WRONG_CURRENCY", "TEXT_NOISE"),
        "Date": ("MISSING", "INVALID_DATE", "DATE_FORMAT"),
        "Type": ("MISSING", "ACTION_ALIAS", "ACTION_TYPO"),
        "Transaction_ID": ("MISSING", "DUPLICATE_ID", "TEXT_NOISE"),
    }
    return str(rng.choice(choices[field]))


def _apply_corruption(
    damaged: pd.DataFrame,
    clean: pd.DataFrame,
    row_idx: int,
    field: str,
    kind: str,
    rng: np.random.Generator,
) -> Any:
    truth = clean.at[row_idx, field]
    if kind == "MISSING":
        value = np.nan
    elif kind == "TICKER_TYPO":
        value = _typo(str(truth), rng)
    elif kind == "TICKER_SUFFIX":
        value = f" {str(truth).lower()}.US "
    elif kind == "ASSET_TYPO":
        value = _typo(str(truth), rng)
    elif kind == "TEXT_NOISE":
        value = f"  {str(truth).lower()}  "
    elif kind == "NUMERIC_SCALE":
        factor = float(rng.choice([0.1, 10.0, 100.0]))
        value = float(truth) * factor
    elif kind == "NUMERIC_OFFSET":
        base = float(truth)
        value = base + max(abs(base) * 0.35, 7.0)
    elif kind == "NUMERIC_STRING":
        value = _numeric_string(truth, field)
    elif kind == "FEE_SPIKE":
        value = max(float(truth) * 20.0, float(truth) + 25.0)
    elif kind == "WRONG_CURRENCY":
        value = "EUR" if str(truth).strip().upper() != "EUR" else "GBP"
    elif kind == "INVALID_DATE":
        value = "31/31/2099"
    elif kind == "DATE_FORMAT":
        ts = pd.Timestamp(truth)
        value = ts.strftime("%Y/%m/%d")
    elif kind == "ACTION_ALIAS":
        action = str(truth).strip().upper()
        value = "BOUGHT" if action == "BUY" else "SOLD" if action == "SELL" else action
    elif kind == "ACTION_TYPO":
        action = str(truth).strip().upper()
        value = "BYY" if action == "BUY" else "SLEL" if action == "SELL" else _typo(action, rng)
    elif kind == "DUPLICATE_ID":
        choices = [i for i in clean.index if int(i) != int(row_idx)]
        other = int(rng.choice(choices))
        value = clean.at[other, field]
    else:
        raise ValueError(f"Unknown corruption kind: {kind}")
    damaged.at[row_idx, field] = value
    return value


def _semantic_known(kind: str) -> bool:
    return kind in {"TICKER_SUFFIX", "TEXT_NOISE", "NUMERIC_STRING", "DATE_FORMAT", "ACTION_ALIAS"}


def _expected_action(field: str, kind: str, row_events: dict[str, str], profile: dict[str, Any]) -> str:
    # Harmless representation changes should be normalised, not escalated.
    if _semantic_known(kind):
        return "NORMALISE"
    if field in {"Date", "Type", "Transaction_ID"}:
        return "ESCALATE"
    if field == "Currency":
        dom = (profile.get("patterns", {}).get("dominant_currency") or {})
        return "REPAIR" if float(dom.get("share", 0.0)) >= 0.95 else "ESCALATE"
    if field in {"Ticker", "Asset"}:
        partner = "Asset" if field == "Ticker" else "Ticker"
        partner_kind = row_events.get(partner)
        return "REPAIR" if partner_kind is None or _semantic_known(partner_kind) else "ESCALATE"
    if field in CORE_NUMERIC:
        # One corrupted member of the accounting identity can be localised. Two
        # or three simultaneous corruptions in the same triple are ambiguous.
        bad = [f for f in CORE_NUMERIC if f in row_events and not _semantic_known(row_events[f])]
        return "REPAIR" if len(bad) == 1 else "ESCALATE"
    if field == "Fees":
        fee_model = profile.get("patterns", {}).get("fee_model")
        if not fee_model:
            return "ESCALATE"
        bad_core = [f for f in CORE_NUMERIC if f in row_events and not _semantic_known(row_events[f])]
        return "REPAIR" if len(bad_core) <= 1 else "ESCALATE"
    return "ESCALATE"


def _value_matches(field: str, expected: Any, actual: Any) -> bool:
    if _missing(actual):
        return False
    if field == "Ticker":
        return standardise_ticker(actual).replace(".US", "") == standardise_ticker(expected).replace(".US", "")
    if field == "Date":
        try:
            return pd.Timestamp(actual).date() == pd.Timestamp(expected).date()
        except Exception:
            return False
    if field in {"Asset", "Currency", "Type", "Transaction_ID"}:
        return str(actual).strip().upper() == str(expected).strip().upper()
    try:
        act = float(clean_number(actual))
        exp = float(expected)
    except Exception:
        return False
    if field == "Fees":
        return abs(act - exp) <= 0.05
    if field == "Gross_Value":
        return abs(act - exp) <= max(0.50, abs(exp) * 0.001)
    return abs(act - exp) <= max(1e-6, abs(exp) * 0.002)


def _final_semantic_value(field: str, repaired: pd.DataFrame, audit: pd.DataFrame, row_idx: int) -> Any:
    if audit is not None and not audit.empty and row_idx in audit.index:
        row = audit.loc[row_idx]
        mapping = {
            "Ticker": "Ticker",
            "Date": "Date",
            "Type": "Action",
            "Quantity": "Shares",
            "Price": "Price",
            "Fees": "Fee",
            "Transaction_ID": "Transaction ID",
        }
        if field in mapping and mapping[field] in audit.columns:
            return row[mapping[field]]
    return repaired.at[row_idx, field]


def _repair_and_audit(damaged: pd.DataFrame, profile: dict[str, Any]):
    current = damaged.copy().reset_index(drop=True)
    changed: dict[tuple[int, str], dict[str, Any]] = {}
    escalated_rows: set[int] = set()
    escalated_cells: set[tuple[int, str]] = set()

    for _ in range(4):
        suggestions, diagnostics = diagnose_repairs(current, reference_profile=profile, learn_from_current=False)
        manual = diagnostics.get("manual_questions", pd.DataFrame())
        if isinstance(manual, pd.DataFrame) and not manual.empty:
            for _, q in manual.iterrows():
                row = int(q["Source Row"]) - 2
                label = str(q["Field"])
                escalated_rows.add(row)
                if label == "Date": escalated_cells.add((row, "Date"))
                elif label == "Action": escalated_cells.add((row, "Type"))
                elif label == "Transaction ID": escalated_cells.add((row, "Transaction_ID"))
                elif "Quantity" in label or "Price" in label or "Gross" in label:
                    for f in CORE_NUMERIC:
                        escalated_cells.add((row, f))
        if suggestions is None or suggestions.empty:
            break
        for _, s in suggestions.iterrows():
            pos = int(s["Source Row"]) - 2
            col = str(s["Source Column"])
            changed[(pos, col)] = {"method": str(s["Repair Type"]), "confidence": float(s["Confidence"]), "proposed": s["Proposed Value"]}
        updated, _ = apply_repairs(current, suggestions, accepted_indices=list(suggestions.index))
        if updated.equals(current):
            break
        current = updated

    _, report = auto_clean_ledger(current)
    audit = report.get("audit_table", pd.DataFrame()).copy()
    if not audit.empty:
        audit.index = np.arange(len(audit))
        for idx, row in audit.iterrows():
            if str(row.get("Status", "")) in {"REVIEW", "REJECT"}:
                escalated_rows.add(int(idx))
            if bool(row.get("Hard Duplicate ID", False)):
                escalated_cells.add((int(idx), "Transaction_ID"))
    return current, audit, changed, escalated_rows, escalated_cells


def run_hard_validation(
    clean_df: pd.DataFrame,
    *,
    scenarios: int = 20_000,
    seed: int = 20260909,
    holdout_share: float = 0.20,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    progress_every: int = 250,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    clean = clean_df.copy().reset_index(drop=True)
    train, holdout = _split_train_holdout(clean, holdout_share, seed)
    profile = learn_portfolio_patterns(train, profile_name="hard_validation_training_profile")
    eligible = _eligible_cells(clean)
    levels = tuple(min(int(x), len(eligible)) for x in levels)

    totals = Counter()
    by_kind: dict[str, Counter] = defaultdict(Counter)
    by_field: dict[str, Counter] = defaultdict(Counter)
    by_method: dict[tuple[str, str], Counter] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []
    start = time.time()

    for scenario_id in range(1, scenarios + 1):
        rng = np.random.default_rng(np.random.SeedSequence([seed, scenario_id]))
        n_errors = levels[(scenario_id - 1) % len(levels)]
        pick = rng.choice(len(eligible), size=n_errors, replace=False)
        cells = [eligible[int(i)] for i in pick]
        if not any(r in holdout for r, _ in cells):
            holdout_cells = [c for c in eligible if c[0] in holdout]
            cells[-1] = holdout_cells[int(rng.integers(0, len(holdout_cells)))]

        # Object dtype deliberately permits realistic broker-export noise such
        # as "$1,234.56" inside otherwise numeric columns without pandas dtype warnings.
        damaged = clean.copy().astype(object)
        event_by_cell: dict[tuple[int, str], dict[str, Any]] = {}
        row_events: dict[int, dict[str, str]] = defaultdict(dict)
        for r, f in cells:
            kind = _choose_corruption(f, rng)
            corrupted = _apply_corruption(damaged, clean, r, f, kind, rng)
            event_by_cell[(r, f)] = {"kind": kind, "corrupted": corrupted}
            row_events[r][f] = kind

        repaired, audit, changed, escalated_rows, escalated_cells = _repair_and_audit(damaged, profile)

        # False positives: production repair suggestions must not overwrite cells
        # that were untouched by the corruption generator.
        for (r, col), info in changed.items():
            if (r, col) not in event_by_cell:
                totals["false_positive_repairs"] += 1
                display_col = "Gross Value" if col == "Gross_Value" else "Action" if col == "Type" else "Transaction ID" if col == "Transaction_ID" else str(col)
                by_method[(display_col, str(info.get("method", "UNKNOWN")))]["false_positive_repairs"] += 1

        for (r, f), event in event_by_cell.items():
            if r not in holdout:
                continue
            expected = _expected_action(f, event["kind"], row_events[r], profile)
            final_value = _final_semantic_value(f, repaired, audit, r)
            matches = _value_matches(f, clean.at[r, f], final_value)
            escalated = (r, f) in escalated_cells or (r in escalated_rows and expected == "ESCALATE")
            method = (changed.get((r, f)) or {}).get("method", "NORMALISER" if expected == "NORMALISE" else "NONE")

            if expected in {"REPAIR", "NORMALISE"}:
                if matches:
                    result, passed = "CORRECT_REPAIR", True
                elif escalated:
                    result, passed = "MISSED_REPAIR_ESCALATED", False
                elif (r, f) in changed:
                    result, passed = "WRONG_REPAIR", False
                else:
                    result, passed = "MISSED_DETECTION", False
            else:
                if matches:
                    # A safe exact correction is better than escalation even if
                    # the oracle considered the case ambiguous.
                    result, passed = "SAFE_RECOVERY", True
                elif escalated:
                    result, passed = "CORRECT_ESCALATION", True
                elif (r, f) in changed:
                    result, passed = "UNSAFE_REPAIR", False
                else:
                    result, passed = "MISSED_DETECTION", False

            totals["decisions"] += 1
            totals["passed"] += int(passed)
            totals["failed"] += int(not passed)
            totals[result.lower()] += 1
            if matches: totals["exact_recoveries"] += 1
            if escalated: totals["escalations"] += 1
            by_kind[event["kind"]]["decisions"] += 1
            by_kind[event["kind"]]["passed"] += int(passed)
            by_kind[event["kind"]][result.lower()] += 1
            by_field[DISPLAY_FIELD[f]]["decisions"] += 1
            by_field[DISPLAY_FIELD[f]]["passed"] += int(passed)
            by_method[(DISPLAY_FIELD[f], str(method))]["decisions"] += 1
            by_method[(DISPLAY_FIELD[f], str(method))]["passed"] += int(passed)

            if not passed and len(failures) < 10_000:
                failures.append({
                    "scenario_id": scenario_id,
                    "errors_in_scenario": n_errors,
                    "source_row": r + 2,
                    "field": DISPLAY_FIELD[f],
                    "corruption_type": event["kind"],
                    "truth": clean.at[r, f],
                    "corrupted": event["corrupted"],
                    "final": final_value,
                    "expected": expected,
                    "result": result,
                    "method": method,
                })

        totals["scenarios"] += 1
        totals["corruptions"] += n_errors
        if scenario_id % progress_every == 0 or scenario_id == scenarios:
            elapsed = time.time() - start
            rate = scenario_id / elapsed if elapsed else 0
            eta = (scenarios - scenario_id) / rate if rate else 0
            accuracy = totals["passed"] / totals["decisions"] if totals["decisions"] else 0
            print(
                f"[{scenario_id:>7,}/{scenarios:,}] accuracy={accuracy:7.3%} "
                f"decisions={totals['decisions']:,} false_pos={totals['false_positive_repairs']:,} "
                f"rate={rate:,.2f}/s ETA={eta/60:,.1f} min",
                flush=True,
            )

    elapsed = time.time() - start
    decisions = int(totals["decisions"])
    summary = {
        "scenarios": int(totals["scenarios"]),
        "corruptions_generated": int(totals["corruptions"]),
        "held_out_decisions": decisions,
        "overall_decision_accuracy": float(totals["passed"] / decisions) if decisions else 0.0,
        "exact_recoveries": int(totals["exact_recoveries"]),
        "correct_escalations": int(totals["correct_escalation"]),
        "safe_ambiguous_recoveries": int(totals["safe_recovery"]),
        "wrong_repairs": int(totals["wrong_repair"]),
        "unsafe_repairs": int(totals["unsafe_repair"]),
        "missed_detections": int(totals["missed_detection"]),
        "missed_repair_escalations": int(totals["missed_repair_escalated"]),
        "false_positive_repairs": int(totals["false_positive_repairs"]),
        "training_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "severity_levels": list(levels),
        "max_errors_in_one_scenario": int(max(levels)),
        "elapsed_seconds": elapsed,
        "seed": int(seed),
    }

    def serialise(mapping):
        rows = []
        for key, c in mapping.items():
            row: dict[str, Any] = {}
            if isinstance(key, tuple): row["field"], row["method"] = key
            else: row["category"] = key
            row.update({k: int(v) for k, v in c.items()})
            if row.get("decisions", 0): row["accuracy"] = row.get("passed", 0) / row["decisions"]
            rows.append(row)
        return rows

    evidence = {"by_kind": serialise(by_kind), "by_field": serialise(by_field), "by_method": serialise(by_method), "failures": failures}

    methods = {}
    for (field, method), c in by_method.items():
        n = int(c.get("decisions", 0))
        fp = int(c.get("false_positive_repairs", 0))
        if not n and not fp:
            continue
        passed = int(c.get("passed", 0))
        decision_acc = float(passed / n) if n else 0.0
        # A method that fixes corrupted cells but also changes clean cells is not
        # safe. The reusable policy therefore penalises false positives directly.
        safe_denominator = n + fp
        safe_acc = float(passed / safe_denominator) if safe_denominator else 0.0
        methods[f"{field}|{method}"] = {
            "field": field,
            "method": method,
            "validated_decisions": n,
            "validated_accuracy": safe_acc,
            "decision_accuracy_on_corrupted_cells": decision_acc,
            "false_positive_repairs": fp,
            "auto_accept_recommended": bool(
                n >= 250 and safe_acc >= 0.999 and fp == 0
                and method in {"RECONSTRUCTED", "MAPPING_CORRECTION", "NORMALISER"}
            ),
        }

    policy = {
        "policy_version": "3.25-hard",
        "generated_from": "held-out mixed hard-corruption validation",
        "scenarios": int(totals["scenarios"]),
        "overall_accuracy": summary["overall_decision_accuracy"],
        "false_positive_repairs": summary["false_positive_repairs"],
        "methods": methods,
        "corruption_regime": [
            "missing values", "wrong-but-present numeric values", "ticker/asset typos", "currency conflicts",
            "fee spikes", "duplicate transaction IDs", "invalid dates", "action aliases/typos", "formatting noise",
            "mixed simultaneous corruption up to 1,200 cells",
        ],
        "important_note": "Synthetic validation is evidence about represented error classes, not proof of universal real-world accuracy.",
    }
    return summary, evidence, {"profile": profile, "policy": policy}


def save_outputs(summary: dict[str, Any], evidence: dict[str, Any], artefacts: dict[str, Any], output: str | Path) -> Path:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "hard_validation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "validated_cleaning_policy.json").write_text(json.dumps(artefacts["policy"], indent=2), encoding="utf-8")
    save_pattern_profile(artefacts["profile"], out / "hard_training_pattern_profile.json")
    pd.DataFrame(evidence["by_kind"]).to_csv(out / "hard_accuracy_by_corruption.csv", index=False)
    pd.DataFrame(evidence["by_field"]).to_csv(out / "hard_accuracy_by_field.csv", index=False)
    pd.DataFrame(evidence["by_method"]).to_csv(out / "hard_accuracy_by_method.csv", index=False)
    pd.DataFrame(evidence["failures"]).to_csv(out / "hard_failure_samples.csv", index=False)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Hard mixed-corruption portfolio cleaning benchmark")
    parser.add_argument("--ground-truth", default="tests/fixtures/clean_transactions.csv")
    parser.add_argument("--scenarios", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--holdout-share", type=float, default=0.20)
    parser.add_argument("--progress-every", type=int, default=250)
    parser.add_argument("--output", default="validation_outputs")
    args = parser.parse_args()

    clean = _load_ground_truth(args.ground_truth)
    print("HARD CLEANING VALIDATION LAB", flush=True)
    print(f"Ground truth rows: {len(clean):,}", flush=True)
    print(f"Scenarios: {args.scenarios:,}", flush=True)
    print("Corruptions: missing + wrong-present + contradictions + typos + duplicates + malformed fields", flush=True)
    print("Severity: up to 1,200 simultaneous corrupted cells", flush=True)
    print("Scoring: held-out rows only; the learner never sees their clean values.\n", flush=True)

    summary, evidence, artefacts = run_hard_validation(
        clean, scenarios=args.scenarios, seed=args.seed, holdout_share=args.holdout_share,
        progress_every=args.progress_every,
    )
    out = save_outputs(summary, evidence, artefacts, args.output)
    print("\nHARD VALIDATION COMPLETE")
    print(f"Scenarios:             {summary['scenarios']:,}")
    print(f"Corruptions generated: {summary['corruptions_generated']:,}")
    print(f"Held-out decisions:    {summary['held_out_decisions']:,}")
    print(f"Decision accuracy:     {summary['overall_decision_accuracy']:.3%}")
    print(f"Wrong repairs:         {summary['wrong_repairs']:,}")
    print(f"Unsafe repairs:        {summary['unsafe_repairs']:,}")
    print(f"Missed detections:     {summary['missed_detections']:,}")
    print(f"False-positive repairs:{summary['false_positive_repairs']:,}")
    print(f"Reusable policy:       {out / 'validated_cleaning_policy.json'}")


if __name__ == "__main__":
    main()
