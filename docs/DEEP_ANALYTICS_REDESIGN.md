# Deep Analytics redesign — review report

10 October 2026. Development branch: `feat/deep-analytics-visual-redesign`. Baseline: `10827c423ef5104b8ca51588ff0439f9ae3bde30`. Production has not been merged or deployed. Paid AI was disabled throughout testing; no paid provider calls were made.

## Milestone outcomes

| Step | Result |
|---|---|
| Replacement and empty charts | Implemented. Candidates are validated before replacing accepted state. Scenario, Copilot, constituent and chart selections are invalidated. Invalid candidates preserve the prior portfolio and show explicit rejection diagnostics. Empty figures become compact explanations; genuine zero values remain visible. |
| Supplied snapshot | Implemented for the GBP statement schema. Quantity, quote units, FX, market value, signed cost/P&L and weights reconcile before existing canonical accounting. A declared valuation date is mandatory and resets when file content changes. Supplied snapshot costs stay in their stated reporting currency. |
| Executive Overview | Four compact valid visuals: class donut, sector treemap, source-country bars and top holdings. Available KPI strip and at most three concise findings, with evidence on demand. |
| Performance & Macro | Existing history reused for instrument/category comparisons and ETF benchmarks; indexed growth, daily returns and time windows. Whole-portfolio current-weight simulation is explicitly distinguished from actual ledger performance. Explicit, cached FRED loading; monthly return/rate-change comparison with sample, period and limitations. |
| Drivers / Exposure / Risk | Signed unrealised P&L bars and waterfall, direct chart selection, exposure bars and existing look-through evidence. Existing risk calculations populate compact valid KPIs, contribution and correlation visuals. Scenario investigation remains available. |
| Navigation / Trust | Deep Analytics opens Overview; compact perspective controls. Trust, ETF source review and Copilot remain available through disclosure. Material rejected-submission blockers remain visible. |
| Validation | Regression and independent numerical checks pass. Desktop and 390-pixel responsive screenshots captured. Final acceptance is conditional on fixture identity/date confirmation and physical-phone review. |

## Numerical evidence

The repository sample `examples/demo_portfolio_holdings.csv` exactly matches the brief's row/class counts, 13 sector labels and stated total. It was used pending confirmation that it is the intended uploaded acceptance fixture.

- 58 records: 52 equities, 3 ETFs, 2 bonds, 1 cash.
- Canonical equity: **£439,514.35**; supplied GBP total reconciles within £0.02.
- Unrealised P&L: **£42,772.69**; cash: **£18,500.00**.
- Realised P&L and actual account history remain **unavailable**, not zero.
- All four Overview charts remain useful when history providers fail.
- Source sector/country labels are retained; source-country allocation is not claimed as economic currency exposure.

The CSV does not provide a statement valuation date. Browser fixtures explicitly assumed **9 October 2026** for testing. This is not verification of the actual source date. The application requires the user to declare it. Purchase dates and supplied annualised returns never establish complete account history.

## Actual test results

- Complete regression: **343 passed, 10 warnings, 317.59 seconds** (`pytest --ignore-glob='* 2.py' -q`). Existing unrelated untracked “ 2” copies were excluded and preserved.
- Final focused replacement/snapshot/macro/workspace checks after narrow changes: **14 passed, 10 warnings, 21.34 seconds**.
- Additional changed-file valuation-date reset assertion: **2 workspace tests passed, 9 warnings, 17.95 seconds**.
- Independent financial script: **47/47 numerical checks passed**; snapshot realised P&L remains unavailable. The script's historical baseline label is unchanged; this execution tested the current development tree against the main baseline listed above.
- Warnings concern existing ambiguous purchase-date parsing. They do not establish snapshot history; no accounting equation was changed to suppress them.

Full regression log and numerical JSON accompany the report in the local outputs directory. These are local/CI-style checks, not production infrastructure verification.

## Persona evidence and scope

