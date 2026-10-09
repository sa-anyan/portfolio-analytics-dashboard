# Phase 3B — ETF Look-Through and Hidden Exposure Analytics

Baseline: PR #8, `feat/etf-constituent-inputs`, commit `3bc41a9`. Prior PRs remain unmerged. No later phase is implemented.

## Delivered

Deep Analytics → Exposure now ranks known underlying securities and lets the user select a security to inspect direct ownership, each fund contribution, original weights, dates, typed identity mappings and source fingerprints. Coverage, deterministic findings and reconciliation are expandable. Existing legal holdings, portfolio equity, accounting, direct concentration, risk and scenarios retain their original methodology.

The new deterministic module consumes canonical reporting-currency market values; it never obtains prices, converts currencies, adds owned positions, reconstructs transactions or computes underlying risk. No dependencies, paid sources or market requests were added. Listing currency is explicitly distinguished from economic currency exposure. Separate share classes remain separate securities rather than being silently aggregated into issuer totals.

Before using fund weights, the user must confirm ordinary long-only equity structure and whole-fund capital-allocation weights, with an evidence/reason field. This reversible attestation is bound to the current parent identity. An ETF label alone does not establish eligibility. Changed quantities/market values recompute exposures; changed parent identity requires a fresh declaration. Accepted source records and identity decisions are revalidated against current holdings without market requests.

Leveraged/inverse/derivative structures and short fund positions are unavailable. Cash, non-equity, nested or unspecified constituent types conservatively make that fund's composition unavailable; its positive eligible capital becomes unknown. No recursive or delta/notional calculation is attempted. A missing declaration excludes the fund from the eligible scope and clearly identifies why. Eligible funds without data retain their full value as unknown. Invalid canonical valuation or blocked Trust diagnostics suppress monetary exposure and equity percentages.

## Independent numerical validation

An independent exact rational oracle produces:

| NVDA component | Calculation | USD |
|---|---|---:|
| Direct | 10 × 100 | 1,000.00 |
| Through VOO | 5 × 500 × 6.5 / 100 | 162.50 |
| Through VGT | 4 × 700 × 20 / 100 | 560.00 |
| Combined known | Sum of the three components | **1,722.50** |

Canonical equity remains **6,300.00**. The exact share is `689 / 2520`, or **27.3412698413%**. Production contains no hardcoded fixture results.

In the supplied full synthetic composition, Microsoft is actually largest: **2,650.00 (42.0635%)**, followed by Apple **1,927.50 (30.5952%)**, then NVDA **1,722.50 (27.3413%)**. All three appear in both funds. NVDA additionally has direct ownership. Findings use a disclosed 20% of equity review threshold; this is a deterministic attention rule, not a validated suitability or risk threshold.

Full reconciliation: **6,300 known + 0 unknown + 0 unresolved identities = 6,300 eligible**, residual **0**. Fund market values are decomposed rather than added again to equity.

## Partial and unknown composition

Replacing VOO with the existing top-ten fixture preserves **53% known / 47% unknown**: **1,325 known + 1,175 unknown = 2,500**. VGT stays fully reported. Together with direct NVDA, the decomposition is **5,125 known + 1,175 unknown = 6,300**, residual **0**.

Partial-fixture NVDA is **1,935 known**: direct 1,000 + VOO 375 + VGT 560. No weight is scaled to 100%. Unknown holdings may include NVDA or change the ranking. A lower-bound interpretation is allowed only with positive supported capital exposure, verified weight/composition basis, usable identities and compatible fresh dates; it is not a guarantee about current issuer holdings.

An unverified 100%-weight dataset retains an explicit unverified state and unavailable composition-unknown estimate; zero arithmetic residual does not verify composition. Stale and different-date snapshots remain labelled historical-composition estimates and disable complete-ranking/lower-bound claims. Conflicting typed identifier components remain unresolved, with their monetary amount separately reconciled. Similar names never merge securities.

## Daniel acceptance — Passed for the agreed supported workflow

Streamlit interaction tests upload the actual synthetic portfolio and both constituent CSVs through the existing parser/review/accept path, explicitly confirm completeness and eligibility, and then:

