# Yahoo history follow-up — 10 October 2026

The missing risk snapshot in the earlier local preview was caused by an intentionally offline staging harness. The production application already called its Yahoo history adapter, but its default `3y` period was passed directly to Yahoo. Annual windows now use explicit date ranges (including 3y).

Default input behaviour: Yahoo Finance adjusted daily price history for the selected 1y/3y/5y/10y window. Snapshot valuation evidence remains separate. The checkbox **Use spreadsheet price history** is off by default. If enabled, a separate bounded CSV/XLSX import requires ISO `Date`, `Ticker`, `Adjusted Close` daily observations, with positive finite prices and unique date/security identities. Missing columns, invalid data or absent uploads never silently fall back to Yahoo. The existing FX adapter still supplies historical reporting-currency conversion. Existing actual-account reconstruction and risk methodology remain unchanged.

The local preview was replaced with a real-adapter development harness. Paid AI remains disabled. No production merge/deploy occurred.

## Live evidence

- NVDA/MSFT: 753 adjusted daily prices per ticker, 10 October 2023–9 October 2026; 752 common return observations.
- Real-browser synthetic NVDA/MSFT holdings, with live quotes: annualised volatility 37.03%, historical 95% one-day VaR $921.02, ES $1,361.60, historical current-weight simulation drawdown −30.93%. These are modelled risks for that accepted test portfolio, not metrics for the 58-row sample.
- Main dashboard and Deep Analytics share that same accepted return history and risk estimates. Screenshot: `yahoo-live-risk.png`.
- The 58-row sample: Yahoo returned 753 dated rows and GBP FX history with no missing currencies. Full-book risk remained unavailable because usable history was missing for **BARC, DGE, HSBA, IGLT, LLOY, MRK, SIE, ULVR, VWRL** in this request. MRK failure may be transient; provider error is not proof of delisting.
- Canonical sample equity stayed £439,514.35. Missing assets were not silently dropped from full-portfolio risk.

## Remaining data issue

The sample contains bare exchange-specific symbols. Some require Yahoo exchange suffixes; some bare symbols can resolve to an ADR or another company. Supplying the correct listing identifier and matching quote currency/units is necessary before trusting those instrument comparisons. No ticker substitution or guessed listing map was implemented. One current quote per holding is insufficient to reconstruct historical volatility or actual account performance. Incomplete full-book coverage must remain unavailable, while separately labelled available instrument charts can still work.

## Files

`app.py` — source selector and explicit spreadsheet upload branch.
`core/market_data.py` — selected annual windows expressed as download start/end dates.
`input_engine/price_history.py` — validated explicit daily adjusted-price import using existing upload security limits.
`tests/test_history_source.py` — date-window contract, missing coverage, malformed/duplicate observations, source default, selected 5y and failed spreadsheet preservation.

Validation: complete regression **345 passed, 15 warnings, 358.07 seconds**; final source-selection tests **3 passed in 8.61 seconds**; independent numerical checks **47/47 passed**. Warnings concern existing purchase-date parsing. Full evidence is recorded in `yahoo-history-regression.txt`, `yahoo-history-targeted.txt` and `yahoo-history-financial-checks.json`. The final added interaction assertion was run in the separate targeted set after the full suite had collected its tests.
