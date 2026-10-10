from types import SimpleNamespace
import pandas as pd
import pytest
from portfolio_analytics.core import market_data
from portfolio_analytics.input_engine.price_history import spreadsheet_price_history


def test_three_year_yahoo_window_uses_explicit_dates(monkeypatch):
    calls=[]
    frame=pd.DataFrame({'Adj Close':[100.,101.]},index=pd.date_range('2025-01-01',periods=2))
    monkeypatch.setattr(market_data,'_import_yfinance',lambda:SimpleNamespace(download=lambda **kw:(calls.append(kw) or frame)))
    history,_=market_data.fetch_price_history(['NVDA'],period='3y')
    assert len(history)==2
    assert 'period' not in calls[0]
    assert pd.Timestamp(calls[0]['start'])==pd.Timestamp.today().normalize()-pd.DateOffset(years=3)
    assert calls[0]['auto_adjust'] is False


def test_explicit_spreadsheet_history_retains_missing_coverage_and_window():
    today=pd.Timestamp.today().normalize()
    content=f'Date,Ticker,Adjusted Close\n{today.date()},NVDA,100\n{(today-pd.DateOffset(years=6)).date()},NVDA,50\n'.encode()
    history,meta=spreadsheet_price_history(content,'history.csv',['NVDA','MSFT'],'3y')
    assert len(history)==1 and history.NVDA.iloc[0]==100
    assert history.MSFT.isna().all() and meta['missing']==['MSFT']
    with pytest.raises(ValueError,match='Duplicate'):
        spreadsheet_price_history(content+f'{today.date()},NVDA,101\n'.encode(),'history.csv',['NVDA'],'3y')
    with pytest.raises(ValueError,match='requires'):
        spreadsheet_price_history(b'Ticker,Current Price\nNVDA,100\n','snapshot.csv',['NVDA'],'3y')


def test_app_defaults_to_yahoo_selected_window_and_sheet_failure_preserves_state(monkeypatch):
    from tests.test_discover import upload_app, button
    app,_=upload_app(monkeypatch,{'NVDA':85,'MSFT':15},n=100)
    assert next(c for c in app.checkbox if c.label=='Use spreadsheet price history').value is False
    calls=[]
    def history(tickers, **kwargs):
        calls.append(kwargs)
        idx=pd.bdate_range('2025-01-01',periods=100)
        return pd.DataFrame({t:[100+i+(i%3) for i in range(100)] for t in tickers},index=idx),{'source':'Yahoo test'}
    monkeypatch.setattr(market_data,'fetch_price_history',history)
    next(s for s in app.selectbox if s.label=='Historical window').set_value('5y').run()
    button(app,'Parse & Analyse Portfolio').click().run(timeout=30)
    assert not app.exception
    assert calls and calls[0]['period']=='5y'
    accepted=app.session_state['portfolio_state']
    next(c for c in app.checkbox if c.label=='Use spreadsheet price history').check().run()
    button(app,'Parse & Analyse Portfolio').click().run(timeout=30)
    assert not app.exception
    assert app.session_state['portfolio_state']==accepted
    assert len(calls)==1  # malformed statement isn't history; no provider fallback
