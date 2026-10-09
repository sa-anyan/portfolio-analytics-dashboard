# Phase 3A — ETF Constituent Inputs

Baseline: PR #7, `feat/canonical-consolidation` (`25baba8`). PRs #1–#7 remain unmerged. Phase 3B is not started.

## Delivered workflow and scope

Deep Analytics now offers **Discover** and an optional **Exposure** view. Exposure uses:

**Select held fund → upload CSV/XLSX → review exceptions → accept supplementary data.**

Direct portfolio analysis requires no constituent upload. Accepted snapshots live separately in `fund_constituents`; they never enter owned positions, cash, cost basis, valuation, accounting history, concentration or covariance. Existing dashboard, Discover, Trust, scenarios and Copilot continue consuming the accepted canonical portfolio. No new market/provider/AI request is made by constituent input, review, acceptance or navigation.

The file reader reuses existing CSV/XLSX infrastructure, including the first worksheet restriction. No additional dependency, paid data source or accounting engine is introduced. The interface shows source/date/count/coverage/freshness, compact exception groups, one constituent identity review at a time, expandable original records and accepted-snapshot evidence.

## Input and identity contract

Supported fields include parent ticker/ISIN/security identifier/CUSIP/SEDOL; constituent ticker/ISIN/security identifier/CUSIP/SEDOL; company/security name; weight; percentage/decimal format; ISO holdings date; source/provider information; optional instrument class and currency. Raw values, source rows, filenames, SHA-256 fingerprints and decisions remain available. String reading preserves identifiers such as `0007`.

Parents link to accepted securities using exact **typed** identifiers. The catalog uses current positions plus available original accepted source identifiers; consolidated source inspection is restricted to retained partitions. Similar names are never identity evidence. Missing/unmatched/ambiguous parent identifiers require explicit assignment and reason. A file identifying another held fund is blocked until the correct parent is selected or affected records are corrected/excluded. Missing fund classification requires an explicit user fund-identification declaration; it does not change canonical asset classification.

Constituent identifiers are retained even when a security is not directly held. An exact direct-security match is metadata linkage, not additional owned exposure. Conflicting identifiers, overlapping constituent identities and missing identifiers block acceptance until corrected or explicitly reviewed. A listing alias such as `NVDA.O` is preserved and can be explicitly linked to held `NVDA` using security evidence. Names alone never create that link. No company/share-class/issuer aggregation is implemented.

## Deterministic validation and coverage

- Missing, nonnumeric, non-finite, negative or greater-than-100% weights block acceptance. Bare numbers require a stated percentage/decimal format; an explicit percent suffix is unambiguous. File declarations take precedence over a fallback format. Decimal scaling and summation do not rescale weights to 100%; precision up to 64 fractional decimal places is supported. Overweight checks do not round tiny excesses down to 100%.
- Exact duplicates and shared constituent identifiers require explicit record exclusion/correction. Original rows remain stored. Exclusions require affected IDs and reasons.
- Multiple source/date snapshots require explicit selection of one snapshot with a reason. No source files or holdings dates are automatically merged. Invalid/missing/future dates block retained records. Dates use ISO `YYYY-MM-DD`; ambiguous locale dates and Excel numeric serial dates are unsupported.
- Parsing errors and ambiguous recognized headers block the source. Users can correct/re-upload it or explicitly exclude that source with a reason.
- Weight sums above 100% block. Sums below 100% remain partial, even when the user declares the file complete. Invalid/unresolved records make known/unknown coverage unavailable; a valid-weight subtotal is explicitly provisional.
- Exactly 100% is **reported complete** only with an explicit complete-dataset declaration and resolved validation. An unspecified or explicitly partial dataset reporting 100% has unverified coverage; the unknown fraction is not fabricated as zero. A provider-normalised top-holdings file cannot prove whole-fund coverage.
- Missing provider information is a warning; file provenance still exists. Default freshness is **90 calendar days**, configurable in Exposure. Old snapshots can be accepted with visible warnings; freshness is not a guarantee or issuer schedule. Accepted snapshot age is recalculated on navigation without requests.
- Cash, bonds, derivatives and foreign-denominated records are retained as supplementary metadata, with non-equity warnings. Their currency is preserved without FX conversion or inference of economic currency exposure. Weights are not validated as derivative notional/delta exposure.

