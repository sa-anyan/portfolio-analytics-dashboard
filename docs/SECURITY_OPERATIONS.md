# Phase 4.2 security and operations contract

Release status is NOT READY pending subsequent integrated acceptance. No deployment is authorised by this document.

## AI access architecture

`AI_ACCESS_POLICY=disabled` is the default, including when an OpenAI key exists. Provider calls, anonymous requests and automatic captions are blocked. Portfolio upload/navigation, Discover, Exposure, Trust, consolidation and deterministic scenarios remain available. A session counter is informational only, never an enforcement mechanism.

The only opt-in policy is `controlled_oidc`. Operators must provision and independently validate all of:

- Streamlit OIDC `[auth]` secrets: trusted issuer metadata, client ID/secret, random cookie secret and exact HTTPS redirect URI. The separate Linux-only `requirements-controlled.lock` pins Authlib and patched cryptography; paid access rejects a missing/outdated auth stack. Default runtime needs neither. Do not expose tokens. Test issuer/audience/signature validation, login, logout and expiration with the actual provider before enabling paid access. The app additionally checks issuer, allowlisted subject and token expiry; email/form fields are never identity.
- `AI_OIDC_ISSUER=https://...`, `AI_ALLOWED_SUBJECTS=subject1,subject2`. Only explicit subjects may use paid AI.
- `AI_DEPLOYMENT_ARCHITECTURE=single_host`. All workers/instances must share the SAME LOCAL durable volume/file. Multi-host, isolated instance volumes, network/NFS SQLite and distributed enforcement are unsupported: keep AI disabled for those deployments. Merely setting the variable is not proof of topology.
- A private persistent directory (0700), and budget database (0600), provisioned once offline using `python scripts/provision_ai_budget.py /absolute/private-volume/requests.sqlite`. Set `AI_BUDGET_DB_PATH` to that exact file. App startup never creates/resets a budget. Do not place it in an ephemeral container/session filesystem. Missing/corrupted/inaccessible/schema-invalid storage fails closed.
- Server-only `OPENAI_API_KEY`, and an explicit `AI_ALLOWED_MODEL` equal to the operator-selected `OPENAI_MODEL` (existing default is unchanged). Use a dedicated project credential with provider-side project restrictions/alerts. No key is accepted from the UI or stored in portfolio/chat state. The SDK endpoint is fixed to OpenAI; no user-selected base URL.

The actor binding is a server-only ContextVar scoped to the explicit Ask action. Every router/explainer request is reserved independently BEFORE transport in a SQLite `BEGIN IMMEDIATE` transaction. Defaults/maximum configurable ceilings:

| Control | Ceiling |
|---|---|
| Per user, UTC calendar day | 10 provider requests; 100,000 reserved tokens |
| Shared across users/processes, UTC calendar month | 100 requests; 1,000,000 reserved tokens |
| Concurrent provider requests | 2 |
| Input including instructions | 32,000 UTF-8 bytes |
| Output per request | 1,000 tokens (captions 900, but automatic UI captions disabled) |
| Question / recent conversation | 1,000 characters; latest six messages, each at most 1,200 characters |
| SDK timeout / automatic retries | 20 seconds / zero retries |

Environment variables `AI_USER_DAILY_REQUESTS`, `AI_USER_DAILY_TOKENS`, `AI_GLOBAL_MONTHLY_REQUESTS`, `AI_GLOBAL_MONTHLY_TOKENS`, `AI_MAX_CONCURRENT_REQUESTS` may only LOWER these ceilings. Invalid values fail closed. Model mismatch and oversized context are refused rather than silently dropping financial evidence. Large portfolios may require a separately reviewed compact context design.

Token reservation uses input UTF-8 bytes + capped output + 512 overhead; this conservatively bounds supported text requests, and reserves failures/timeouts too. These are request/token controls, NOT a validated dollar budget or provider billing guarantee. A typical question uses two provider requests. No automatic retry/refund occurs after an uncertain result. Explicit repeat requests consume another reservation. Model pricing and billing restrictions remain operator/provider responsibilities.

