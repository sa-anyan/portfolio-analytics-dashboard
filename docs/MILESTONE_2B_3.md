# Milestone 2B-3 — Canonical Consolidation and Financial Validation

Baseline: PR #6 (`41495c4`), stacked on PRs #1–#5. No PRs are merged. Phase 3A is not started.

## Delivered behavior

Upload → review exceptions → confirm → consolidate and validate → explicitly accept → analyse.

Each included account's selected normalised records enter the existing `build_portfolio_state` accounting engine independently. The resulting current positions and native-currency cash are composed through the same canonical holdings engine. This prevents a sale in one account from using another account's cost basis. Account holdings quantities, native cash and reporting-currency financial totals reconcile to the constituent states before acceptance. This is a conservation check; the independent arithmetic fixtures below separately test accounting correctness.

Every accepted position retains contributing record/account/source/fingerprint evidence. The accepted batch retains original raw files/rows, review exclusions, reasons and reversal history. Excluded records never enter accounting. A candidate preview is separate from accepted state; failed preparation, rejected drafts, changed settings or review reversal cannot replace the previous canonical portfolio. Acceptance requires the same freshly confirmed review digest and candidate settings. All calculation, valuation and Trust checks complete before replacing accepted dashboard/Discover/scenario/Copilot state.

The global single-file starting-cash control is not applied to consolidated accounts; account cash comes only from reviewed statements or complete ledgers.

The reporting currency remains **USD**, preserving the established financial methodology. Cash stays in its native currency and is revalued at valuation-date FX. Ledger executions and cost basis use event FX; GBX uses GBP/100. P&L follows existing average-cost accounting, gross of fees; fees affect cash/equity. Undated foreign snapshot basis retains the existing current-FX approximation, explicitly warned by Trust.

## Supported inputs and blockers

- A single authoritative snapshot per account, with a common statement valuation date across included snapshots. Unknown dates require explicit user declarations; purchase dates are not valuation dates.
- Multiple reviewed ledger exports per account, with explicit confirmation of complete history from inception, including funding, with zero opening cash and positions. Different inception dates across accounts are supported under that declaration.
- Snapshots in some accounts and complete ledgers in other accounts can establish a combined current book. They cannot establish complete consolidated historical performance.
- Snapshot/ledger overlap or multiple snapshots within one account must be resolved through review first. They are never simply added together.
- Missing opening history/balances, conflicting quote currencies, conflicting supplied marks without a common quote, misaligned snapshot dates, future events, unresolved review and missing/stale FX block acceptance.
- Opposite trades in separate same-account sources at an identical timestamp require a chronological authoritative ledger or distinct execution timestamps; ambiguous order must not determine cost/P&L.
- **Offsetting long and short positions in the same ticker across accounts are blocked.** The existing analytics model has one position per ticker; silently netting would hide gross ownership and concentration. Separate-symbol longs/shorts are supported. Supporting opposite-side same-security ownership safely requires a future account-aware analytics extension.
- Zero-quantity rows and empty/malformed account files remain rejected by existing ingestion validation. Users correct these sources or explicitly exclude them. No invalid row silently enters the book. Cash-only accounts and closed ledger positions are supported.
- Missing current security prices block complete valuation and acceptance. Trust identifies affected securities and valuation/allocation/risk/performance/scenarios. Blocked previews withhold aggregate financial figures and reconciliation sums from the interface. Supplying a valid common quote permits revalidation, retaining original reviewed records.

Snapshots never contribute invented acquisitions/funding to consolidated accounting history. Realised P&L is unavailable whenever any included account is snapshot-based. Complete cost basis and unrealised P&L are unavailable if any open position lacks basis; per-position known values remain inspectable. All-ledger histories can enter the existing historical accounting-performance engine, subject to its existing complete price/FX/history and positive-equity requirements. Current-weight historical simulation remains distinct from actual account performance.

## Exact independent numerical fixtures

Exact CSV inputs are delivered in `milestone-2b-3-input-portfolios.zip`. `milestone-2b-3-reconciliation.json` contains declarations, prices, account states, residuals, review decisions, Trust outputs and Discover findings. All eight delivered CSV files match their retained SHA-256 fingerprints. Fixtures use the execution date as their common snapshot date; supplied prices and constant FX are explicit synthetic assumptions.

### Maya — Passed for the agreed current-book workflow

Five source files:

| Source | Account | NVDA quantity | Entry price USD | Current quote USD | USD cash |
|---|---|---:|---:|---:|---:|
| isa.csv | ISA | 10 | 80 | 100 | 0 |
| pension.csv | Pension | 20 | 60 | 100 | 200 |
| gia.csv | GIA | 5 | 90 | 100 | 50 |
| renamed.csv | ISA | 10 | 80 | 100 | 0 |
| isa-new.csv | ISA | 12 | 80 | 100 | 100 |

