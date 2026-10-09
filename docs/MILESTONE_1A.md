# Milestone 1A — Deterministic Findings

Baseline: PR #1, commit `a34478aea945632a26c4d8761bb29689f2eaffd7`. Implementation branch: `feat/deterministic-findings`. PR #1 remains unmerged and its branch is unchanged.

## Completed

Added a deterministic findings module and attached its output as `analytics.deep_findings`. The canonical state supplies current values; the existing engine supplies risk contributions. No financial return, FX, covariance, VaR, attribution or scenario calculation was replaced. No UI, navigation, fetching, dependency or paid service was added.

Concentration includes top-five exposure, HHI on a 0–1 scale and effective holdings (`1/HHI`). Weights are absolute net security market values divided by canonical gross security exposure, excluding cash. Signed equity weights are also retained, including weights over 100% and negative short weights. Values must reconcile with canonical gross exposure before concentration is available.

Risk contributors are ranked by signed contribution, with a separate absolute ranking and explicit hedge labels. Comparisons retain signed equity weights, gross security exposure shares and absolute risk shares. Explanations disclose different denominators, rather than treating all percentages as interchangeable.

Each finding includes an ID, title, explanation, exact evidence and a suggested investigation. Unknown valuation/risk remains unavailable. Thirty common observations is the default findings-only interpretation floor; it is configurable and does not suppress or change PR #1's underlying metrics. It is not a claim of statistical adequacy, especially for tail estimates. Zero/near-zero volatility and non-finite or unreconciled contributions are not interpreted.

## Problems and personas

Improves B (interpretation) and D (concentration) directly. Provides the backend foundation for A/E (less manual analysis and consistent investigations). F remains explicitly unknown for indirect exposure.

| Persona | Status | Evidence / remaining work |
|---|---|---|
| Daniel | Partially satisfied | Tested direct 85% concentration; ETF look-through and UI remain pending. |
| Priya | Partially satisfied | Tested risk ranking, correlated assets, capital/risk comparison and hedge effects; finding-to-scenario UI remains pending. |
| Maya | Partially satisfied | Uses canonical consolidated holdings; multi-file/account reconciliation remains pending. |
| Oliver | Partially satisfied | PR #1 FX baseline preserved; additional diagnostics remain pending. |
| James | Partially satisfied | Missing history is explicit; consolidation and period investigation remain pending. |

## Changed files

- `portfolio_analytics/analytics/findings.py`: pure deterministic concentration and interpretation.
- `portfolio_analytics/analytics/engine.py`: attach findings to existing analytics output.
- `tests/test_findings.py`: numerical and availability regressions.
- `docs/MILESTONE_1A.md`: milestone record.

## Validation

`python3 -m pytest -q`: **105 passed, 0 failed** (90 baseline cases plus 15 new cases, including parameterisations). `git diff --check` passed.

New cases cover 85% concentration, top-five exposure, long/short leverage, negative hedge contributions, capital/risk differences, perfectly correlated assets, missing history, 12 observations, absent/zero/near-zero volatility, missing/NaN/infinite values, cash exclusion, no state mutation, empty portfolios, inconsistent gross exposure and invalid risk contributions.

Existing accounting, analytics, scenario, Copilot routing/context and Streamlit integration tests all pass. Live market feeds, paid AI responses and broker reconciliation were not tested. There is no Deep Analytics interface to test yet.

## Limitations and next milestone

Security concentration is not sector/geographic/economic concentration. Cash is excluded from gross security concentration. Net positions do not reveal separate lots/accounts or ETF underlying companies. Unknown indirect exposure is never labelled zero. HHI does not incorporate correlation. Historical contribution is not a forecast or suitability judgment. A finite sample can still be economically unrepresentative.

Milestone 1B would render this existing backend output in Discover, with navigation and supporting investigations while preserving portfolio state. It requires explicit approval; no 1B work is included here.
