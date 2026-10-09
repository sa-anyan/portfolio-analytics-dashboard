# Milestone 1B — Deep Analytics Discover

Baseline: PR #2 (`0a37ce7`), including PR #1's validated methodology. Neither PR is merged. No analytics, accounting, risk or scenario formula has changed.

## Delivered

Visible dashboard/Deep Analytics workspace selector. Discover renders `analytics.deep_findings` from the accepted canonical portfolio, initially capped at three prioritised findings. Each finding provides its conclusion, explanation, expandable evidence and investigation suggestion. Existing styles, responsive Streamlit layout and dependencies are reused; no duplicate charts or data requests are added.

Supporting-analysis buttons return to the existing dashboard with a section anchor link. Users click that link to reach allocation, risk or data review; navigation does not automatically scroll. Copilot buttons prepare an editable question without sending an AI request. Existing Copilot lookup/explanation tools now include deterministic findings evidence.

The scenario expander uses the existing price-shock engine, defaults to the leading risk contributor when available, and opens existing scenario results. Accepted holdings and analytics remain unchanged. Findings and tables display existing values; no redundant financial calculation is introduced.

No portfolio, cash-only accounts, missing history, insufficient observations, unavailable findings and incomplete ETF information receive explicit explanations. Unknown ETF indirect exposure is never reported as zero.

## Actual validation

`python3 -m pytest -q`: **112 passed, 0 failed**, in 20.72 seconds. Includes the 105 existing regression cases and seven new Discover cases (including parameterisation). `git diff --check` passed.

Streamlit AppTest executes the real CSV parser, analytics and scenario engine. File-picker transport and market providers are mocked with deterministic synthetic data. Tests exercise workspace selection, evidence rendering, supporting-analysis navigation, editable Copilot prefill, scenario execution/results, state equality and zero additional provider calls on navigation. Missing-history and 12-observation cases show unavailable risk without fabricated rankings. Cash-only and pre-analysis states remain usable. Existing tests retain long/short, correlated-assets and math counterexample coverage.

## Persona evidence

| Persona | Actual accomplishment | Status and limits |
|---|---|---|
| Daniel | Uploads six securities with 85% NVDA; sees concentration immediately, accesses holdings/calculation evidence, sees ETF look-through explicitly unavailable, returns to allocation analysis without changing holdings. | Requested 1B workflow satisfied. Overall persona remains partially satisfied: indirect ETF exposure cannot yet be determined. |
| Priya | Uploads LOWVOL/HIGHVOL at 50% capital each; sees HIGHVOL's 85.2% signed volatility contribution and capital comparison; default investigation selects HIGHVOL; a -15% shock produces -75 USD and opens existing results without changing her portfolio. | Requested 1B workflow satisfied. Historical contribution and static price shocks retain baseline limitations; broader scenario research is outside 1B. |
| Maya | Uses exactly the existing canonical portfolio across both workspaces and scenario investigation; no re-entry and no additional market requests. | Requested state-reuse workflow satisfied. Overall persona remains partially satisfied: multi-account reconciliation is outside 1B. |

No blanket persona Passed claim is made beyond these explicitly tested workflows. Live provider behavior, native browser file selection, paid Copilot responses and mobile/pixel visual validation were not tested. Streamlit's existing column layout is retained; visual responsiveness needs browser acceptance review.

## Changed files

- `app.py`: visible workspace navigation, dashboard rendering guards and section anchors.
- `portfolio_analytics/ui/discover.py`: compact findings/evidence and investigation controls.
- `portfolio_analytics/ai/copilot.py`: deterministic evidence in existing lookup/explanation output.
- `tests/test_discover.py`: interaction, integration and priority regressions.
- `tests/test_app_accounting.py`: locate the input-method radio by label after navigation is introduced.
- `docs/MILESTONE_1B.md`: completion and acceptance record.

## Stop point

Milestone 1B implementation is complete. Phase 2A has not begun and requires explicit user approval. No merge is authorised or performed.