Successful or failed completion releases only concurrency. A crash/failed release leaves the active reservation blocking capacity. There is no lease timer that could release a still-running paid call. After a restart the operator must verify the old process/request has ended before manually setting that reservation inactive; never delete/refund its request/token debit. Preserve the ledger across restarts, backups and upgrades; restoring an old backup can reduce recorded usage and must not be done while AI is enabled. Retain records through their complete applicable UTC month; no automatic deletion is promised. An accurate host clock is required.

Provider exceptions are replaced with fixed messages; no exception body, credentials, portfolio payload or provider request URL is rendered. `OPENAI_LOG=info/debug` is prohibited. Production root/transport logging must also avoid debug payload capture. Explicit user consent is required before sending financial evidence. `store=False` is sent; this is not a promise of zero provider retention. Review applicable OpenAI contractual/data-control settings separately.

## Upload and processing limits

- CSV/XLSX only: 5 MiB per file, 10 files / 20 MiB per staged draft.
- 20,000 rows per file and per account-import draft, 64 columns, 2,048 characters per cell, 100 owned-book security identifiers per table (supplementary constituent inputs may contain up to the 20,000-row limit), 50 accounts per draft. Appended imports retain the same aggregate caps.
- Strict UTF-8 CSV, rectangular rows and unambiguous headers. Invalid control characters and ticker identifiers are rejected. Financial numbers retain existing normalization/accounting.
- XLSX must be a valid package; at most 200 archive members and 25 MiB declared expanded size. Duplicate paths, encrypted archives, XML entities, macros, formulas, external links/connections and embedded objects are rejected. Every worksheet is checked, even though only the first is imported; sparse out-of-range cells/rows are bounded. Upload values-only workbooks. XLSM/legacy XLS are not supported.
- Failed uploads do not replace accepted portfolio state or previous constituent data. Batch-limit failures retain the existing draft; malformed individual sources remain blocked review evidence.
- Tables use a presentation/export copy that prefixes spreadsheet formula-like text with an apostrophe. Numeric short positions and canonical/provenance data are unchanged. Built-in dataframe CSV downloads therefore receive neutralised text.

These per-request limits do not prove immunity to multi-user CPU/memory/network exhaustion. A hosting proxy must enforce aggregate request rate, simultaneous sessions/uploads, body size, timeouts and CPU/memory/process limits. Streamlit transport caps do not replace parser checks. Do not expose the development process directly to the internet.

## Privacy and session lifecycle

Uploads, financial state, reviews, rejected drafts, ETF metadata and chat live in the current Streamlit session; no new portfolio persistence was introduced. Refresh/expiry/server restart may require re-upload/review. A visible banner says this. Session owner changes clear analytical and review state. Authenticated sign-out clears session state and the OIDC cookie; it does not guarantee forensic deletion of host memory, logs, backups or provider data. Clear chat clears local conversation state only. Remove uploads and sign out on shared computers.

Copilot evidence excludes raw uploaded/normalised record tables, account/transaction IDs, consolidation account/provenance stores, source filenames/fingerprints and full accounting trades/logs. Financial holdings, quantities, prices, totals, diagnostic findings, recent chat and scenario evidence can be sent after consent. Do not put secrets/account identifiers in a question. User-provided prose/diagnostic text can still contain identifiers; the UI describes this possibility rather than promising perfect content redaction.

The budget ledger persists only pseudonymous issuer/subject hashes, reservation IDs, dates, token bounds and activity status. Hashes are not anonymous identities. Scope access to the operator and retain/delete according to an approved operational policy; deleting current budget rows while enabled defeats accounting.

Only public market data and synthetic download templates are globally cached, with bounded market-cache entries and TTLs. Accepted holdings, uploads, account decisions, ETF snapshots and Copilot contexts are not globally cached. Cached market-data keys may include requested public symbols; keep cache/host access private.

