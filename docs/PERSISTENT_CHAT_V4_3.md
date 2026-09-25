# Persistent Chat — v4.3

This build was rebuilt directly from the user-verified v4.2 baseline.

## Protected engines

The following v4.2 files were intentionally left unchanged:

- `portfolio_analytics/analytics/engine.py`
- `portfolio_analytics/scenarios/engine.py`
- `portfolio_analytics/core/portfolio_state.py`

## Added behaviour

- Recent conversation is supplied to the AI router for reference resolution.
- Routes explicitly distinguish `accepted` from `latest_scenario`.
- Follow-up scenarios can use `scenario_base=latest_scenario`.
- Lookups and explanations can use `scope=latest_scenario`.
- If no scenario exists, Python forces the scope/base back to the accepted portfolio.
- Reset Scenario and Clear Chat are separate controls.
- The accepted Portfolio State is never mutated by chat or scenarios.

## Safety boundary

The LLM selects intent and structured parameters. Python validates scope, tickers, fields and scenario actions before deterministic execution. The LLM does not calculate portfolio metrics.
