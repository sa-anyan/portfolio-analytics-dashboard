# Permanent Demo Portfolios

Implemented on `feat/demo-portfolios`, against main `c90b256` (including dashboard PR #13). The five source CSVs are byte-for-byte PR #14 inputs. PR #14 remains unmerged; this integration includes its fixture changes. Approve the integration PR as the complete replacement for the fixture-only PR, rather than independently merging both.

## User experience

Four permanent cards provide local CSV downloads and direct Load/Apply actions: Holdings (10), Transactions (90), SPY constituents (10), QQQ constituents (8). No GitHub access or manual re-upload is required. Full ETF Analysis Demo loads Holdings and both constituent datasets. Individual and joint Apply actions use the established constituent staging, validation, acceptance and eligibility workflow. They preserve canonical quantities, cash and equity.

Portfolio loads use the existing app parser, currency handling, accounting engine, Trust diagnostics and analytics pipeline. Successful replacement clears previous scenario, Copilot, exposure selection, constituent metadata and historical result caches. Failed validation preserves the previous accepted workspace. Demo requests reset unrelated manual-input cash and spreadsheet-history settings; the selected upload history window is retained.

Holdings default to Simulate Today's Holdings; Transactions default to Actual Portfolio History. Both use Yahoo history through the existing verified listing/FX path. Streamlit widget preference restoration was corrected after browser testing exposed a default-mode reset when navigating between dashboard and Deep Analytics. No accounting or return formula was changed.

## Financial evidence

The supplied Holdings marks produce USD 152,429 equity, 128,221 cost basis, 24,208 unrealised P&L and unavailable realised P&L. There are ten owned positions. SPY reports 39% known constituents and 61% unreported; QQQ reports 50% known and 50% unreported. They are not rescaled. NVDA known exposure is 27,675 direct + 2,254 through SPY + 2,425.50 through QQQ = 32,354.50. The exposure decomposition residual is zero; the economic ranking remains incomplete.

The independent raw-ledger oracle (fixed supplied marks; no split fixture) gives cash 301,840.99 and equity 454,269.99, with 646 net shares across ten positions. This checks quantities, fees, external flows and cash against canonical accounting. Live mode instead uses provider quotes and dated split metadata; its quantities, cost basis and valuation must not be compared to this no-split test as if the inputs were identical.

Holdings and Transactions are independent synthetic scenarios. The supplemental cashflows CSV is included unchanged from PR #14 but is not appended to Transactions: its flows are already represented in the ledger. Appending it would double-count cash.

## Browser verification

Local AI-disabled Streamlit on port 8514, using real Yahoo market data:

- Full ETF Analysis Demo: ten holdings accepted, both constituent datasets applied, 752 common observations, hypothetical growth and drawdown rendered.
- Transactions: dated ledger accepted, actual equity/growth and drawdown rendered, 784 observations within the selected three-year window.
- Holdings → Transactions → full Holdings demo refreshed accounting, chart labels and ETF metadata. Transactions' actual-history selection survived dashboard → Deep Analytics navigation after the correction.
- Exposure selected NVIDIA/NVDA and displayed 32,354.50 USD known exposure, consistent with the independent calculation above.
- All four CSV buttons downloaded bytes identical to their bundled source files.

Screenshots: `outputs/demo-portfolios/cards.png`, `actual-history.png`. Independent numerical results: `financial-checks.json` (47/47 passed).

## Tests and scope

New tests exercise record counts, canonical snapshot accounting, an independent ledger cash/quantity oracle, partial ETF exposure arithmetic, atomic constituent validation, full demo loading, portfolio switching, stale cache/scenario/Copilot/security cleanup, navigation preference preservation, offline history and failed-load preservation. The former 58-row GBP statement remains a dedicated regression fixture; its existing financial tests retain their assertions.

Focused final checks: 11 passed (demo, history modes and history source). Earlier snapshot/intelligence/demo checks: 15 passed. Full regression: 358 passed, 16 existing date-parser warnings, 424.25 seconds. The run started before the last navigation assertion and failed-load test were added; the final focused 11-test run covers those changes. Final GitHub CI is required on the committed integration.

No merge, deployment, production changes or paid AI calls were made.

## Limitations

These are synthetic QA datasets, not a real customer's investment results. Provided holdings quotes lack a verified pricing date and correctly produce Trust warnings. Constituent dates remain the supplied 2026-09-30, with existing configurable freshness checks; they are not silently refreshed. GLD/TLT remain outside ordinary equity ETF look-through scope. Yahoo outages or incomplete prices/FX can make risk or charts unavailable; no results are fabricated and missing holdings are not dropped. Session-only storage remains unchanged. Historical growth is hypothetical for snapshots, and dated ledger performance reflects synthetic transactions with the existing dividend, fee, flow and split conventions.
