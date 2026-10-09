# Phase 2B-1 — Multi-file ingestion and provenance

Baseline: PR #4, commit `69b8884`. PRs #1–#4 remain unmerged. This is the smallest reliable increment requested by the Phase 2B brief; Phase 2B as a whole is not complete.

## Workflows delivered

The dashboard's Input method now offers **Multiple account files**. Users select multiple CSV/XLSX files, stage them together, inspect source/account/format/row summaries, correct account assignments, and reject a staged batch. There is no new portfolio acceptance action in this increment; its analysis button is disabled. The existing single-file and manual workflows remain available.

Each file/account partition uses the existing parser and normaliser. Provenance retains filename, source ID/type, content SHA-256, import session/time, detected format, account hint and explicit corrections. Normalised records retain source/account provenance; original records retain logical source row numbers. No financial engine or valuation formula is introduced.

Explicit account/portfolio columns identify accounts. Missing assignments remain unresolved, never inferred from ticker or filename. Leading zeros and literal account names such as `NA`/`NULL` remain intact. A file can contain multiple accounts; otherwise a single explicit assignment covers its rows. Original account hints survive correction.

Repeated securities, identical files, identical transactions in different accounts, multiple snapshots and snapshot/ledger combinations are retained separately for the next review milestone. Fingerprints are captured, but duplicate decisions are **pending**: no economic record is deleted or automatically consolidated.

Errors in one source do not remove other sources. Parsing exceptions and unsupported records are visible, with original rows retained for inspection. CSV and the first XLSX worksheet are supported through the existing reader. A six-thousand-row import retains every row across three accounts. No market or AI requests are made by staging.

## Acceptance status

| Persona | Actual demonstrated workflow | Status |
|---|---|---|
| Maya | Stages ISA, pension and general-investment files; NVDA quantities 10 and 20 retain distinct ISA/pension provenance. Corrects an unresolved GIA assignment. Navigates to Discover and prepares a Copilot question while the previous accepted portfolio remains unchanged. | **Partially satisfied, not Passed.** Canonical aggregation and detection/review of re-uploads against accepted sources remain for 2B-2/2B-3. Equal fingerprints across repeated imports are tested, but duplicate prevention is not yet implemented. |
| James | Stages equities, fund, an unpriced private asset, a short and EUR cash with USD/GBX/EUR quote units intact. Source evidence is preserved; malformed/unsupported sources remain inspectable. | **Partially satisfied, not Passed.** Trust validation and valuation of the proposed consolidated portfolio are intentionally unavailable until 2B-3. Existing Phase 2A Trust tests still verify unpriced-asset rejection for accepted-analysis workflows. |

No consolidated portfolio, combined account equity, or historical performance is manufactured. New financial reconciliation is **not performed** in 2B-1. Integration assertions compare the prior accepted state and all numerical analytics before/after staging and navigation; they remain identical. Provider request counts remain unchanged and Copilot usage remains zero. Undated holdings retain the existing parser's unavailable account-history capability.

## Validation

`python3 -m pytest -q --tb=short`: **166 passed, 0 failed**, in **48.48 seconds**. Includes all 148 prior cases plus 18 new cases (including parameterisations). `git diff --check` passed. No regression was observed.

Targeted staging/interaction cases cover account corrections, CSV/XLSX, literal/leading-zero identifiers, duplicate-preserving fingerprints, separate-account transactions, same-account snapshot/ledger sources, partial/empty/unsupported files, missing IDs, shorts, foreign cash, retained raw rows, rejection followed by a valid staged import, and a 6,000-row import. Snapshot/overlap inputs are tested for preservation only; decisions about which sources can be combined remain pending.

Streamlit AppTest runs the real app and parser. File-picker transport and the previously accepted portfolio's market providers are mocked. Staging invokes neither those providers nor financial/AI engines. Live browser file selection and mobile visual acceptance were not tested.

## Changed files

- `portfolio_analytics/input_engine/batch.py`: independent file/account parsing, provenance, fingerprints and explicit account corrections.
- `portfolio_analytics/input_engine/parser.py`: optional reader flags to preserve string identifiers in staging; original defaults retained for existing callers.
- `portfolio_analytics/ui/consolidation.py`: lightweight staged source review, account correction and rejection.
- `app.py`: multiple-account input entry and separation from canonical acceptance.
- `tests/test_batch_ingestion.py`, `tests/test_app_ingestion.py`: ingestion and state-preservation regressions.
- `docs/PHASE_2B_1.md`: this milestone record.

## Remaining steps and limitations

1. **2B-2 — Duplicate detection and review:** accepted-source registry, exact re-upload decisions, transaction identity/overlap review, account snapshot conflicts and snapshot/ledger compatibility decisions. Ambiguous identities must require review, not automatic deletion.
2. **2B-3 — Canonical consolidation and persona validation:** feed reviewed records into the existing accounting engine; reconcile account/consolidated values, validate currencies/Trust, and demonstrate complete Maya/James acceptance stories.

Manual steps now: choose the multiple-account input method, stage files, enter/correct missing IDs and save assignments; reject and replace malformed files. Multiple candidate account columns require correcting the source file to retain one authoritative column. XLSX uses its first worksheet; worksheet selection and preservation of Excel numeric display formatting are not added. Normalised records identify their file/account; exact raw row identity is retained separately for future duplicate review.

The draft lives in the Streamlit session and survives workspace navigation, but is not a durable cross-session import archive. Filenames/account IDs are local draft data and are not added to Copilot context. Fingerprints identify exact bytes only; semantic duplicate detection is pending. Starting cash is not inferred or multiplied across files: staging uses zero, and funding/cash decisions belong to canonical consolidation. An unpriced staged asset is retained without any claim that its financial value is reliable.

No broker API, paid integration, new dependency, competing accounting engine or PR merge is introduced. Stop after 2B-1; 2B-2 requires explicit approval.
