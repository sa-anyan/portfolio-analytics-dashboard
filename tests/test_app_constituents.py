"""Daniel's actual optional Exposure upload/review/accept metadata workflow."""
from copy import deepcopy
from decimal import Decimal

import pandas as pd
import streamlit as st

from tests.test_discover import upload_app, workspace, button
from tests.test_app_ingestion import source
from tests.test_constituents import EXAMPLES


def select(app,label,value):
    next(s for s in app.selectbox if s.label==label).set_value(value).run()
    assert not app.exception, [e.message for e in app.exception]


def exposure(app):
    workspace(app,'Deep Analytics')
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Exposure').run()
    assert not app.exception, [e.message for e in app.exception]


def setup(monkeypatch):
    app,calls=upload_app(monkeypatch,{'VOO':5,'VGT':4,'NVDA':10})
    portfolio=source('daniel.csv',(EXAMPLES/'synthetic_daniel_portfolio.csv').read_bytes())
    files=[]
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k: files if k.get('key')=='constituent_upload' else portfolio)
    next(t for t in app.toggle if t.label=='Use live market prices').set_value(False)
    button(app,'Parse & Analyse Portfolio').click().run(timeout=20)
    assert not app.exception and app.session_state['portfolio_state']['totals']['equity']==6300
    exposure(app)
    return app,calls,files


def stage(app,files,parent,data=None,name=None):
    select(app,'Select parent ETF / fund',parent)
    files[:]=[source(name or f'{parent.lower()}.csv',data or (EXAMPLES/f'synthetic_{parent.lower()}.csv').read_bytes())]
    button(app,'Review constituent data').click().run()
    assert not app.exception, [e.message for e in app.exception]


def accept(app):
    assert not button(app,'Accept constituent data').disabled
    button(app,'Accept constituent data').click().run()
    assert not app.exception, [e.message for e in app.exception]


def test_daniel_two_etf_inputs_dates_coverage_and_canonical_navigation(monkeypatch):
    app,calls,files=setup(monkeypatch)
    state=deepcopy(app.session_state['portfolio_state'])
    analytics=deepcopy(app.session_state['analytics'])
    fetched=calls.copy()
    tables=[d.value for d in app.dataframe if 'Fund' in d.value.columns]
    assert any(set(d['Fund'])=={'VOO','VGT'} and all('unknown coverage' in s for s in d['Status']) for d in tables)
    for parent in ('VOO','VGT'):
        stage(app,files,parent)
        select(app,'Dataset completeness declaration','complete')
        accept(app)
        saved=app.session_state['fund_constituents'][parent]
        assert saved['coverage']['completeness']=='reported_complete'
        assert saved['coverage']['as_of']=='2026-10-09'
        nvda=next(r for r in saved['records'] if r['matched_direct_security']=='NVDA')
        assert Decimal(nvda['weight'])==Decimal('.065' if parent=='VOO' else '.20')
        assert nvda['parent']==parent
    assert len(app.session_state['fund_constituents'])==2
    assert any('Unknown is not zero' in i.value for i in app.warning)
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Discover').run()
    assert not app.exception
    button(app,'Prepare a Copilot question').click().run()
    button(app,'Run hypothetical shock').click().run()
    workspace(app,'Portfolio dashboard')
    assert app.session_state['portfolio_state']==state
    current=deepcopy(app.session_state['analytics']); current.pop('trust_diagnostics'); current.pop('lookthrough', None)
    analytics.pop('trust_diagnostics'); analytics.pop('lookthrough', None)
    assert current==analytics and calls==fetched and app.session_state['copilot_uses']==0
    exposure(app)
    assert set(app.session_state['fund_constituents'])=={'VOO','VGT'} and calls==fetched


def test_invalid_replacement_partial_top_ten_and_version_history(monkeypatch):
    app,calls,files=setup(monkeypatch)
    state=deepcopy(app.session_state['portfolio_state']); fetched=calls.copy()
    stage(app,files,'VOO'); select(app,'Dataset completeness declaration','complete'); accept(app)
    original=deepcopy(app.session_state['fund_constituents'])
    bad=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    bad.loc[0,'Weight']='garbage'
    stage(app,files,'VOO',bad.to_csv(index=False).encode(),'invalid-replacement.csv')
    assert button(app,'Accept constituent data').disabled
    assert app.session_state['fund_constituents']==original
    button(app,'Reject constituent draft').click().run()
    assert not app.exception and app.session_state['fund_constituents']==original
    stage(app,files,'VOO',(EXAMPLES/'synthetic_voo_top_ten.csv').read_bytes(),'top-ten.csv')
    select(app,'Dataset completeness declaration','complete')
    assert any('coverage remains partial' in w.value for w in app.warning)
    assert any('47%' in c.value for c in app.caption)
    accept(app)
    snapshot=app.session_state['fund_constituents']['VOO']
    assert snapshot['coverage']['completeness']=='partial'
    assert Decimal(snapshot['coverage']['reported_weight'])==Decimal('.53')
    assert len(snapshot['records'])==10 and len(snapshot['history'])==1
    assert app.session_state['portfolio_state']==state and calls==fetched


def test_duplicate_exclusion_and_ticker_alias_review_are_evidence_backed(monkeypatch):
    app,calls,files=setup(monkeypatch)
    state=deepcopy(app.session_state['portfolio_state']); fetched=calls.copy()
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame=pd.concat([frame,frame.iloc[:1]],ignore_index=True)
    frame.loc[0,'Constituent Ticker']='NVDA.O'
    stage(app,files,'VOO',frame.to_csv(index=False).encode(),'alias-duplicate.csv')
    assert button(app,'Accept constituent data').disabled
    draft=app.session_state['constituent_draft']
    last=draft['records'][-1]['record_id']
    next(m for m in app.multiselect if m.label=='Constituent records to exclude').set_value([last]).run()
    next(t for t in app.text_input if t.label=='Reason for record exclusions').set_value('Provider repeated the same NVDA constituent.').run()
    rid=draft['records'][0]['record_id']
    select(app,'Constituent identity to review',rid)
    select(app,'Resolved constituent identity','NVDA')
    next(t for t in app.text_input if t.label=='Identity mapping reason').set_value('Confirmed same US67066G1040 ISIN; source uses NVDA.O listing code.')
    button(app,'Save constituent identity decision').click().run()
    assert not app.exception
    select(app,'Dataset completeness declaration','complete')
    accept(app)
    snapshot=app.session_state['fund_constituents']['VOO']
    assert len(snapshot['records'])==3 and len(snapshot['raw_records'])==4
    assert snapshot['decisions']['excluded_records'][last]
    assert snapshot['decisions']['identity_history'][0]['reason']
    first=snapshot['records'][0]
    assert first['identifiers']['ticker']==['NVDA.O'] and first['resolved_identifiers']['ticker']==['NVDA']
    assert app.session_state['portfolio_state']==state and calls==fetched
