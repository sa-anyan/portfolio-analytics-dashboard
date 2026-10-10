# Statement-date correction — 10 October 2026

The reported browser selected 26 October 2023 as the statement valuation date. Trust previously checked dated snapshot observations against today, so historical FX was marked stale and blocked acceptance. Reconciled statements now use their declared valuation date for observation freshness; check date remains separately disclosed. Historical snapshots carry an explicit non-current warning and no claim that the declared date was independently verified. Ordinary current valuations still block genuinely stale FX.

The selected risk-history window is now anchored to the snapshot date: 3y means the three years ending at the statement date. Yahoo uses explicit start/end dates; explicit spreadsheet history uses the same anchor. The history window is distinct from statement valuation and purchase dates.

The representative sample also contains purchases after 26 October 2023. Its statement date cannot precede its supplied purchases. The importer now rejects that inconsistency explicitly before market requests. This is a necessary input correction, not a reason to bypass valuation controls. Use the date of the supplied prices/FX; do not enter the historical analysis start date. The true sample valuation date remains unconfirmed.

Evidence: 48 focused Trust/AppTest/snapshot/history-source tests passed (53.86 seconds); 11 snapshot/source tests passed separately. Additional unit run after diagnostic rejection-priority adjustment is recorded in snapshot-date-final-unit-tests.txt. The complete 345-test regression and 47 numerical checks from the previous follow-up remain the latest full-suite run; this narrow follow-up did not repeat that full suite.

Real browser on the freshly loaded application reproduces the 2023 conflict and displays the specific NVDA purchase-date rejection. Screenshot: snapshot-date-error.png. The older 8511 harness retained imported modules; fresh direct-app preview is at http://127.0.0.1:8512. No production merge/deployment and no paid AI calls.

Changed: diagnostics/trust.py, ui/trust.py, input_engine/snapshot.py, input_engine/price_history.py, app.py, tests/test_reported_snapshot.py and tests/test_intelligence_workspace.py. Canonical financial equations remain unchanged. Missing Yahoo listings still prevent complete portfolio risk, independently of these date checks.
