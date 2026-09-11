# Automated Validation Lab

This project now uses a clean baseline fixture plus deterministic synthetic corruption and `pytest` regression tests.

## Run the suite

```bash
pip install -r requirements.txt
pytest -q
```

## Run with coverage

```bash
pytest --cov=. --cov-report=term-missing
```

## Core rule

When a new bug is found:

1. Add a test that reproduces the bug.
2. Confirm the test fails.
3. Fix the production code.
4. Confirm the new test passes.
5. Run the full suite to make sure previous behaviour did not regress.

## Test structure

- `tests/fixtures/clean_transactions.csv` — clean ground truth.
- `tests/corruption_engine.py` — deterministic noise generator with an audit log.
- `tests/test_corruption_engine.py` — corruption reproducibility.
- `tests/test_schema_parser_validation.py` — fragmented-header/schema classification tests.
- `tests/test_repair_validation.py` — reconstructed/inferred/manual-review rules.
- `tests/test_cleaning_regression.py` — formatting noise and row reconciliation.
- `tests/test_end_to_end_repair.py` — repair → clean → compare with ground truth.
- Existing accounting/valuation smoke tests remain in the project root.

The corruption engine uses fixed random seeds so every failure is reproducible.

## v3.22 — automated 20,000-scenario validation lab

Run `python -m portfolio_analytics.validation.automated_validation_lab` to learn from the perfect portfolio, create 20,000 deterministic mixed-missing scenarios, run the production repair engine, compare every decision with ground truth, and write a failure report automatically. See `RUN_20000_VALIDATION.md`.

## v3.30 accounting coalescing safety
- Prevents price metadata columns such as `Derived Price Method`, `Price Source`, and `Price Change %` from being coalesced into numeric `Price`.
- Uses exact aliases first and at most one semantic fallback during accounting standardisation.
- Coalescing is dtype-safe for legitimate mixed numeric/string aliases.
- Regression-tested against the uploaded 150-row cleaning audit.
- Full suite: 52 passed.

## v3.31 Hybrid portfolio export regression
- Added regression coverage for broker/watchlist CSVs containing both transaction fields and market snapshot/OHLC fields.
- Strong transaction signature (`Transaction Type` + `Quantity` + `Trade Date` + transaction/entry price) now outranks market-data classification.
- Transaction schema precedence: `Trade Date` beats generic market `Date`; `Purchase Price`/trade price beats `Current Price` for accounting.
- Current/reference market fields remain available as metadata instead of being consumed into accounting fields.
- `portfolio.csv` regression routes to ledger and standardises to Trade Date + Purchase Price.
