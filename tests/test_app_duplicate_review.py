"""Exercise persona review decisions through the actual Streamlit UI."""
import copy

import pandas as pd
import streamlit as st

from tests.test_discover import upload_app, workspace, button
from tests.test_app_ingestion import source, open_ingestion
from tests.test_duplicate_review import holdings, james_batch
from portfolio_analytics.input_engine.review import review_view


def select_issue(app, code):
    view = review_view(app.session_state["consolidation_batch"])
    identifier = next(i["id"] for i in view["issues"] if i["code"] == code)
    next(s for s in app.selectbox if s.label == "Exception to review").set_value(identifier).run()
    assert not app.exception, [e.message for e in app.exception]
    return next(i for i in review_view(app.session_state["consolidation_batch"])["issues"] if i["id"] == identifier)


def action(app, value):
    next(s for s in app.selectbox if s.label == "Review decision").set_value(value).run()
    assert not app.exception, [e.message for e in app.exception]


def save(app, reason):
    next(t for t in app.text_input if t.label == "Reason for decision").set_value(reason)
    button(app, "Save review decision").click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert not app.error, [e.value for e in app.error]


def test_maya_ui_reupload_snapshot_selection_confirmation_and_accepted_state(monkeypatch):
    app, calls = upload_app(monkeypatch, {"NVDA": 85, "B": 15})
    baseline = copy.deepcopy(app.session_state["portfolio_state"])
    metrics = copy.deepcopy(app.session_state["analytics"])
    fetched = calls.copy()
    files = [source("isa.csv", holdings(date="2026-01-01")), source("pension.csv", holdings("Pension", 20)), source("gia.csv", holdings("GIA", 5))]
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: files)
    open_ingestion(app)
    button(app, "Stage account files").click().run()
    assert not app.exception
    assert all(i["classification"] == "legitimate" for i in review_view(app.session_state["consolidation_batch"])["issues"])
    button(app, "Confirm review decisions").click().run()
    assert review_view(app.session_state["consolidation_batch"])["review_confirmed"]
    files[:] = [source("copy-renamed.csv", holdings(date="2026-01-01"))]
    button(app, "Add files to staged import").click().run()
    assert not app.exception and button(app, "Confirm review decisions").disabled
    selected = select_issue(app, "exact_file_reupload")
    assert selected["classification"] == "confirmed_duplicate"
    action(app, "exclude_sources")
    sid = app.session_state["consolidation_batch"]["files"][-1]["source_id"]
    next(m for m in app.multiselect if m.label == "Source files to exclude").set_value([sid])
    save(app, "Same ISA statement re-uploaded under another filename.")
    assert len(review_view(app.session_state["consolidation_batch"])["retained_record_ids"]) == 3
    files[:] = [source("isa-new.csv", holdings(quantity=12, date="2026-02-01"))]
    button(app, "Add files to staged import").click().run()
    conflict = select_issue(app, "snapshot_conflict")
    action(app, "choose_snapshot")
    pid = app.session_state["consolidation_batch"]["files"][-1]["parts"][0]["part_id"]
    next(s for s in app.selectbox if s.label == "Snapshot to retain").set_value(pid)
    save(app, "Use the February ISA snapshot, preserving pension and GIA.")
    button(app, "Confirm review decisions").click().run()
    assert not app.exception
    assert review_view(app.session_state["consolidation_batch"])["review_confirmed"]
    assert button(app, "Parse & Analyse Portfolio").disabled
    workspace(app, "Deep Analytics")
    button(app, "Prepare a Copilot question").click().run()
    workspace(app, "Portfolio dashboard")
    open_ingestion(app)
    assert review_view(app.session_state["consolidation_batch"])["review_confirmed"]
    assert app.session_state["portfolio_state"] == baseline and calls == fetched
    current = copy.deepcopy(app.session_state["analytics"])
    current.pop("trust_diagnostics")
    metrics.pop("trust_diagnostics")
    assert current == metrics and app.session_state["copilot_uses"] == 0


def test_james_ui_record_exclusion_keep_both_overlap_and_reverse(monkeypatch):
    app, calls = upload_app(monkeypatch, {"A": 85, "B": 15})
    baseline, fetched = copy.deepcopy(app.session_state["portfolio_state"]), calls.copy()
    fixtures = james_batch()
    files = [source(f["filename"], pd.DataFrame([{k: v for k, v in r.items() if k != "_provenance"}
             for r in f["raw_records"]]).to_csv(index=False).encode()) for f in fixtures["files"]]
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: files)
    open_ingestion(app)
    button(app, "Stage account files").click().run()
    assert not app.exception
    duplicate = select_issue(app, "transaction_duplicate")
    action(app, "exclude_records")
    rows = review_view(app.session_state["consolidation_batch"])["records"]
    rid = next(r["record_id"] for r in rows if r["record_id"] in duplicate["record_ids"] and r["source_file"] == "broker2.csv")
    next(m for m in app.multiselect if m.label == "Record IDs to exclude").set_value([rid])
    save(app, "T1 is the same confirmed execution in both exports.")
    select_issue(app, "transaction_match_ambiguous")
    action(app, "defer")
    save(app, "Checking whether the GBX fills are separate executions.")
    assert button(app, "Confirm review decisions").disabled
    select_issue(app, "transaction_match_ambiguous")
    action(app, "keep_both")
    save(app, "Broker confirms distinct GBX executions with no unique IDs.")
    select_issue(app, "ledger_period_overlap")
    action(app, "keep_both")
    save(app, "Duplicate T1 excluded; other records are economically distinct.")
    view = review_view(app.session_state["consolidation_batch"])
    assert len(view["retained_record_ids"]) == 8 and len(view["excluded_record_ids"]) == 1
    button(app, "Confirm review decisions").click().run()
    assert review_view(app.session_state["consolidation_batch"])["review_confirmed"]
    next(s for s in app.selectbox if s.label == "Decision to reverse").set_value(duplicate["id"])
    next(t for t in app.text_input if t.label == "Reason for reversal").set_value("Reopen T1 identity review.")
    button(app, "Reverse review decision").click().run()
    assert not app.exception
    view = review_view(app.session_state["consolidation_batch"])
    assert len(view["retained_record_ids"]) == 9 and not view["can_progress"] and not view["review_confirmed"]
    assert app.session_state["portfolio_state"] == baseline and calls == fetched


def test_rejected_draft_history_can_be_restored_without_financial_acceptance(monkeypatch):
    app, calls = upload_app(monkeypatch, {"A": 85, "B": 15})
    baseline, fetched = copy.deepcopy(app.session_state["portfolio_state"]), calls.copy()
    files = [source("invalid.csv", b"")]
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: files)
    open_ingestion(app)
    button(app, "Stage account files").click().run()
    assert button(app, "Confirm review decisions").disabled
    button(app, "Reject staged import").click().run()
    assert app.session_state["consolidation_batch"] is None
    button(app, "Restore most recently rejected draft").click().run()
    assert not app.exception and app.session_state["consolidation_batch"]["summary"]["blocked_files"] == 1
    assert app.session_state["consolidation_batch"]["review_history"][-1]["event"] == "draft_restoration"
    button(app, "Reject staged import").click().run()
    files[:] = [source("valid.csv", holdings())]
    button(app, "Stage account files").click().run()
    assert not app.exception and review_view(app.session_state["consolidation_batch"])["can_progress"]
    assert not app.session_state["consolidation_batch"].get("review_decisions")
    assert app.session_state["portfolio_state"] == baseline and calls == fetched
