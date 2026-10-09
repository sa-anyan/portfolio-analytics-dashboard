# Phase 2A — Trust Diagnostics completion report

Baseline: PR #3, commit `cf08436d673fef1f03769559f37927cd5a9af108`. Implementation branch: `feat/trust-diagnostics`. PRs #1–#3 remain unmerged. Phase 2B has not begun.

## Delivered

Deterministic `analytics.trust_diagnostics` evaluates currency integrity, current pricing provenance, historical FX problems, canonical risk coverage and account-performance availability. It consumes existing state, analytics, timestamps and FX observations; it never calculates a competing portfolio valuation or makes market/AI requests.

Data Quality / Trust appears before Discover findings and above the dashboard. The first three material issues are visible; expandable evidence names holdings, affected calculations, observation dates, age limits, currency distinctions, common sample size and period. The existing methodology document is downloadable. No numerical confidence score is invented.

Required missing/invalid FX, stale FX and unknown FX timestamps prevent acceptance. If accepted FX expires, affected dashboard totals and position values are withheld; Discover findings/scenarios and Copilot requests are gated. The canonical accepted portfolio remains unchanged. Rejected submissions remain inspectable; any previous accepted portfolio is explicitly labelled. Older sessions without FX evidence must refresh instead of bypassing diagnostics.

Missing prices preserve canonical complete-portfolio rejection: no asset is silently dropped. Stale/undated security marks receive warnings and affected figures are labelled as uncertain. The market adapter retains the exact last observation date per ticker instead of making a forward-filled mark appear fresh. Its latest price values are unchanged.

FX defaults to a five-calendar-day freshness limit; prices have their own five-day rule. Users can configure both within Trust evidence, including before a first submission is accepted. Rules survive navigation. Historical FX gaps and invalid observations are disclosed because the existing adapter masks invalid rates and carries prior valid observations forward. No financial alignment formula is changed.

Risk uses existing coverage/common observations. Twelve observations produce an interpretation warning while retaining the engine's estimates. Missing/non-finite/negative required risk values are masked only in display/Copilot lookup output, not rewritten in canonical analytics. Unavailable risk findings are not replaced by a zero-risk claim. Scenario and Copilot output retain source trust limitations; the existing price-shock workflow still passes.

## Actual diagnostic outputs

Deterministic fixtures use an as-of date of **2026-10-09**, without live or paid requests.

| Case | Actual diagnostic result |
|---|---|
| GBP/USD/EUR book missing EUR FX | `valuation_status=blocked`; `fx_missing_invalid`; affected holdings include `EU` and `CASH:EUR`; valuation, risk, performance and scenarios affected. No submitted aggregate is accepted. |
| GBP/EUR FX dated 2026-09-29 | Age **10 calendar days** against a **5-day** limit; `fx_stale`; submitted portfolio rejected. |
| Valid mixed book with a EUR short, EUR cash and GBX holding | Reporting **USD**; `valuation_status=checked`; canonical equity **209.25 USD**, foreign cash **22 USD**; GBX uses GBP/100. Listing currency is explicitly distinct from unknown company economic exposure. |
| UK security without a mark | `price_missing`, holding **UK**; valuation/allocation/risk/performance/scenarios identified; complete valuation withheld. |
| Priya's 12-return sample | `common_observations=12`; `risk_short_sample`; `risk_status=limited`; interpretation minimum **30**, engine minimum **2**. Existing estimates unchanged. |
| Missing/NaN/infinite/negative volatility | `risk_values_unavailable`; `risk_status=unavailable`; display **—**, Copilot tool value **null**, never substituted zero. |
| Price dated 2026-09-01 | Age **38 days**; `price_stale`; valuation warning. Other holdings without per-asset dates receive `price_date_unknown`, even when another ticker has a recent aggregate timestamp. |
| Undated FX snapshot | `fx_date_unknown`; aggregate blocked. Dated source series can establish freshness without a separate provider metadata object. |
| Cash-only book | `no_securities`; concentration undefined; missing risk remains unavailable. Any missing trading Currency assumption is disclosed. |

## Persona acceptance