Review explicitly excludes `renamed.csv` as the identical repeated statement, then selects `isa-new.csv` instead of the original ISA snapshot. **6 normalised records retained, 2 excluded**. All five original sources remain traceable. The three included accounts retain separate ownership before aggregation.

Independent calculation: quantity `12+20+5=37`; securities `37×100=$3,700`; cash `100+200+50=$350`; equity `$4,050`. Basis `12×80+20×60+5×90=$2,610`; unrealised P&L `$3,700−$2,610=$1,090`. Realised P&L/actual historical performance are unavailable, not zero.

Account equities: ISA `$1,300`, pension `$2,200`, GIA `$550`; sum `$4,050`. Quantity, cash, valuation, exposures, equity, basis and unrealised-P&L residuals are zero within floating-point tolerance. The excluded 20 shares do not increase ownership or wealth.

Streamlit demonstrates actual upload, review exclusions, snapshot selection, confirmation, preview without acceptance, explicit acceptance, dashboard/Trust, Discover concentration and risk contribution, scenario shock and Copilot evidence preparation. Discover reports NVDA as **100% of gross security exposure**, excluding cash; signed equity weight is about **91.36%**. A −15% NVDA scenario changes equity by **−$555** without mutating the accepted state. Navigation does not call market providers. Reversing review after acceptance invalidates the draft candidate while preserving the accepted portfolio.

### James — Passed for the agreed valid current-book workflow

`broker1.csv` contains: US BUY 1 at USD10 on Jan 1, ID T1; UK SELL 2 at GBP20 on Jan 2, ID T2; EUR100 deposit on Jan 3, ID C1. `broker2.csv` contains the repeated T1, two separate PENCE BUY 1 at GBX500 on Jan 2 without IDs, and FUND BUY 1 at USD2 on Jan 4, ID T3. `private.csv` contains an initially unpriced EUR asset, quantity1 with entry EUR3, and EUR25 cash. Snapshot date is explicitly common; ledger history is explicitly confirmed complete from zero balances.

Review excludes only broker2's T1; both GBX executions are explicitly retained as distinct fills and the remaining ledger overlap is acknowledged. **8 normalised records retained, 1 excluded**. No currency, short position, fund, foreign cash or unpriced asset disappears.

The unpriced attempt is blocked, showing `UNPRICED` Trust evidence and affected calculations. Accepted state and confirmed decisions remain intact. After supplying quotes US12, UK18 GBP, PENCE600 GBX, FUND3 USD, UNPRICED4 EUR, with GBP/USD1.25 and EUR/USD1.10, the book validates and is explicitly accepted.

| Security | Quantity | USD signed value | USD absolute basis | USD unrealised P&L |
|---|---:|---:|---:|---:|
| US | 1 | 12.00 | 10.00 | 2.00 |
| UK | −2 | −45.00 | 50.00 | 5.00 |
| PENCE | 2 | 15.00 | 12.50 | 2.50 |
| FUND | 1 | 3.00 | 2.00 | 1.00 |
| UNPRICED | 1 | 4.40 | 3.30 | 1.10 |

Native cash: USD `−10−2=−12`; GBP `+40`; GBX `−1000`; EUR `100+25=125`. USD cash `−12+40×1.25−1000×0.0125+125×1.10=$163`. Signed security value `$−10.60`; **equity `$152.40`**. Long exposure `$34.40`; short exposure `$45`; gross `$79.40`; net `$−10.60`. Basis `$77.80`; unrealised P&L `$11.60`. Realised consolidated P&L/history remain unavailable because Private is a snapshot.

Broker equity `$120.50` plus Private `$31.90` equals `$152.40`. Cash, FX, quantity, exposure, equity, basis and unrealised residuals reconcile. The USD cash debit is preserved, not replaced by invented funding.

Discover identifies UK as the largest gross security exposure (**56.68%**) and, with the documented synthetic price history, the largest positive volatility contributor. Risk contributions, Trust, scenarios and Copilot evidence consume the accepted same five-position state. Section navigation makes no provider requests. A failed valuation is followed by valid revalidation/acceptance in the real Streamlit workflow; draft rejection afterward leaves the accepted book intact.

### Independent accounting checks beyond personas

Two complete USD ledger accounts with different start dates: A deposits100, buys10 X at5, sells4 at8; B deposits100, buys2 X at10, buys1 Y at3 and fully closes Y at4. With X marked8, the independent expected totals are X8, cash163, market value64, equity227, basis50, realised13, unrealised14. The closed Y realised gain survives despite no open Y position. X surviving average basis is6.25; trading all accounts as one ledger would use the wrong account basis.

Short fixture: sell3 Z at10 with fee1, buy1 back at8 with fee0.5, mark remaining −2 at9. Cash20.5; short value−18; equity2.5; absolute basis20; realised2; unrealised2. Gross P&L4 less fees1.5 reconciles to equity2.5.

