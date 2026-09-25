# Copilot Router — v4.2

## Purpose

The router converts natural-language portfolio questions into a small structured instruction. It does **not** calculate portfolio values.

```text
User question
    ↓
OpenAI intent interpretation
    ↓
Route JSON
    ↓
Python normalisation
    ↓
Python validation
    ↓
Deterministic tool
    ↓
Deterministic result
    ↓
OpenAI explanation
```

## Supported intents

- `lookup` — retrieve accepted portfolio or analytics values
- `explain` — explain deterministic portfolio metrics/results
- `scenario` — execute one or more scenario functions
- `general` — textbook finance explanation without invented portfolio values
- `clarification` — stop execution and ask for one missing detail

## Supported scenario actions

The router maps natural language only to scenario functions already implemented in `portfolio_analytics/scenarios/engine.py`:

- `price_shock`
- `rate_shock`
- `resize`
- `remove_position`
- `reallocation`
- `target_risk`

Compound actions are kept in user-stated order. The scenario engine then executes them sequentially.

## Validation boundary

`validate_route()` runs before any scenario instruction executes. It checks:

- allowed intent
- allowed lookup fields
- ticker aliases such as `BTC → BTC-USD`
- open-position existence for lookups, shocks, resize and removal
- action schema using the scenario engine's own validator
- requested rate-shock tickers
- reallocation source existence
- a positive supplied price when reallocating into a new ticker
- no hidden scenario actions inside non-scenario intents

This means the language model proposes an instruction, but Python decides whether the instruction is allowed to reach financial functions.

## Context boundary

Copilot can query every engine stage through `build_copilot_context()`:

- parser metadata and normalised variables
- Portfolio State
- analytics
- latest scenario result

Raw uploaded rows, account/transaction identifiers, heavy historical-return matrices and the deterministic restoration dataset remain inside Python rather than being sent wholesale to OpenAI.

## Execution boundary

`execute_route()` is intentionally independent of OpenAI. Given a validated route it dispatches directly to deterministic Python:

- lookup → Portfolio State / analytics dictionaries
- explain → selected deterministic context
- scenario → `run_scenario()`
- clarification → no calculation
- general → restricted contextual metadata only

This separation makes routing testable without API calls and prevents the LLM from becoming the portfolio calculator.
