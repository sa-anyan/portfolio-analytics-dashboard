"""Security failures preserve accepted financial state and permit navigation."""
from copy import deepcopy
import streamlit as st
from tests.test_discover import upload_app, button, workspace
from tests.test_app_ingestion import source
from tests.test_app_constituents import setup
from portfolio_analytics.security.uploads import MAX_FILE_BYTES


def test_anonymous_with_server_key_is_still_disabled_and_upload_failures_preserve_state(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fake-secret-do-not-render')
    monkeypatch.delenv('AI_ACCESS_POLICY',raising=False)
    app,calls=upload_app(monkeypatch,{'A':85,'B':15})
    accepted=deepcopy(app.session_state['portfolio_state'])
    assert button(app,'Ask Copilot').disabled
    assert any('disabled by server policy' in i.value for i in app.info)
    assert any('Session-only workspace' in c.value for c in app.caption)
    assert all('fake-secret-do-not-render' not in x.value for x in app.markdown)
    for name,data in [('oversized.csv',b'a'*(MAX_FILE_BYTES+1)),('broken.xlsx',b'PK\x03\x04bad')]:
        monkeypatch.setattr(st,'file_uploader',lambda *a,**k:source(name,data))
        button(app,'Parse & Analyse Portfolio').click().run(timeout=20)
        assert app.error and not app.exception
        assert app.session_state['portfolio_state']==accepted
        workspace(app,'Deep Analytics'); workspace(app,'Portfolio dashboard')
        assert app.session_state['portfolio_state']==accepted


def test_constituent_resource_failure_keeps_accepted_portfolio_and_prior_draft(monkeypatch):
    app,calls,files=setup(monkeypatch);accepted=deepcopy(app.session_state['portfolio_state'])
    files[:]=[source('big.csv',b'x'*(MAX_FILE_BYTES+1))]
    button(app,'Review constituent data').click().run()
    assert app.error and not app.exception
    assert app.session_state['portfolio_state']==accepted


def test_two_actual_streamlit_sessions_do_not_share_accepted_state(monkeypatch):
    a,calls=upload_app(monkeypatch,{'A':85,'B':15});first=deepcopy(a.session_state['portfolio_state'])
    b,_=upload_app(monkeypatch,{'PRIVATE':1});second=deepcopy(b.session_state['portfolio_state'])
    assert first!=second
    assert a.session_state['portfolio_state']==first
    assert [p['ticker'] for p in b.session_state['portfolio_state']['positions']]==['PRIVATE']
    assert not b.session_state['copilot_messages']
