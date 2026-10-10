from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from portfolio_analytics.core import market_data, fx
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.analytics.engine import run_analytics, run_account_performance
from portfolio_analytics.ui.history_workspace import select_history,SIMULATE,ACTUAL


def ledger_fixture():
    dates=pd.bdate_range('2025-01-01','2026-10-09')
    prices=pd.DataFrame({'AAA':100+np.arange(len(dates))*.12+np.sin(np.arange(len(dates)))},index=dates)
    raw=b'Date,Type,Ticker,Quantity,Price,Amount,Currency\n2025-01-01,DEPOSIT,,,,1000,USD\n2025-05-01,BUY,AAA,5,110,,USD\n2025-09-01,DEPOSIT,,,,300,USD\n2026-01-05,SELL,AAA,1,130,,USD\n'
    state=build_portfolio_state(parse_upload(raw,'ledger.csv'),latest_prices={'AAA':160})
    analytics=run_analytics(state,prices)
    analytics['actual_performance']=run_account_performance(state,prices)
    return raw,state,analytics,prices


def test_actual_and_simulation_use_distinct_engine_paths_and_window_metrics():
    _,state,analytics,_=ledger_fixture()
    original=deepcopy(state)
    simulated=select_history(state,analytics,SIMULATE,'3y',anchor='2026-10-10')
    actual=select_history(state,analytics,ACTUAL,'3y',anchor='2026-10-10')
    assert simulated['available'] and actual['available']
    assert actual['actual']['accounting_summary']['external_contributions']==1300
    assert actual['metrics']['annual_return']!=pytest.approx(simulated['metrics']['annual_return'])
    shorter=select_history(state,analytics,SIMULATE,'1y',anchor='2026-10-10')
    assert len(shorter['analytics']['series']['portfolio_path'])<len(simulated['analytics']['series']['portfolio_path'])
    assert shorter['metrics']['max_drawdown'] is not None
    state['meta']['path']='holdings'
    assert not select_history(state,analytics,ACTUAL,'3y')['available']
    state['meta']['path']=original['meta']['path']
    assert state==original


def test_partial_coverage_and_gbx_fx_are_not_silently_discarded():
    parsed=parse_upload(b'Ticker,Quantity,Current Price,Currency\nUKTEST,10,200,GBX\nUSTEST,1,100,USD\n','holdings.csv')
    dates=pd.bdate_range('2026-01-01',periods=50)
    rates=pd.DataFrame({'GBP':np.linspace(1.2,1.4,len(dates))},index=dates)
    state=build_portfolio_state(parsed,fx_history=rates)
    prices=pd.DataFrame({'UKTEST':np.linspace(180,200,len(dates)),'USTEST':np.linspace(90,100,len(dates))},index=dates)
    prices.attrs['splits']=pd.DataFrame({'UKTEST':[0.0]},index=dates[:1])
    full=select_history(state,run_analytics(state,prices,fx_history=rates),SIMULATE,'3y')
    assert full['available'] and full['coverage']['common_observations']==49
    partial=select_history(state,run_analytics(state,prices[['USTEST']],fx_history=rates),SIMULATE,'3y')
    assert not partial['available'] and partial['covered']==['USTEST']
    assert partial['coverage']['missing_tickers']==['UKTEST']
    assert not select_history(state,run_analytics(state,prices),SIMULATE,'3y')['available']


def test_streamlit_modes_period_navigation_and_two_panels(monkeypatch):
    raw,_,_,prices=ledger_fixture()
    monkeypatch.setattr(st,'file_uploader',lambda *a,**k:SimpleNamespace(name='ledger.csv',getvalue=lambda:raw))
    requested_periods=[]
    def history(tickers,**kwargs):
        requested_periods.append(kwargs.get('period'))
        result=prices.copy();result.attrs['price_basis']=kwargs.get('price_basis','total_return');result.attrs['splits']=pd.DataFrame()
        return result,{'source':'test'}
    monkeypatch.setattr(market_data,'fetch_price_history',history)
    monkeypatch.setattr(market_data,'fetch_latest_prices',lambda *a:({'AAA':160},{'source':'test'}))
    monkeypatch.setattr(fx,'fetch_fx_history',lambda *a,**k:(pd.DataFrame(),{'source':'test'}))
    st.cache_data.clear()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(b for b in app.button if b.label=='Parse & Analyse Portfolio').click().run(timeout=30)
    assert not app.exception
    state=deepcopy(app.session_state['portfolio_state'])
    mode=lambda:next(c for c in app.button_group if c.key=='overview_history_mode')
    assert mode().value==SIMULATE
    mode().set_value(ACTUAL).run(timeout=30)
    assert not app.exception
    assert any('Use my actual portfolio history' in c.value for c in app.caption)
    next(c for c in app.button_group if c.key=='overview_history_period').set_value('1y').run(timeout=30)
    next(r for r in app.radio if r.label=='Workspace').set_value('Deep Analytics').run()
    assert not app.exception
    assert mode().value==ACTUAL
    assert next(c for c in app.button_group if c.key=='overview_history_period').value=='1y'
    assert any('Actual portfolio equity / growth' in m.value for m in app.markdown)
    assert any('Actual drawdown' in m.value for m in app.markdown)
    assert len(app.get('plotly_chart'))>=6
    perspective=next(r for r in app.radio if r.label=='Deep Analytics view')
    perspective.set_value('Drivers').run()
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Overview').run()
    assert mode().value==ACTUAL
    assert next(c for c in app.button_group if c.key=='overview_history_period').value=='1y'
    assert app.session_state['portfolio_state']==state
    mode().set_value(SIMULATE).run(timeout=30)
    assert not app.exception
    assert any('Hypothetical portfolio growth' in m.value for m in app.markdown)
    for window in ['3y','5y','10y','1y']:
        next(c for c in app.button_group if c.key=='overview_history_period').set_value(window).run(timeout=30)
        assert not app.exception
        assert app.session_state['portfolio_state']==state
    assert {'1y','5y','10y'}.issubset(set(requested_periods))
    st.cache_data.clear()