## Actual sample inputs and outcomes

`phase-3a-sample-inputs.zip` contains the actual synthetic CSV portfolio, two full constituent files, a partial top-ten file, alias/duplicate review input, invalid replacement and a VGT XLSX example. Every example is labelled synthetic; none claims current issuer holdings. `phase-3a-validation-results.json` provides original records, fingerprints, dates, mappings, validation outputs, accepted metadata and review decisions.

Daniel's accepted synthetic portfolio contains VOO5 at USD500, VGT4 at USD700 and NVDA10 at USD100: three actual owned positions and USD6,300 equity. These quantities/values remain unchanged after constituent acceptance.

| Input | Reported constituent weights | Date | Retained count | Result |
|---|---|---|---:|---|
| synthetic_voo.csv | NVDA6.5%, AAPL43.5%, MSFT50% | 2026-10-09 | 3 | 100% reported weight; declared complete; no unresolved identity issues |
| synthetic_vgt.csv | NVDA20%, AAPL30%, MSFT50% | 2026-10-09 | 3 | 100% reported weight; declared complete; no unresolved identity issues |
| synthetic_voo_top_ten.csv | NVDA15%, AAPL10%, MSFT10%, AVGO5%, AMD3%, ORCL3%, ADBE2%, CRM2%, INTC1.5%, CSCO1.5% | 2026-10-09 | 10 | **53% known reported coverage, 47% unknown/unreported**; partial even if declared complete |
| invalid-replacement.csv | NVDA weight `garbage`; other rows retained in evidence | 2026-10-09 | 3 staged | Acceptance blocked; 93.5% valid subtotal is provisional; known/unknown coverage unavailable |
| alias-duplicate.csv | Original NVDA row uses `NVDA.O`; repeated NVDA row creates 106.5% subtotal | 2026-10-09 | 4 staged | Identity conflict/overlap and overweight block acceptance |

Parent VOO matches the accepted VOO ticker/ISIN `US9229083632`; VGT matches VGT/ISIN `US92204A7028`. NVDA constituents in both files retain `US67066G1040` and link to the directly held NVDA security. AAPL/MSFT identifiers remain external security identifiers; similar names do not create matches. The same NVDA security is legitimate across two different fund snapshots; these records are not deleted as duplicates across funds.

Alias review explicitly links `NVDA.O` to NVDA using the matching ISIN, preserving the reported alias and recording the reason. The repeated fourth record is explicitly excluded. The three retained weights sum to exactly 100%; all four original records and source fingerprint remain available. This is snapshot metadata validation, not direct-plus-indirect exposure calculation.

Synthetic snapshots dated 2026-10-09 have age0 at the fixture check date, within the 90-day rule. A January snapshot receives a stale warning. A 100%-subtotal dataset declared partial retains unknown coverage rather than reporting zero missing exposure. Missing source information is visible; incorrect parent assignments, missing IDs, conflicting snapshots and invalid weights/dates block acceptance.

## Daniel acceptance result — Partially satisfied; not fully Passed

Streamlit integration demonstrates actual upload/parser/review/accept controls for both VOO and VGT, stored dates, preserved NVDA weights, coverage display, duplicate exclusion and explicit alias review. Both fund snapshots remain independent of the direct NVDA holding. Before uploads, funds without constituent data display unknown coverage.

Invalid replacement cannot replace the accepted snapshot. A valid partial replacement produces 53%/47% coverage and archives the previous complete snapshot. Review reasons and original evidence survive acceptance. The user can inspect accepted weights/mappings/dates and prior-version count. Rejected drafts retain draft/decision evidence within the session.