| Persona | Required Phase 2A story | Result |
|---|---|---|
| Oliver | Upload mixed GBP/USD/EUR with missing or stale FX, inspect why aggregates are withheld, then use a valid mixed book. | **Passed the required Phase 2A story** through real parser/Streamlit integration. Both FX failure cases show evidence and no submitted aggregate. Valid foreign cash, GBX and long/short values reconcile to the independent 209.25 USD oracle. Economic currency exposure remains unknown for securities. |
| James | Identify an unpriced asset and the metrics affected by exclusion. | **Passed the required Phase 2A story.** UK is named; affected metrics are explicit. The complete portfolio is rejected rather than silently excluding UK. Previous accepted holdings remain intact and clearly distinguished. This does not complete broader consolidation/broker reconciliation. |
| Priya | Inspect a 12-observation sample and understand that missing/invalid risk is unknown. | **Passed the required Phase 2A story.** Short-sample warnings and evidence render; original estimates are retained; injected invalid volatility renders unavailable. Existing HIGHVOL comparison and scenario tests still pass. Paid AI wording is not independently validated. |

These are acceptance results for the requested milestone stories, not blanket completion of all broader persona goals.

## Actual tests

`python3 -m pytest -q --tb=short`: **148 passed, 0 failed**, in **44.69 seconds**. This includes the 112 prior cases plus 36 additional cases (including parameterisations). `git diff --check` passed.

New unit and integration cases cover: missing/zero/negative/non-finite FX; stale FX and exact configurable age boundary; unknown FX dates/metadata; mixed currencies; long/short; foreign cash; GBX; unpriced holdings; invalid aggregate fields; unavailable/mismatched reporting currencies; stale/unknown prices; invalid historical FX and gaps; 12 observations; missing history; empty/cash-only portfolios; assumed currency; unavailable/invalid risk; actual per-security price timestamps; blocked Copilot; trust evidence in tool output; rejected replacement; failure after valid valuation; expired accepted inputs; older sessions; rule persistence/configuration; and zero additional provider requests during navigation/re-evaluation.

Streamlit AppTest uses the real CSV parser, canonical state, analytics, Discover and scenario engine. File-picker transport and provider responses are mocked. No financial engine file is modified. Previously validated accounting, currencies, drawdown, attribution, historical reconstruction, risk contributions, scenarios and navigation continue to pass.

## Changed files

- `portfolio_analytics/diagnostics/__init__.py`, `portfolio_analytics/diagnostics/trust.py`: policies, evidence and deterministic availability checks.
- `portfolio_analytics/ui/trust.py`: compact disclosure, evidence, rule controls and existing methodology download.
- `portfolio_analytics/ui/discover.py`: trust-first rendering and investigation guards.
- `app.py`: acceptance/expiry gating, persistent rejected-attempt evidence, daily metadata re-evaluation and safe metric rendering.
- `portfolio_analytics/core/market_data.py`: actual per-security observation dates from existing responses.
- `portfolio_analytics/ui/insights.py`, `portfolio_analytics/ai/copilot.py`: unavailable-risk presentation and trust-aware Copilot output/guards.
- `tests/test_trust.py`, `tests/test_app_trust.py`, `tests/test_market_data_cash.py`: diagnostic and persona regressions.
- `tests/test_app_accounting.py`: keep the existing numerical mixed-currency oracle while using current fixture dates so new freshness rules can be satisfied.
- `docs/MATH_METHODOLOGY.md`, `docs/PHASE_2A.md`: diagnostic assumptions and this report.

## Limitations and unresolved accuracy risks

- Calendar-day age rules are configurable heuristics. Holidays, trading sessions, intraday prices and timestamp accuracy are not validated. A checked result is not a broker reconciliation or an accuracy guarantee. Raising an age limit can accept older FX; the chosen rule is visible in evidence.
- User-supplied marks without dates remain unverified warnings. ETF look-through/economic security currency exposures are unknown. Currency labels and provider listings still require broker verification.
- Historical FX invalid observations and long gaps are warned about, while the prior forward-fill methodology is preserved. These warnings do not repair the data or establish historical completeness. Security history shows the existing coverage/sample period; no exchange-calendar completeness model is added.
- The 30-observation interpretation and 20-tail warning thresholds do not establish reliable risk estimates. Historical correlations, static scenario risk and tail samples retain their existing limitations.
- Account-history reconstruction, dividend/fee/borrow-income omissions, end-of-day flow assumptions and holdings-only reconstruction remain as documented in PR #1. Missing actual performance is explicitly distinguished from current-weight hypothetical simulation.
- This is deterministic app-layer trust enforcement. Direct callers of canonical engines retain their original contracts and must consume diagnostics/app guards when presenting results. No alternative valuation engine is introduced.
- Live feeds, native browser file selection, paid AI responses, mobile visual acceptance and broker reconciliation were not tested. No new dependencies, data sources or paid services are added.

## Stop point

Phase 2A is complete and ready for review. No merges were performed. Phase 2B requires explicit user approval.