## Reproducible installation and host safeguards

Supported runtime: Python 3.12.10; reviewed Linux (Ubuntu 24.04 CI) / macOS validation. Controlled OIDC requires Linux and `requirements-controlled.lock`; the patched cryptography release has no wheel for the Intel Mac used for local validation. Install default runtime using `python -m pip install -r requirements.txt` and validation tools using `requirements-dev.lock`; all transitive versions are pinned. Re-run vulnerability checks and full financial/security/browser acceptance for any lock update. Version pins improve reproducibility; artifact hashes/signed mirror and Linux execution are additional release gates, not claimed here.

Streamlit configuration retains the visual theme, disables telemetry and detailed browser tracebacks, enables CORS/XSRF, and caps upload/message transport. Market downloads have explicit 15-second provider timeouts. OpenAI uses a 20-second SDK timeout with no retry; SDK phase timeout is not a whole-process hard deadline. Hosting must enforce bounded process/request lifetime.

CI runs pinned-install checks, full pytest/Streamlit interactions, all 47 independent financial audit checks and dependency advisories, with read-only permissions and immutable action commit references. Branch protection / required checks are EXTERNAL GitHub settings; do not claim them enforced until configured and verified. Workflow presence alone does not prove an actual Actions run passed.

Before controlled deployment, validate HTTPS/proxy WebSocket handling, trusted forwarded headers, secret injection without logging, filesystem permissions/durability, accurate clock, request/session limits, graceful restarts and `/_stcore/health` health response on the actual host. Health only proves process readiness, not market availability, budget integrity, authentication or financial correctness. Paid mode should remain disabled until each dependency is verified. No hosting settings, provider account or paid service were configured by this milestone.

## Audit inventory / boundary

| ID / severity | Disposition, root cause and evidence gate |
|---|---|
| A01 P1 | Fixed in accepted PR #10: ranking index confused underlying identity. Preserve stable-identity AppTest and real-browser journeys. |
| A02 P1 | Fixed in accepted PR #10: snapshot zero invented realised history. Preserve canonical-null and 47 numerical checks. |
| A03 P1 | Addressed here: paid boundary defaults off, OIDC allowlist/consent, shared durable reservations, bounded input/output/retry/errors. Security tests include actual separate processes; live OIDC/hosting validation still required before opt-in. |
| A04 P2 | Parser/aggregate bounds, active-content rejection, safe exports and state preservation. Dedicated abuse and interaction tests. Global resource/rate controls remain hosting requirements. |
| A05 P2 | Pinned CI workflow and independent checks added. Actual Actions results and protected/required review checks remain external acceptance gates; no branch rules changed. |
| A06 P2 | Exact runtime/transitive locks and clean install/vulnerability checks. Linux runner and artifact-hash verification remain release evidence gates. |
| A07 P2 | Accurate session-only warning and user-switch scoping; no secure cross-session save/restore layer. Limitation remains explicit. |
| A08 P2 | Dashboard investigation hierarchy unchanged; outside this security milestone. Retained unresolved. |
| A09 P2 | Raw JSON / fund-selector UX limitation unchanged; outside this milestone. Retained unresolved. |
| A10 P3 | Diagnostic grouping/width deprecation remains unresolved; version locking limits upgrade drift, not a UX fix. |

No original P0 was recorded. No unconditional unresolved P1 in the safe default paid-disabled configuration is knowingly omitted. Enabling paid mode on unverified/ephemeral/multi-host storage, or without validated OIDC, would reintroduce a P1 and is not recommended. Overall release remains NOT READY for unrestricted access.

References: [OpenAI SDK timeout/retry controls](https://developers.openai.com/api/reference/python), [Streamlit verified user claims and expiry](https://docs.streamlit.io/develop/api-reference/user/st.user), [OIDC installation/configuration](https://docs.streamlit.io/develop/api-reference/user/st.login).
