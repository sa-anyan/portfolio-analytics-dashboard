"""Bundled demo imports and real Streamlit switching through canonical acceptance."""
from copy import deepcopy
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from portfolio_analytics.core import market_data, fx
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.ui.demo_portfolios import dataset, constituent_bundle

APP=Path(__file__).resolve().parents[1]/'app.py'


def holdings():
    source=dataset('holdings')
    parsed=parse_upload(source['data'],source['filename'])
    return parsed,build_portfolio_state(parsed)


def test_demo_sources_and_canonical_snapshot():
    assert [dataset(k)['records'] for k in ['holdings','transactions','SPY','QQQ']]==[10,90,10,8]
    parsed,state=holdings()
    assert parsed['classification']=='holdings'
    assert state['totals']['equity']==pytest.approx(152429)
    assert state['totals']['cost_basis']==pytest.approx(128221)
    assert state['totals']['unrealised_pnl']==pytest.approx(24208)
    assert state['totals']['realised_pnl'] is None
    source=dataset('transactions')
    ledger=parse_upload(source['data'],source['filename'])
    marks={p['ticker']:p['current_price'] for p in state['positions']}
    account=build_portfolio_state(ledger,latest_prices=marks)
    assert ledger['classification']=='ledger'
    # Independent raw-source quantity/cash oracle, without invoking accounting helpers.
    rows=pd.read_csv(__import__('io').BytesIO(source['data'])).fillna(0)
    quantities={t:0. for t in marks}; cash=0.
    for _,r in rows.iterrows():
        typ=r['Type']; q=float(r['Quantity']); price=float(r['Price']); fee=float(r['Fees'])
        if typ=='BUY': quantities[r['Ticker']]+=q;cash-=q*price+fee
        elif typ=='SELL': quantities[r['Ticker']]-=q;cash+=q*price-fee
        elif typ in ['DEPOSIT','DIVIDEND']: cash+=float(r['Amount'])
        elif typ in ['WITHDRAWAL','FEE']: cash-=float(r['Amount'])
    assert {p['ticker']:p['quantity'] for p in account['positions']}==quantities
    assert account['totals']['cash']==pytest.approx(cash)
    assert account['totals']['equity']==pytest.approx(cash+sum(quantities[t]*marks[t] for t in marks))


def test_constituents_are_partial_atomic_supplementary_inputs(monkeypatch):
    parsed,state=holdings(); before=deepcopy(state)
    store,decl=constituent_bundle(state,parsed,['SPY','QQQ'],as_of='2026-10-10')
    assert set(store)=={'SPY','QQQ'} and set(decl)=={'SPY','QQQ'}
    assert state==before
    from portfolio_analytics.input_engine.constituents import identity_catalog
    from portfolio_analytics.analytics.lookthrough import calculate_lookthrough
    result=calculate_lookthrough(state,store,identity_catalog(state,parsed=parsed),declarations=decl,as_of='2026-10-10')
    funds={f['parent']:f for f in result['funds']}
    assert float(funds['SPY']['unknown_exposure'])==pytest.approx(49*575*.61)
    assert float(funds['QQQ']['unknown_exposure'])==pytest.approx(49*495*.50)
    nvda=next(r for r in result['securities'] if r['identifiers'].get('ticker')==['NVDA'])
    assert float(nvda['known_total'])==pytest.approx(205*135+49*575*.08+49*495*.10)
    assert float(result['reconciliation']['residual'])==pytest.approx(0)
    assert state==before
    from portfolio_analytics.ui import demo_portfolios as demos
    original=demos.dataset
    monkeypatch.setattr(demos,'dataset',lambda key: {**original(key),'data':b'bad\n'} if key=='QQQ' else original(key))
    with pytest.raises(ValueError): constituent_bundle(state,parsed,['SPY','QQQ'],store=store,as_of='2026-10-10')
    assert state==before and set(store)=={'SPY','QQQ'}


def market_fixture(monkeypatch,missing=False):
    _,state=holdings();marks={p['ticker']:p['current_price'] for p in state['positions']}
    def history(tickers,**kwargs):
        if missing:return pd.DataFrame(),{'source':'offline test'}
        dates=pd.bdate_range('2023-01-03','2026-10-09');x=np.arange(len(dates))
        frame=pd.DataFrame({t:marks[t]*np.exp(.0001*x+.02*np.sin(x/13+i)) for i,t in enumerate(tickers)},index=dates)
        frame.attrs.update(price_basis=kwargs.get('price_basis','total_return'),splits=pd.DataFrame())
        return frame,{'source':'synthetic regression market history'}
    monkeypatch.setattr(market_data,'fetch_price_history',history)
    monkeypatch.setattr(market_data,'fetch_latest_prices',lambda tickers:({t:marks[t] for t in tickers},{'source':'test'}))
    monkeypatch.setattr(fx,'fetch_fx_history',lambda *a,**k:(pd.DataFrame(),{'source':'test'}))
    st.cache_data.clear()


