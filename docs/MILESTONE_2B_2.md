# Milestone 2B-2 — Duplicate Detection and Review

Baseline: PR #5 (`b76ef22`). PRs #1–#5 remain unmerged. Financial acceptance and consolidated accounting are not implemented; Milestone 2B-3 requires approval.

## Delivered workflow

Upload → review exceptions → confirm decisions → accept later. Staging starts a new draft; **Add files to staged import** preserves the existing draft and audit history. Original files, raw rows and normalised financial records are retained even when explicitly excluded from the prospective selection. No new valuation, risk, performance, market request or paid AI call is introduced.

The existing 2B-1 source hashes and account assignments underpin deterministic classification:

| Evidence | Classification and behavior |
|---|---|
| Identical SHA-256 bytes, matching known accounts | Confirmed repeated source. Recommend excluding a repeated export; require an explicit decision. Different filenames do not defeat detection. |
| Identical bytes with missing accounts | Possible duplicate plus unresolved account assignment; account identity must be corrected or the source excluded. |
| Explicit distinct account assignments | Legitimate repeated activity. Repeated NVDA or identical transactions/IDs are not duplicates across accounts. |
| Same account, same transaction ID and matching economic fields | Strong confirmed-duplicate evidence; users choose records to exclude or document why IDs were reused for distinct activity. No automatic deletion. |
| Matching transaction fields without stable identity or with different IDs | Possible duplicate; retaining distinct executions requires an explicit reason. |
| Same transaction ID with conflicting details | Identity conflict; inspect source/version evidence and explicitly resolve it. |
| Overlapping ledger date ranges | Possible overlap, not proof of duplicate trades. Review remaining records and acknowledge distinct activity or exclude a source. |
| Multiple snapshots in one account | Explicitly choose one authoritative snapshot. Valuation/as-of dates are shown when supplied; purchase dates/filenames are not used as snapshot dates. |
| Snapshot and ledger for one account | Choose one authoritative input format, preserving other accounts. Reconciliation of both formats is deferred to 2B-3. |
| Same filename with changed bytes | Informational source/version difference. No deduplication merely by filename. |

The UI presents one actionable exception at a time, with expandable source/record evidence, recommendations, exclusion selectors, snapshot/format choices, keep/defer actions and reasons. Legitimate repeats remain informational. Confirming review is blocked while material conflicts, invalidated decisions, unresolved accounts or included parsing errors remain. An empty retained selection cannot be confirmed.

Every exclusion is a reversible selection, not an alteration to economic data. Decision audit evidence includes account, filenames, SHA-256, source/record IDs, action, reason and original evidence. Invalid sources also retain source/raw-row evidence even when they contain no normalised financial records. Account corrections record their rationale and invalidate decisions based on old source/account context. Reversals reopen affected conflicts and remove confirmation. New files revoke previous confirmation. Rejected drafts are archived within the session and can be restored for review.

## Actual persona demonstrations

### Maya — Partially satisfied; not fully Passed

Three accounts with NVDA records (ISA 10, Pension 20, GIA 5) are classified as legitimate separate ownership, with no duplicate blocker. Review confirmation succeeds without manual duplicate decisions.

Re-uploading the ISA export under another filename flags an exact repeated source and a same-account snapshot conflict. Maya explicitly excludes the re-uploaded source: **3 records retained, 1 excluded**, while all 4 original sources remain available.

Adding a later ISA snapshot (12 units) reopens review. She explicitly selects that snapshot, preserving Pension/GIA. Final selection: **3 records retained, 2 excluded** across 5 original sources; ISA 12, Pension 20 and GIA 5 remain separate in provenance. Confirmed review survives Discover/Copilot navigation. Accepted holdings and numerical analytics remain identical to the previous accepted portfolio; no provider requests or AI usage occur.

Her required duplicate-review workflow is demonstrated, but canonical consolidation and account/consolidated financial reconciliation remain unimplemented. Therefore her overall persona is not Passed.

### James — Partially satisfied; not fully Passed

James stages overlapping broker ledgers plus an account containing an unpriced asset and EUR cash. The review identifies a repeated **T1** with stable broker ID, two matching GBX executions without IDs, and overlapping date ranges.

He excludes the T1 copy from the second ledger, defers the unidentified fills while checking them, then explicitly retains both as distinct executions and acknowledges the remaining period overlap. Final selection: **8 of 9 normalised records retained, 1 excluded**. Retained records include the GBP sell/short trade, EUR deposit, both GBX executions, fund trade, unpriced EUR asset and foreign cash. All original rows and source fingerprints remain stored.