- Identify all three known exposures in descending order.
- Select NVDA and see **1,722.50 USD**, direct **1,000**, VOO **162.50** and VGT **560**.
- Access expandable calculations, exact identity evidence, dates, coverage and reconciliation.
- Prepare an exposure-specific Copilot question without submitting a paid request.
- Navigate Exposure → Discover/Trust → existing scenario → dashboard → Exposure without changing the canonical portfolio or triggering new historical, latest-price or FX requests.
- Replace a complete snapshot with partial data, inspect unknown exposure/history and revoke eligibility to return to unavailable analysis.

Copilot explanation and lookup results carry deterministic `lookthrough` evidence. Routing permits verified indirect-only tickers for exposure explanation, while ordinary owned-position lookup and scenario validation remain restricted to owned instruments. Accepted exposure is not silently reused as a scenario exposure calculation. Instructions prohibit invented weights, FX, figures, risk or economic currency inference. Deterministic context/router execution is tested; paid model response quality is not claimed as verified.

Native file selection is mocked in Streamlit tests; parsing, review controls, acceptance, selection, navigation and session state are exercised. This is automated interaction acceptance, not manual browser/device visual inspection.

## Test results

Complete regression suite: **261 passed in 170.81 seconds (2:50)**, including all 235 prior tests and 26 new tests (24 deterministic engine/route cases and two Streamlit user workflows). **No regressions detected.** `git diff --check` passed. The focused new engine/UI suite passed **26 tests in 14.54 seconds** before the full regression run. Earlier focused failures caught an attestation identity reference that needed a deep copy and a test fixture that changed only one row's date; both were corrected. No financial methodology changed.

Coverage includes independent arithmetic; overlap/direct-plus-indirect/concentration; 53% partial; unverified 100%; no data; absent/revoked eligibility; leveraged/inverse/derivative/short/cash/nested/unspecified structures; invalid valuation/Trust; stale/different dates; foreign parent canonical values; changed quantities/identity; same names and separate share classes; conflicting typed IDs; aliases/unresolved mappings; invalid replacement; empty/zero equity; Copilot evidence and route boundaries; navigation/provider isolation. Prior financial, Trust, consolidation, constituent review, scenario and Copilot regression suites are included.

## Changed files

- `portfolio_analytics/analytics/lookthrough.py`: read-only exposure, safeguards, identity components, findings and reconciliation.
- `portfolio_analytics/ui/constituents.py`: initial exposure ranking, company drill-down, eligibility controls, evidence and unknown states.
- `portfolio_analytics/ui/discover.py`: accurate direct-analysis scope and Exposure navigation guidance.
- `app.py`: refresh deterministic exposure from accepted state/metadata for Copilot on navigation.
- `portfolio_analytics/ai/copilot.py`: structured exposure evidence in lookup/explanation and interpretation rules.
- `portfolio_analytics/ai/router.py`: validated look-through requests and indirect-only security explanation.
- `tests/test_lookthrough.py`, `tests/test_app_lookthrough.py`: new deterministic and complete persona interaction coverage.
- `tests/test_app_constituents.py`, `tests/test_app_ingestion.py`: update the old Phase 3A message/assertions and exclude the new derived field when checking unchanged prior numerical analytics.
- `docs/PHASE_3B.md`: this report.

## Remaining limitations and manual steps

Users must provide issuer-quality snapshots, declare whole-fund capital weights/completeness, review aliases and attest ordinary equity structure. The app cannot independently authenticate source contents or issuer methodology. Declared ordinary structure cannot expose undisclosed derivatives. Name-based instrument screening is an additional conservative guard, not an exhaustive instrument database.

Source and eligibility metadata remain session-local, as in Phase 3A. A new session requires loading the portfolio and supplementary data again. No issuer feed, economic currency model, issuer-level share-class aggregation, nested recursion or underlying risk/performance/scenario model is introduced. Exact external typed identifiers work across funds; alias review currently targets an accepted direct holding, following Phase 3A's contract. Conservative conflict detection may withhold real aliases until explicitly reviewed.

Reconciliation applies to eligible equity capital, not unsupported assets or cash; the canonical total remains authoritative. Historical constituent estimates do not establish current issuer composition. Source quality, stale prices/FX and incomplete histories remain the existing Trust/metadata limitations and are not repaired by decomposition.

**Stop at Phase 3B. No PR merges or later features without explicit approval.**
