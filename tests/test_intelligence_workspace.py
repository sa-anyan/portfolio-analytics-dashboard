from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio_analytics.core import market_data, fx
from portfolio_analytics.ui.intelligence import positions_with_labels

APP=Path(__file__).resolve().parents[1]/'app.py'
SAMPLE=APP.parent/'examples/demo_portfolio_holdings.csv'


def test_snapshot_ui_views_reuse_state_and_offline_history_cannot_blank_allocations(monkeypatch):
    file=SimpleNamespace(name=SAMPLE.name,getvalue=SAMPLE.read_bytes)
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:file)
    calls={'quotes':0,'history':0,'fx':0}
    def quotes(*a,**k):
        calls['quotes']+=1
        raise AssertionError('Source quotes must not be overwritten')
    def history(*a,**k):
        calls['history']+=1
        raise RuntimeError('offline')
    def currency(*a,**k):
        calls['fx']+=1
        raise RuntimeError('offline')
    monkeypatch.setattr(market_data,'fetch_latest_prices',quotes)
    monkeypatch.setattr(market_data,'fetch_price_history',history)
    monkeypatch.setattr(fx,'fetch_fx_history',currency)
    st.cache_data.clear()
    app=AppTest.from_file(str(APP)).run()
    next(d for d in app.date_input if d.label=='Statement valuation date').set_value(pd.Timestamp('2026-10-09').date())
    next(b for b in app.button if b.label=='Parse & Analyse Portfolio').click().run(timeout=30)
    assert not app.exception, [e.message for e in app.exception]
    state=app.session_state['portfolio_state']
    assert state['totals']['equity']==pytest.approx(439514.35)
    assert state['meta']['base_currency']=='GBP'
    assert calls['quotes']==0
    next(r for r in app.radio if r.label=='Workspace').set_value('Deep Analytics').run()
    assert next(r for r in app.radio if r.label=='Deep Analytics view').value=='Overview'
    assert len(app.get('plotly_chart'))==4
    frame=positions_with_labels(state,app.session_state['parsed'])
    assert frame.Sector.nunique()==13
    for view in ['Drivers','Risk','Performance & Macro','Discover','Overview']:
        next(r for r in app.radio if r.label=='Deep Analytics view').set_value(view).run()
        assert not app.exception, [e.message for e in app.exception]
        assert app.session_state['portfolio_state']==state
    assert calls['history']==1 and calls['fx']==1 and calls['quotes']==0
    assert next(b for b in app.button if b.label=='Ask Copilot').disabled
    # A changed file must not silently inherit the previous statement date.
    next(r for r in app.radio if r.label=='Workspace').set_value('Portfolio dashboard').run()
    file.getvalue=lambda: SAMPLE.read_bytes() + b'\n'
    app.run()
    assert next(d for d in app.date_input if d.label=='Statement valuation date').value is None
    next(b for b in app.button if b.label=='Parse & Analyse Portfolio').click().run()
    assert not app.exception
    assert app.session_state['portfolio_state']==state
    st.cache_data.clear()


def test_historical_sector_class_instrument_and_rate_controls_use_existing_evidence(monkeypatch):
    from tests.test_discover import upload_app, workspace
    from portfolio_analytics.ui import performance
    app, calls = upload_app(monkeypatch, {'HIGHVOL': 50, 'LOWVOL': 50}, n=300)
    workspace(app, 'Deep Analytics')
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Performance & Macro').run()
    fetched=calls.copy()
    for scope in ['Instrument','Sector','Asset class']:
        next(r for r in app.radio if r.label=='Historical perspective').set_value(scope).run()
        assert not app.exception, [e.message for e in app.exception]
        assert app.get('plotly_chart')
    next(r for r in app.radio if r.label=='Historical perspective').set_value('Instrument').run()
    rates=pd.Series([i*i/100 for i in range(20)],index=pd.date_range('2024-12-01',periods=20,freq='MS'))
    monkeypatch.setattr(performance,'rate_data',lambda:(rates,{'source':'synthetic monthly rate fixture'}))
    next(b for b in app.button if b.label=='Load US policy-rate history').click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert len(app.get('plotly_chart'))==2
    assert any('overlapping complete months' in c.value for c in app.caption)
    assert calls==fetched
    st.cache_data.clear()
