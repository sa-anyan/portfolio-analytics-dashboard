"""Actual upload/review/accept/navigation workflows with deterministic providers."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
import streamlit as st

from tests.test_discover import upload_app, button, workspace
from tests.test_app_ingestion import source, open_ingestion
from tests.test_app_duplicate_review import select_issue, action, save
from tests.test_consolidation import maya_reviewed, james_reviewed, james_fx, JAMES_QUOTES
from portfolio_analytics.input_engine.review import review_view
from portfolio_analytics.core import fx, market_data
from portfolio_analytics.ai.copilot import _explanation_result


def raw_uploads(batch):
    return [source(f['filename'], pd.DataFrame([{k: v for k, v in r.items() if k != '_provenance'} for r in f['raw_records']]).to_csv(index=False).encode()) for f in batch['files']]


def stage(app, monkeypatch, batch):
    files = raw_uploads(batch)
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: files)
    open_ingestion(app)
    button(app, 'Stage account files').click().run(timeout=30)
    next(t for t in app.toggle if t.label == 'Use live market prices').set_value(False).run()
    assert not app.exception


def confirm(app):
    button(app, 'Confirm review decisions').click().run()
    assert review_view(app.session_state['consolidation_batch'])['review_confirmed']


def test_maya_complete_review_consolidation_acceptance_and_all_views(monkeypatch):
    app, calls = upload_app(monkeypatch, {'NVDA': 85, 'B': 15})
    old = deepcopy(app.session_state['portfolio_state'])
    stage(app, monkeypatch, maya_reviewed())
    select_issue(app, 'exact_file_reupload')
    action(app, 'exclude_sources')
    sid = app.session_state['consolidation_batch']['files'][3]['source_id']
    next(m for m in app.multiselect if m.label == 'Source files to exclude').set_value([sid])
    save(app, 'The renamed file is the same original ISA statement.')
    select_issue(app, 'snapshot_conflict')
    action(app, 'choose_snapshot')
    pid = app.session_state['consolidation_batch']['files'][4]['parts'][0]['part_id']
    next(s for s in app.selectbox if s.label == 'Snapshot to retain').set_value(pid)
    save(app, 'Use the corrected ISA snapshot at the same date as pension/GIA.')
    confirm(app)
    button(app, 'Consolidate & validate').click().run(timeout=30)
    assert not app.exception, [e.message for e in app.exception]
    c = app.session_state['consolidation_candidate']
    assert c['ready'], c['errors']
    assert app.session_state['portfolio_state'] == old  # Preview is not acceptance.
    assert c['state']['totals']['equity'] == 4050
    button(app, 'Accept consolidated portfolio').click().run(timeout=30)
    assert not app.exception, [e.message for e in app.exception]
    accepted = deepcopy(app.session_state['portfolio_state'])
    assert accepted['positions'][0]['quantity'] == 37
    assert app.session_state['analytics']['trust_diagnostics']['reporting_currency'] == 'USD'
    fetched = calls.copy()
    workspace(app, 'Deep Analytics')
    assert any('NVDA' in m.value and '100.0%' in m.value for m in app.markdown)
    assert app.session_state['analytics']['risk_contribution']
    button(app, 'Run hypothetical shock').click().run()
    assert app.session_state['latest_scenario']['comparison']['equity_change'] == pytest.approx(-555)
    button(app, 'Prepare a Copilot question').click().run()
    explained = _explanation_result({'scope': 'accepted', 'ticker': None}, {'portfolio_state': accepted, 'analytics': app.session_state['analytics'], 'scenario': None})
    assert explained['analytics']['deep_findings'] == app.session_state['analytics']['deep_findings']
    workspace(app, 'Portfolio dashboard')
    assert calls == fetched and app.session_state['portfolio_state'] == accepted
    open_ingestion(app)
    # Reversing a decision invalidates any previous candidate; accepted analytics remain intact.
    identifier = next(i for i, d in app.session_state['consolidation_batch']['review_decisions'].items() if d['action'] == 'exclude_sources')
    next(s for s in app.selectbox if s.label == 'Decision to reverse').set_value(identifier)
    next(t for t in app.text_input if t.label == 'Reason for reversal').set_value('Check the duplicate again.')
    button(app, 'Reverse review decision').click().run()
    assert not review_view(app.session_state['consolidation_batch'])['review_confirmed']
    assert not any(b.label == 'Accept consolidated portfolio' for b in app.button)
    assert app.session_state['portfolio_state'] == accepted and calls == fetched


def test_james_unpriced_failure_then_valid_acceptance_mixed_book(monkeypatch):
    app, calls = upload_app(monkeypatch, {'A': 85, 'B': 15})
    baseline = deepcopy(app.session_state['portfolio_state'])
    def fx_provider(*a, **k):
        calls['fx'] += 1
        return james_fx(), {'source': 'fixture'}
    def history_provider(tickers, **kwargs):
        calls['history'] += 1
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=81)
        return pd.DataFrame({t: JAMES_QUOTES[t] * np.cumprod(1 + .003 * np.sin(np.arange(81)+i)) for i, t in enumerate(tickers)}, index=dates), {'source': 'fixture'}
    monkeypatch.setattr(fx, 'fetch_fx_history', fx_provider)
    monkeypatch.setattr(market_data, 'fetch_price_history', history_provider)
    st.cache_data.clear()
    stage(app, monkeypatch, james_reviewed())
    duplicate = select_issue(app, 'transaction_duplicate')
    action(app, 'exclude_records')
    view = review_view(app.session_state['consolidation_batch'])
    rid = next(r['record_id'] for r in view['records'] if r['record_id'] in duplicate['record_ids'] and r['source_file'] == 'broker2.csv')
    next(m for m in app.multiselect if m.label == 'Record IDs to exclude').set_value([rid])
    save(app, 'T1 is one confirmed broker execution repeated in the overlapping export.')
    select_issue(app, 'transaction_match_ambiguous')
    action(app, 'keep_both')
    save(app, 'Broker confirms two separate GBX fills.')
    select_issue(app, 'ledger_period_overlap')
    action(app, 'keep_both')
    save(app, 'The repeated transaction is excluded; remaining trades are distinct.')
    confirm(app)
    next(c for c in app.checkbox if c.label.startswith('Broker: complete ledger')).set_value(True)
    for ticker, quote in JAMES_QUOTES.items():
        if ticker != 'UNPRICED':
            next(t for t in app.text_input if t.label == f'{ticker}: common current price').set_value(str(quote))
    button(app, 'Consolidate & validate').click().run(timeout=30)
    c = app.session_state['consolidation_candidate']
    assert not c['ready'] and any(i['code'] == 'price_missing' and 'UNPRICED' in i['holdings'] for i in c['trust']['issues'])
    assert button(app, 'Accept consolidated portfolio').disabled
    assert app.session_state['portfolio_state'] == baseline and review_view(app.session_state['consolidation_batch'])['review_confirmed']
    next(t for t in app.text_input if t.label == 'UNPRICED: common current price').set_value('4')
    button(app, 'Consolidate & validate').click().run(timeout=30)
    c = app.session_state['consolidation_candidate']
    assert c['ready'], c['errors']
    assert c['reconciliation']['passed'] and c['state']['totals']['equity'] == pytest.approx(152.4)
    button(app, 'Accept consolidated portfolio').click().run(timeout=30)
    assert not app.exception, [e.message for e in app.exception]
    accepted = deepcopy(app.session_state['portfolio_state'])
    assert accepted['positions'] and len(accepted['positions']) == 5
    assert accepted['cash']['balances']['EUR'] == 125
    assert app.session_state['analytics']['risk_contribution']
    fetched = calls.copy()
    workspace(app, 'Deep Analytics')
    assert app.session_state['analytics']['deep_findings']
    button(app, 'Run hypothetical shock').click().run()
    button(app, 'Prepare a Copilot question').click().run()
    workspace(app, 'Portfolio dashboard')
    assert app.session_state['portfolio_state'] == accepted and calls == fetched
    open_ingestion(app)
    button(app, 'Reject staged import').click().run()
    assert app.session_state['portfolio_state'] == accepted
    stage(app, monkeypatch, maya_reviewed())
    assert not app.exception and app.session_state['portfolio_state'] == accepted


def test_stale_fx_candidate_withholds_aggregate_and_preserves_accepted_state(monkeypatch):
    app, calls = upload_app(monkeypatch, {'A': 85, 'B': 15})
    old = deepcopy(app.session_state['portfolio_state'])
    monkeypatch.setattr(fx, 'fetch_fx_history', lambda *a, **k: (james_fx().iloc[:1], {'source': 'stale fixture'}))
    st.cache_data.clear()
    from portfolio_analytics.input_engine.batch import ingest_files
    from tests.test_consolidation import TODAY
    batch = ingest_files([('cash.csv', pd.DataFrame({'Account': ['Cash'], 'Ticker': ['CASH'], 'Quantity': [100],
        'Current Price': [1], 'Currency': ['EUR'], 'Valuation Date': [TODAY]}).to_csv(index=False).encode())])
    stage(app, monkeypatch, batch)
    confirm(app)
    button(app, 'Consolidate & validate').click().run(timeout=30)
    c = app.session_state['consolidation_candidate']
    assert not app.exception and not c['ready'] and c['trust']['valuation_status'] == 'blocked'
    assert any(i['code'] == 'fx_stale' for i in c['trust']['issues'])
    assert button(app, 'Accept consolidated portfolio').disabled
    assert not any('equity' in d.value.columns for d in app.dataframe)
    assert not any('account_sum_equity' in str(j.value) for j in app.json)
    assert app.session_state['portfolio_state'] == old