Daniel navigates back to Discover/dashboard, prepares a Copilot question and runs an existing scenario without new provider requests. The canonical portfolio and numerical analytics remain unchanged; no paid AI request is sent. The continued look-through-unavailable message is explicit. **He cannot yet obtain combined Nvidia exposure**, so the overall persona remains incomplete until Phase 3B validates that calculation.

## Regression and integration validation

**Final complete regression suite: 235 passed, 0 failed in 163.55 seconds.** All 208 prior tests pass, alongside 24 new deterministic input-validation cases and three new Streamlit workflows. `git diff --check` passed. New tests cover typed identifiers, CSV/XLSX leading zeroes, fund/direct matches, repeated/ambiguous identities, source and snapshot decisions, malformed/empty inputs, invalid dates/weights/formats, exact high-precision overweight checks, coverage declarations, partial top holdings, missing sources, stale data, cash/derivatives/bonds, foreign currency, replacement history, invalid replacement preservation, orphaned/changed parent links, retained consolidated provenance and canonical/navigation isolation. Three Streamlit tests exercise Daniel's upload/accept/navigation, invalid/partial replacement and alias/duplicate review stories.

Transport/providers are mocked in Streamlit tests; parsing and UI controls are real. Native browser file-picker transport, mobile layout, issuer-file authenticity and paid Copilot wording are not independently validated.

## Changed files

- `portfolio_analytics/input_engine/constituents.py`: supplementary source parsing, typed identity catalog, deterministic validation, exact weights/coverage, acceptance/version history and freshness/link checks.
- `portfolio_analytics/ui/constituents.py`: optional Exposure workspace, coverage, declarations, exception/source/identity review, accepted evidence and replacement controls.
- `portfolio_analytics/ui/discover.py`: Discover/Exposure navigation; existing Discover behavior is retained.
- `tests/test_constituents.py`, `tests/test_app_constituents.py`: validation and real Streamlit interactions.
- `examples/constituents/synthetic_daniel_portfolio.csv`, `synthetic_voo.csv`, `synthetic_vgt.csv`, `synthetic_voo_top_ten.csv`: explicitly synthetic examples/template.
- `docs/PHASE_3A.md`: this report.

Existing accounting, consolidation, analytics, Trust, scenario and Copilot engines are unchanged.

## Limitations and remaining manual steps

1. Select the correct held fund, supply original whole-fund weights and date/source information, resolve duplicates/identifiers/snapshots, declare dataset completeness and explicitly accept. Completeness and source claims are user assertions, not independently authenticated issuer evidence.
2. Only exact typed identifier equality and explicit user mappings are supported. No fuzzy name matching, automatic ticker punctuation aliases, issuer/company/share-class consolidation, registry lookup or ISIN checksum/authentication is performed. Generic identifiers remain in their supplied namespace; correction/explicit mapping is needed when namespaces are ambiguous.
3. Unsupported inputs require source correction: ambiguous date formats/serial dates, unrepresented contract IDs, signed/negative or greater-than-100% allocation weights and precision beyond 64 fractional decimal places. Positive non-equity weights remain metadata; company, payoff, notional and economic currency exposure are unknown.
4. Different funds may retain different snapshot dates, displayed independently. No cross-fund calculation mixes those dates in Phase 3A. Multiple snapshots within one fund are explicitly selected rather than merged.
5. Data and version history are session-local. Changing/removing a parent holding retains its supplementary data with an absent/changed-link warning; missing previously known stable identifiers also require linkage review. Current constituent coverage does not imply verified economic or portfolio exposure.
6. No combined direct/indirect exposure, portfolio look-through, cross-ETF aggregation, economic concentration, look-through risk or rebalancing recommendation is implemented. Those require Phase 3B or later approval.

## Stop point

Phase 3A inputs, validation and coverage only. No PRs merged. Request approval before Phase 3B.
