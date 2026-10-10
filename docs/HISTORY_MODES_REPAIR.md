# Portfolio Overview historical modes repair

Both historical modes and both chart panels are restored in Portfolio Overview and the dashboard. This is a local, reviewable correction on draft PR #13. Nothing was merged or deployed; no paid AI calls were made.

## Why charts were unavailable

1. The Deep Analytics visual redesign omitted historical chart panels from Overview. The legacy dashboard control appeared only for dated input and could fall back to simulation when actual history was unavailable.
2. The 58-row demonstration statement had 57 securities plus cash. Bare exchange identifiers caused missing Yahoo history for BARC, DGE, HSBA, IGLT, LLOY, MRK, SIE, ULVR and VWRL. Whole-book coverage requires every position, so partial prices produced zero complete common observations rather than a misleading partial-book risk number.
3. Bare names can also identify the wrong listing. Verified exchange mappings now retain original canonical holding identifiers, check provider symbol, issuer and quote currency, and scale London GBp prices to GBP by 0.01. London ETFs quoted directly in GBP remain unchanged. Unverified mappings remain unavailable. Foreign unqualified symbols cannot consume an incompatible US quote.
4. Live validation exposed a pandas 3 metadata issue: DataFrame split metadata was propagated into arithmetic Series, where pandas compared attrs and raised an ambiguous-truth error. The transport metadata stays on source history; calculation copies drop attrs. Accounting equations and dividend/split methodology are preserved.

## Behaviour and calculations

- Segmented modes: Simulate Today's Holdings / Actual Portfolio History. Segmented periods: 1Y / 3Y / 5Y / 10Y. Selection persists through Drivers, Overview and dashboard navigation.
- Simulation defaults for holdings and uses the existing current-book risk/return engine, accepted signed weights, adjusted prices and aligned historical FX. Daily rebalancing, dividends/splits, zero base-cash interest, foreign-cash FX, and omitted transaction/financing costs are disclosed. It is labelled hypothetical.
- Actual mode requires the existing dated ledger accounting path. Purchase dates, average costs and snapshot prices are insufficient. Equity includes cash flows; returns, growth and drawdown use the existing flow-adjusted daily account returns. A separate flow-adjusted growth chart is expandable.
- Both chart headings/panels remain when data is unavailable; concise explanations replace figures. Missing holdings and common-observation coverage are reported, without empty axes or silent exclusions.
- Changing simulation period requests cached Yahoo history for that period; navigation reuses accepted/cached data. Explicit spreadsheet history is respected and never silently replaced by Yahoo. Displayed observation dates disclose available coverage.
- Manual Refresh selected history can retry unavailable Yahoo data. No automatic AI requests.

## Actual live numerical evidence

Resolved demonstration sample: 57/57 securities; no missing FX; 683 common observations. Snapshot equity remains GBP 439,514.35. Hypothetical annualised volatility 10.0340%; max drawdown -15.0448%; 95% one-day VaR GBP 3,714.89; expected shortfall GBP 5,943.12. This run assumes 9 October 2026 as the statement date for testing, not a confirmed source valuation date. These are historical model outputs, not actual sample account performance or forecasts.

## Real-browser evidence

Fresh actual application on localhost:8513, with AI disabled:

- Synthetic NVDA/MSFT holdings: 752 common 3Y observations; both simulation curves rendered. Actual mode shows both explanatory panels and no fabricated curve.
- Actual mode + 1Y retained through Drivers -> Overview. Accepted positions survive navigation.
- Synthetic NVDA ledger includes deposits of USD 1,000 and 300, a buy and a sale. Actual account history is checked and both actual charts render.
- Same ledger, same 1Y: simulation annualised return 15.25%, volatility 19.15%, drawdown -10.31%; actual annualised return 6.39%, volatility 18.76%, drawdown -13.70%. Different engine paths are demonstrated, rather than labels alone.
- Full synthetic statement accepted with the explicitly assumed 9 October 2026 date: all 57 securities covered, 684 common observations, and both charts rendered. Its as-of-anchored window is distinct from the current-date live numerical run above; actual observed dates are displayed. Screenshot: history-full-sample-after.jpg.
- Existing port 8512 was still serving its previous imported implementation; a fresh server on 8513 was used without changing the user's existing session. Restart the local application to load this correction.

Screenshots: redesign-overview.png (before: allocation-only Overview), history-simulation-after.jpg, history-ledger-actual-after.jpg, history-holdings-actual-unavailable.jpg. DOM evidence: history-navigation-evidence.txt and history-ledger-{actual,simulation}-evidence.txt.

## Validation

Full regression: 353 passed, 16 warnings, 491.23 seconds. Final focused run: 11 passed, 9 warnings, 45.98 seconds. The focused run includes the additional unqualified-foreign-symbol guard and all four period controls added after full-suite collection. No failures remain. Warnings are existing date-normalisation warnings. Logs: history-modes-final-regression.txt and history-modes-final-focus.txt.

All 47 independent financial checks passed on the corrected engine. Focused tests cover distinct actual/simulated returns, period slicing, missing history, missing FX, full versus partial coverage, GBX, pandas split attrs, canonical-state preservation, segmented controls and navigation cleanup, verified issuer/currency mappings and rejection of incompatible US quotes.

## Changed files

app.py (history fetch caches, source declarations, period metadata, dashboard history rendering); portfolio_analytics/core/history_listings.py (verified Yahoo history adapter); portfolio_analytics/analytics/engine.py (_base_prices metadata isolation); portfolio_analytics/ui/history_workspace.py (shared selection, windowing and rendering); portfolio_analytics/ui/intelligence.py (Overview integration); portfolio_analytics/ui/accepted_state.py (accepted-portfolio cache invalidation); tests/test_history_modes.py; tests/test_history_listings.py; tests/test_workspace_replacement.py; this report.

## Limitations

Yahoo availability and metadata can fail temporarily. No missing security is dropped to manufacture a whole-portfolio result. Mapping coverage is deliberately limited to supported source country/currency declarations; ambiguous listings require exact Yahoo identifiers and correct quote units. Multi-account sources without listing metadata still require explicit identifiers. Historical valuation-series imports outside the existing supported parser are not newly implemented. Actual ledger results require complete executions, flows and usable marks; unrecorded events cannot be inferred. Spreadsheet windows cannot invent observations beyond the supplied accepted data. Source statement valuation date remains unconfirmed. Current snapshot valuation is kept distinct from market-history simulation.
