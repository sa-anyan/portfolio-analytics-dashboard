import pandas as pd
import pytest
from portfolio_analytics.core import market_data, history_listings


def test_verified_london_quote_units_and_canonical_identity(monkeypatch):
    prices=pd.DataFrame({'ULVR.L':[4500.,4600.],'IGLT.L':[12.,13.]},index=pd.date_range('2026-01-01',periods=2))
    monkeypatch.setattr(market_data,'fetch_price_history',lambda *a,**k:(prices,{'source':'test'}))
    monkeypatch.setattr(history_listings,'_metadata',lambda t:{'symbol':t,'currency':'GBp' if t=='ULVR.L' else 'GBP','longName':'Unilever PLC' if t=='ULVR.L' else 'iShares Core UK Gilts ETF'})
    result,meta=history_listings.fetch_listing_history(['ULVR','IGLT'],listings=(('ULVR','UK','GBP','Unilever plc'),('IGLT','UK','GBP','iShares Core UK Gilts ETF')),period='3y')
    assert result.ULVR.iloc[0]==45 and result.IGLT.iloc[0]==12
    assert all(r['verified'] for r in meta['listing_evidence'])
    result,_=history_listings.fetch_listing_history(['ULVR'],listings=(('ULVR','UK','GBX','Unilever plc'),),period='3y')
    assert result.ULVR.iloc[0]==4500


def test_wrong_issuer_or_currency_never_substitutes_history(monkeypatch):
    prices=pd.DataFrame({'MC.PA':[100.,101.]},index=pd.date_range('2026-01-01',periods=2))
    monkeypatch.setattr(market_data,'fetch_price_history',lambda *a,**k:(prices,{}))
    monkeypatch.setattr(history_listings,'_metadata',lambda t:{'symbol':t,'currency':'USD','longName':'Moelis Company'})
    result,meta=history_listings.fetch_listing_history(['MC'],listings=(('MC','France','EUR','LVMH'),),period='3y')
    assert 'MC' not in result and meta['missing']==['MC']
    assert meta['listing_evidence'][0]['verified'] is False


def test_unqualified_foreign_symbol_cannot_accept_us_quote(monkeypatch):
    prices=pd.DataFrame({'SAP':[100.,101.]},index=pd.date_range('2026-01-01',periods=2))
    monkeypatch.setattr(market_data,'fetch_price_history',lambda *a,**k:(prices,{}))
    monkeypatch.setattr(history_listings,'_metadata',lambda t:{'symbol':t,'currency':'USD','longName':'SAP SE'})
    result,meta=history_listings.fetch_listing_history(['SAP'],listings=(('SAP','','EUR','SAP SE'),),period='3y')
    assert result.empty and meta['missing']==['SAP']
