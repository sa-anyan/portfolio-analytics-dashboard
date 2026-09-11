# Automated 20,000-Scenario Cleaning Validation

This build contains a self-running validation lab for the portfolio cleaner.

## What it does

1. Loads a known-perfect transaction ledger.
2. Learns auditable portfolio patterns once from that ground truth.
3. Generates 20,000 unique deterministic corruption scenarios.
4. Removes 1 to 5 fields simultaneously from known-good transactions.
5. Runs the production repair engine iteratively until no more safe repair suggestions exist.
6. Compares every repair with the original known-good value.
7. Counts a safe refusal to invent Date, Action or Transaction ID as a correct escalation.
8. Writes detailed CSV/JSON reports automatically.

This is synthetic stress testing and rule/pattern learning, not neural-network training. The perfect portfolio is the answer key, so the system can score itself without ChatGPT judging each case.

## Run

From the project folder:

```bash
python automated_validation_lab.py
```

Default settings:

```text
Ground truth:  tests/fixtures/clean_transactions.csv
Scenarios:     20,000
Seed:          20260909
Missing cells: 1 to 5 per scenario
Output folder: validation_outputs/
```

Custom run:

```bash
python automated_validation_lab.py --scenarios 20000 --seed 20260909 --max-missing 5 --output validation_outputs
```

## Output

- `validation_summary.json` — headline accuracy and error counts.
- `validation_decisions.csv` — every corrupted field, truth, repair, confidence and result.
- `validation_failures.csv` — only failures for debugging.
- `accuracy_by_field.csv` — accuracy by field.
- `accuracy_by_complexity.csv` — accuracy by number of simultaneous missing fields.
- `learned_pattern_profile.json` — patterns learned from the perfect portfolio.

## Important interpretation

A missing value is not automatically supposed to be reconstructed. Some combinations destroy the information needed to recover a value. Example: if both Quantity and Gross Value are removed and only Price remains, neither can be reconstructed uniquely. The correct behaviour is to escalate rather than guess.

Therefore the key metric is **decision accuracy**, not simply percentage of blanks filled.