He confirms review, then reverses the T1 exclusion: **9 records retained**, duplicate/overlap review reopens and confirmation is cleared. The accepted portfolio and provider request counts remain unchanged throughout. This workflow uses the real upload/parser and Streamlit decision controls with mocked file-picker transport/providers.

His duplicate-review story is demonstrated; financial acceptance, FX/price validation of the selected consolidated book and broker reconciliation remain for 2B-3. Therefore his overall persona is not Passed.

## Validation

**Final full regression suite: 188 passed in 78.51 seconds** (166 existing tests plus 22 new cases, including three Streamlit interaction tests). The focused deterministic review suite also passed: 19 tests in 3.17 seconds. `git diff --check` passed. New tests cover deterministic/read-only classification; exact renamed re-uploads; changed-content filenames; cross-account identical transactions; same-account IDs and missing IDs; conflicting IDs/currencies; explicit snapshot dates and selections; snapshot/ledger format selection; overlapping/non-overlapping date ranges; missing/ambiguous accounts; malformed/empty sources; deferral; documented exclusion/keep decisions; account corrections invalidating review; reversals; confirmation invalidation on added files; archived/restored rejected drafts; and accepted-state/navigation preservation.

A valid trade-only ledger with an empty Amount column exposed a pre-existing pandas string-dtype failure. The existing normaliser now explicitly coerces quantity and monetary subsets to numeric dtypes, including empty trade/cash-flow subsets. This fixes ingestion of trade-only/cash-only provider layouts; no arithmetic rule, accounting, FX, risk or return formula changes. Dedicated mixed-currency/cash-only cases and the full prior financial regression suite validate it.

Streamlit persona tests cover stage/add, source exclusion, snapshot selection, record exclusion, defer/keep, overlap acknowledgement, confirmation, reversal, rejection/restoration, Discover navigation and Copilot prefill. Native browser upload transport, paid AI wording and mobile visual acceptance are not independently tested.

## Changed files

- `portfolio_analytics/input_engine/review.py`: deterministic classification, selected-record view, decision audit, reversal and confirmation gate.
- `portfolio_analytics/input_engine/batch.py`: stable record IDs, non-destructive source append, audited account corrections and confirmation invalidation.
- `portfolio_analytics/input_engine/normalizer.py`: numeric dtype guard for empty string-backed trade/cash-flow subsets.
- `portfolio_analytics/ui/duplicate_review.py`: compact exception evidence, decisions, reversals and confirmation.
- `portfolio_analytics/ui/consolidation.py`: add-files workflow, review integration, account correction reasons and rejected-draft restoration.
- `tests/test_duplicate_review.py`, `tests/test_app_duplicate_review.py`: classification, selection/audit and persona interactions.
- `docs/MILESTONE_2B_2.md`: this report.

## Remaining manual steps and limits

- Correct missing account assignments; inspect broker evidence for ambiguous matches; enter reasons; explicitly choose exclusions or an authoritative snapshot/format; confirm review. A retained ambiguous match is a user assertion, not independently verified broker identity. Reused IDs can be legitimate; “confirmed duplicate” is conditional on supplied identity evidence.
- Matching/re-upload detection covers the active staged sources and additions. Drafts/history are session-local, not a durable cross-session accepted-source registry. Existing accepted single-file portfolios without stored original content fingerprints cannot be compared retrospectively by bytes.
- Date overlaps derive from observed transaction rows, not provider-declared export completeness. Multiple valuation dates within one source are blocked; users must split that source into separate snapshots. Multiple candidate account columns still require source correction.
- Same-account snapshots require a single authoritative selection. Complementary same-account snapshot fragments and snapshot-plus-ledger reconciliation are not automatically combined. Original sources remain retained, so these limitations do not silently narrow the data.
- Excluded/retained counts refer to normalised records. Malformed/unsupported raw rows remain separately identifiable through source/row evidence and cannot silently enter the selection. Excluding an entire bad source is allowed; correcting individual raw rows inside the UI is not implemented.
- The gate is deterministic review eligibility, not financial confidence or financial acceptance. No consolidated equity, currency conversions, risk estimates or historical performance are manufactured in 2B-2. The prior accepted portfolio remains canonical for Discover, Trust, scenarios and Copilot.
- No observed financial regressions; the original financial engines and mathematical methodology are preserved. No broker API, paid dependency or new market request is introduced.

## Stop point

Milestone 2B-2 completes duplicate detection and review only. Milestone 2B-3 must consume a freshly confirmed selection and perform canonical accounting/Trust/reconciliation; it is not started and requires explicit approval. No PRs are merged.
