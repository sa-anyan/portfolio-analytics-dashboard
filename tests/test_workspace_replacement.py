from copy import deepcopy
from io import BytesIO
import pytest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio_analytics.ui.accepted_state import replace_accepted
from portfolio_analytics.ui.chart_guard import has_chart_evidence
from portfolio_analytics.core import market_data, fx


def test_atomic_replacement_clears_all_previous_portfolio_evidence():
    session = {'portfolio_state': {'old': True}, 'scenario_ticker': 'OLD',
               'fund_constituents': {'OLD': {}}, 'exposure_security': 'OLD',
               'accepted_consolidation_batch': {'old': True}, 'workspace': 'Deep Analytics'}
    state = {'totals': {}, 'positions': []}
    replace_accepted(session, {}, state, {}, {})
    assert session['portfolio_state'] == state
    assert session['workspace'] == 'Deep Analytics'
    assert session['accepted_consolidation_batch'] is None
    assert not {'scenario_ticker', 'fund_constituents', 'exposure_security'} & session.keys()


def test_chart_guard_accepts_zero_and_rejects_empty_or_nan():
    assert not has_chart_evidence(go.Figure())
    assert not has_chart_evidence(go.Figure(go.Scatter(y=[None, float('nan')])))
    assert has_chart_evidence(go.Figure(go.Scatter(y=[0, 0])))
    assert has_chart_evidence(go.Figure(go.Bar(x=[1, 2], y=['A', 'B'], orientation='h')))
    assert has_chart_evidence(go.Figure(go.Heatmap(z=[[0, 1], [1, 0]])))


@pytest.mark.parametrize("suffix", ["csv", "xlsx"])
def test_repeated_csv_upload_with_history_failure_and_invalid_replacement(monkeypatch, suffix):
    def file(ticker, quantity):
        frame = pd.DataFrame({'Ticker': [ticker], 'Quantity': [quantity], 'Current Price': [100], 'Currency': ['USD']})
        if suffix == 'xlsx':
            buffer = BytesIO()
            frame.to_excel(buffer, index=False)
            content = buffer.getvalue()
        else:
            content = frame.to_csv(index=False).encode()
        return SimpleNamespace(name='same.' + suffix, getvalue=lambda: content)
    active = [file('NVDA', 85)]
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: active[0])
    def failed(*a, **k):
        raise RuntimeError('offline history')
    monkeypatch.setattr(market_data, 'fetch_price_history', failed)
    monkeypatch.setattr(market_data, 'fetch_latest_prices', lambda tickers: ({t: 100 for t in tickers}, {'source': 'test'}))
    monkeypatch.setattr(fx, 'fetch_fx_history', lambda *a, **k: (pd.DataFrame(), {}))
    st.cache_data.clear()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
    def accept():
        next(b for b in app.button if b.label == 'Parse & Analyse Portfolio').click().run(timeout=30)
        assert not app.exception, [e.message for e in app.exception]
    accept()
    assert app.session_state['portfolio_state']['totals']['equity'] == 8500
    app.session_state['exposure_security'] = 'OLD'
    app.session_state['latest_scenario'] = {'old': True}
    active[0] = file('MSFT', 15)
    accept()
    state = deepcopy(app.session_state['portfolio_state'])
    assert state['totals']['equity'] == 1500
    assert [p['ticker'] for p in state['positions']] == ['MSFT']
    assert app.session_state['latest_scenario'] is None
    assert sum('Missing return/FX coverage' in c.value for c in app.caption)>=2
    assert any('Verify exact Yahoo listing identifiers' in c.value for c in app.caption)
    active[0] = SimpleNamespace(name='same.csv', getvalue=lambda: b'bad\ninvalid\n')
    accept()
    assert app.session_state['portfolio_state'] == state
    assert app.error
    st.cache_data.clear()