Large fixture: **12 accounts, 12,000 transactions**, identical broker IDs across distinct accounts, 1,000 distinct dates each, BUY1 X at2 and current quote3. Quantity12,000, cash−24,000, market value36,000, equity12,000. Detection preserves legitimate account activity; consolidation reconciles. Reviewed ISO dates are restored once to canonical timestamp types to avoid repeated pandas format inference; accounting formulas are unchanged.

## Actual Trust outcomes

Maya's valid candidate has no valuation blockers. Warnings include missing price observation dates, unavailable actual historical performance and limited historical tail evidence. James's unpriced candidate is blocked with explicit security evidence. His priced candidate has no current valuation blocker; warning outputs include undated quotes, unavailable actual performance, the established undated foreign basis approximation, synthetic FX history gaps and limited tail evidence. All are retained in the delivered JSON. No numerical confidence score is invented. Missing/stale FX tests block acceptance.

These synthetic FX series intentionally contain sparse observations; their historical gaps are disclosed. Production history precision remains governed by the existing FX adapter and configurable Trust rules.

## Validation

**Final complete regression suite: 208 passed, 0 failed in 145.28 seconds.** This includes all 188 prior tests, 17 new deterministic/numerical cases and three new Streamlit workflow tests. `git diff --check` passed. The 12-account/12,000-transaction upload/review/consolidation fixture took 15.19 seconds in the final run; native large-file UI rendering is not separately benchmarked. Three Streamlit tests cover both complete persona stories and stale-FX aggregate withholding, using mocked file-picker transport and deterministic market providers. No paid API or AI call is made. Native browser file-picker transport, broker export truth/completeness, mobile visual acceptance and paid Copilot wording are not independently validated.

New cases cover empty/zero/cash-only accounts, closed positions, long/shorts, unknown basis, different native currencies, cross-account IDs, snapshot dates, opening-history declarations, different ledger start dates, FX absence/staleness, missing quotes, conflict/reversal/stale-candidate gating, failure followed by valid acceptance, source preservation, canonical navigation and large files. Existing financial, Discover, Trust, scenarios and Copilot suites remain mandatory.

## Changed files

- `portfolio_analytics/input_engine/consolidate.py`: reviewed selection, compatibility gates, canonical per-account composition, provenance, reconciliation and Trust validation.
- `portfolio_analytics/ui/consolidation_acceptance.py`: date/history/quote declarations, candidate preview, blocked/valid results and atomic explicit acceptance.
- `portfolio_analytics/ui/consolidation.py`, `portfolio_analytics/ui/duplicate_review.py`: connect confirmed review to financial preview/acceptance and update milestone messaging.
- `app.py`: reuse cached providers and existing analytics/performance/Trust pipeline for consolidated candidates; render snapshot and ledger evidence.
- `portfolio_analytics/ui/trust.py`: separate candidate methodology download key; reuse diagnostics without duplicate widget keys.
- `portfolio_analytics/core/portfolio_state.py`: retain unavailable complete cost/P&L totals when refreshing consolidated scenarios with missing basis.
- `portfolio_analytics/scenarios/engine.py`: preserve unknown historical realised P&L during scenario trades; known-value arithmetic is unchanged.
- `tests/test_consolidation.py`, `tests/test_app_consolidation.py`: independent numerical fixtures, safety gates and actual persona workflows.
- `docs/MILESTONE_2B_3.md`: this completion report.

## Remaining manual steps and accuracy limits

1. Resolve identity conflicts, provide aligned statement dates and confirm complete ledger opening history. Completeness is an explicit user assertion; exported broker data is not independently authenticated. Nonzero opening balances or missing opening positions require a snapshot or complete ledger, not guessed reconstruction.
2. Supply valid quotes for unpriced assets or refresh market data, and correct missing/stale FX. Missing observation dates remain visible warnings under the existing Trust policy. USD remains the fixed reporting currency; adding arbitrary reporting currencies is outside this milestone.
3. Offsetting same-ticker longs/shorts, misaligned account snapshot dates and ambiguous cross-file opposite-trade order remain explicitly blocked. Multi-format reconciliation within an account is not inferred.
4. Snapshots cannot establish complete realised P&L, sold positions, dividends or actual historical performance. Current-book risk simulation is hypothetical. Unknown basis stays unavailable; undated foreign basis is approximate. Historical FX gaps retain the established no-look-ahead forward-fill methodology and warning rules.
5. Staging/audit/accepted data remain session-local; there is no durable broker sync or cross-session source registry. Original reviewed batches remain available in the accepted session for provenance. There are no additional paid dependencies or market requests during navigation.

Maya and James are Passed for the complete current-book user stories demonstrated above, with unsupported historical and incompatible-input states clearly explained. This is not a claim that every broker format or incomplete history can be accepted.

## Stop point

Milestone 2B-3 only. No PRs merged. Phase 3A requires explicit approval.