def click(app,label):
    next(b for b in app.button if b.label==label).click().run(timeout=40)
    assert not app.exception,[e.message for e in app.exception]


def test_full_demo_and_portfolio_switching(monkeypatch):
    market_fixture(monkeypatch)
    app=AppTest.from_file(str(APP)).run()
    click(app,'Full ETF Analysis Demo')
    assert app.session_state['portfolio_state']['totals']['equity']==pytest.approx(152429)
    assert set(app.session_state['fund_constituents'])=={'SPY','QQQ'}
    assert len(app.get('plotly_chart'))>=2
    assert app.session_state['overview_history_mode']=="Simulate Today's Holdings"
    first=deepcopy(app.session_state['portfolio_state'])
    app.session_state['latest_scenario']={'stale':True}
    app.session_state['exposure_selected_identity']='stale security'
    app.session_state['copilot_messages']=[{'role':'user','content':'old account'}]
    app.session_state['overview_history_results']={'5y':{'stale':True}}
    click(app,'Load Transactions')
    assert app.session_state['parsed']['classification']=='ledger'
    assert app.session_state['overview_history_mode']=='Actual Portfolio History'
    from portfolio_analytics.ui.history_workspace import select_history,ACTUAL,SIMULATE
    actual=select_history(app.session_state['portfolio_state'],app.session_state['analytics'],ACTUAL,'3y')
    hypothetical=select_history(app.session_state['portfolio_state'],app.session_state['analytics'],SIMULATE,'3y')
    assert actual['available'] and hypothetical['available']
    assert actual['metrics']['annual_return']!=hypothetical['metrics']['annual_return']
    assert app.session_state['portfolio_state']!=first
    assert not app.session_state.get('fund_constituents',{})
    assert app.session_state.get('latest_scenario') is None
    assert not app.session_state.get('copilot_messages')
    assert not app.session_state.get('exposure_selected_identity')
    assert not app.session_state.get('overview_history_results')
    assert len(app.get('plotly_chart'))>=2
    next(r for r in app.radio if r.label=='Workspace').set_value('Deep Analytics').run()
    assert app.session_state['overview_history_mode']=='Actual Portfolio History'
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Discover').run()
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Overview').run()
    assert app.session_state['overview_history_mode']=='Actual Portfolio History'
    click(app,'Apply both constituent datasets')
    assert set(app.session_state['fund_constituents'])=={'SPY','QQQ'}
    click(app,'Load Holdings')
    assert app.session_state['portfolio_state']['totals']['realised_pnl'] is None
    assert app.session_state['overview_history_mode']=="Simulate Today's Holdings"
    assert app.session_state['portfolio_state']['totals']['equity']==pytest.approx(152429)
    assert not app.session_state.get('fund_constituents',{})
    assert len(app.get('plotly_chart'))>=2
    st.cache_data.clear()


def test_missing_history_does_not_fabricate_demo_risk(monkeypatch):
    market_fixture(monkeypatch,missing=True)
    app=AppTest.from_file(str(APP)).run()
    click(app,'Load Holdings')
    assert app.session_state['portfolio_state']['totals']['equity']==pytest.approx(152429)
    assert not app.session_state['analytics']['meta']['coverage']['available']
    st.cache_data.clear()


def test_failed_demo_load_preserves_accepted_account_and_review(monkeypatch):
    market_fixture(monkeypatch)
    app=AppTest.from_file(str(APP)).run()
    click(app,'Full ETF Analysis Demo')
    old=deepcopy(app.session_state['portfolio_state']); funds=deepcopy(app.session_state['fund_constituents'])
    app.session_state['latest_scenario']={'preserved':True}
    monkeypatch.setattr(market_data,'fetch_latest_prices',lambda *a,**k:({}, {'source':'unavailable'}))
    st.cache_data.clear()
    click(app,'Load Transactions')
    assert app.session_state['portfolio_state']==old
    assert app.session_state['fund_constituents']==funds
    assert app.session_state['latest_scenario']=={'preserved':True}
    assert app.session_state['trust_attempt'] is not None
    st.cache_data.clear()
