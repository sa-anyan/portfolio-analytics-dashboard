"""Ingestion workspace must preserve accepted portfolio and existing workflows."""
import copy
from pathlib import Path
from types import SimpleNamespace

import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.test_discover import upload_app, workspace, button

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def source(name, content):
    return SimpleNamespace(name=name, getvalue=lambda: content)


def open_ingestion(app):
    next(r for r in app.radio if r.label == "Input method").set_value("Multiple account files").run()
    assert not app.exception


def test_maya_stages_three_files_corrects_accounts_and_preserves_discover_copilot(monkeypatch):
    app, calls = upload_app(monkeypatch, {"NVDA": 85, "B": 15})
    state, analytics = copy.deepcopy(app.session_state["portfolio_state"]), copy.deepcopy(app.session_state["analytics"])
    fetched = calls.copy()
    files = [source("isa.csv", b"Account,Ticker,Quantity\nISA,NVDA,10\n"),
             source("pension.csv", b"Account,Ticker,Quantity\nPension,NVDA,20\n"),
             source("gia.csv", b"Ticker,Quantity\nFUND,5\n")]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: files)
    open_ingestion(app)
    assert button(app, "Parse & Analyse Portfolio").disabled
    button(app, "Stage account files").click().run()
    assert not app.exception
    assert app.session_state["consolidation_batch"]["summary"]["holdings"] == 3
    assert any("Nothing is consolidated or accepted" in i.value for i in app.info)
    next(t for t in app.text_input if "gia.csv" in t.label).set_value("GIA")
    button(app, "Save account assignments").click().run()
    batch = copy.deepcopy(app.session_state["consolidation_batch"])
    assert batch["summary"]["accounts"] == ["GIA", "ISA", "Pension"]
    workspace(app, "Deep Analytics")
    button(app, "Prepare a Copilot question").click().run()
    workspace(app, "Portfolio dashboard")
    open_ingestion(app)
    assert app.session_state["consolidation_batch"] == batch
    assert app.session_state["portfolio_state"] == state
    # Trust re-evaluation is allowed; every numerical engine output remains unchanged.
    current = copy.deepcopy(app.session_state["analytics"])
    current.pop("trust_diagnostics"); current.pop("lookthrough", None)
    analytics.pop("trust_diagnostics"); analytics.pop("lookthrough", None)
    assert current == analytics and calls == fetched
    assert app.session_state["copilot_uses"] == 0


def test_reject_bad_import_then_stage_valid_import_without_replacing_portfolio(monkeypatch):
    files = [source("empty.csv", b"")]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: files)
    st.cache_data.clear()
    app = AppTest.from_file(APP).run()
    open_ingestion(app)
    button(app, "Stage account files").click().run()
    assert not app.exception
    assert app.session_state["consolidation_batch"]["summary"]["blocked_files"] == 1
    button(app, "Reject staged import").click().run()
    assert app.session_state["consolidation_batch"] is None
    files[:] = [source("valid.csv", b"Account,Ticker,Quantity\nISA,A,1\n")]
    button(app, "Stage account files").click().run()
    assert app.session_state["consolidation_batch"]["status"] == "staged"
    assert app.session_state["portfolio_state"] is None and app.session_state["analytics"] is None