| Persona | What was verified | Limit |
|---|---|---|
| Daniel | Real-browser NVDA 85% evidence, HHI 0.745 and effective holdings 1.34; concentration evidence expands. Existing unknown ETF coverage remains explicit. | No invented ETF constituents. |
| Priya | Real-browser HIGHVOL: 50% capital vs 97.2% volatility contribution; volatility 23.12%, VaR $202.87, ES $204.68, 984 observations; Risk routes to scenarios. | These numerical browser risk results use explicitly labelled synthetic history, not the 58-row statement's account performance. |
| Oliver | Real-browser missing JPY/USD rejects the replacement with an explicit affected-total diagnostic. Prior $10,000 HIGHVOL/LOWVOL state remains accepted. Existing mixed-currency regression retained. | Generic foreign snapshots still need valid FX evidence. |
| James | Real-browser UNPRICED replacement identifies the security, blocks complete valuation and preserves prior holdings. Existing duplicate review and consolidation tests pass. | Full multi-account journey was regression-tested rather than freshly repeated end-to-end in the browser this milestone. |
| Maya | 58-row canonical state reused across perspectives/navigation without re-entry; CSV/XLSX replacement and consolidation acceptance regression pass. | Full multi-account browser journey was not repeated this milestone. |

No claim is made that all five complete persona stories received a fresh full browser acceptance run.

## Browser evidence

- Desktop Overview/Drivers/Risk and unavailable Growth/Drawdown inspected.
- Microsoft, Nvidia and Apple exposure selection returned their respective £3,907.20, £11,988.00 and £2,553.00 values in different selection order.
- Direct sector bar selection filters the related waterfall: Communication Services displays META, NFLX and GOOGL; other positions leave that filtered waterfall.
- Real FRED source loaded with TLS certificate validation: 867 observations, July 1954–September 2026. Synthetic HIGHVOL comparison produced 44 complete overlapping months, February 2023–September 2026; descriptive return/rate-change correlation 0.04. Provider revisions/release lags are not modelled.
- 390×844 responsive viewport: measured document/scroll width 390, wrapped navigation and stacked KPIs. Physical phone/browser testing remains outstanding; this is viewport emulation, not hardware validation.
- Screenshots: before, Overview, Drivers, selected-sector drill-down, Priya Risk, Macro and narrow viewport.

## Changed files and functions

- `app.py`: upload preparation/acceptance, reporting-currency formatting, chart rendering and compact Deep Analytics/Copilot shell.
- `input_engine/snapshot.py`: `reported_snapshot` validates source evidence, supplies dated valuation FX and reporting cost metadata.
- `core/portfolio_state.py`: `build_portfolio_state` accepts validated reporting-currency cost basis and withholds snapshot account history. Existing valuation/P&L/risk equations preserved.
- `ui/accepted_state.py`: `replace_accepted`; `ui/consolidation_acceptance.py`: acceptance uses the same replacement operation.
- `ui/chart_guard.py`: `has_chart_evidence`, `render_chart`.
- `ui/intelligence.py`: label join, allocations and Overview/Drivers/Exposure/Risk rendering.
- `ui/performance.py`: `render_performance`, cached explicit rate loading.
- `analytics/macro.py`: `fetch_us_policy_rate`, `monthly_comparison`.
- `ui/discover.py`: perspective navigation/progressive disclosure; `ui/insights.py`: reporting-currency fallback text.
- Tests: new replacement, reported snapshot, macro comparison and workspace files; existing Discover/Trust tests select the retained Discover perspective explicitly.

## Remaining limitations and approval gates

1. Confirm sample identity and actual statement price/FX valuation date before calling fixture acceptance complete.
2. Policy-rate support initially covers US/USD listings only; UK/euro-area series are explicitly unavailable. USD listing currency is not asserted to equal economic exposure.
3. Historical comparisons depend on real provider coverage. Snapshot metrics survive history failures, but history/risk cannot be inferred from a statement.
4. Session-only storage, uploaded ETF evidence requirements and existing scenario assumptions remain unchanged.
5. Narrow viewport checked; physical-phone text sizing/touch interaction remains a manual review step.
6. No production merge/deploy is authorised by this brief. Review the draft PR and staging visual before approving production changes.

Local staging is an offline test harness with real application UI/accounting and mocked historical providers. It is not a public deployment and contains no API key. No dependencies were added; certificate verification uses the already locked `certifi` package.
