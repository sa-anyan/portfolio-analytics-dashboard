"""Verified exchange-qualified Yahoo history, retained under canonical holding IDs."""
import re
import pandas as pd
from . import market_data


def listing_rows(records):
    return tuple((str(r.get('Ticker','')).upper(),str(r.get('Country','')),str(r.get('Currency','USD')).upper(),str(r.get('Name',''))) for r in records if str(r.get('Ticker','')).upper()!='CASH')


def _candidate(ticker,country,currency,name):
    suffix={'UK':'.L','Germany':'.DE','France':'.PA','Netherlands':'.AS'}.get(country)
    if country=='Global' and currency in {'GBP','GBX'}:
        suffix='.L'
    return ticker+suffix if suffix and currency!='USD' and '.' not in ticker and name else ticker


def _metadata(ticker):
    return market_data._import_yfinance().Ticker(ticker).get_history_metadata()


def _issuer_words(name):
    generic={'plc','inc','corp','corporation','company','group','holding','holdings','nv','se','ag','etf','ucits','ishares','vanguard','core','trust','fund','the','ltd','limited'}
    return {w for w in re.findall(r'[a-z]+',name.lower()) if len(w)>1 and w not in generic}


def fetch_listing_history(tickers, *, listings=(), **kwargs):
    by_ticker={}
    for ticker,country,currency,name in listings:
        row=(country,currency,name)
        if ticker in by_ticker and by_ticker[ticker]!=row:
            raise ValueError(f'{ticker}: conflicting listing declarations require correction.')
        by_ticker[ticker]=row
    candidates={t:_candidate(t,*by_ticker.get(t,('','USD',''))) for t in tickers}
    # Fetch once, then verify qualified candidates before consuming their prices.
    prices,meta=market_data.fetch_price_history(list(dict.fromkeys(candidates.values())),**kwargs)
    original_attrs=prices.attrs.copy()
    prices=prices.copy()
    prices.attrs={}
    output=pd.DataFrame(index=prices.index)
    evidence=[]
    for ticker,candidate in candidates.items():
        if candidate not in prices or prices[candidate].dropna().empty:
            continue
        factor=1.
        if candidate!=ticker or (by_ticker.get(ticker,('','USD',''))[1] in {'GBP','GBX','EUR'}):
            try:
                info=_metadata(candidate)
                expected=by_ticker[ticker][1]
                actual=info.get('currency')
                identity=str(info.get('symbol') or '').upper()==candidate
                words=_issuer_words(by_ticker[ticker][2])
                match=len(words&_issuer_words(str(info.get('longName') or info.get('shortName') or ''))) >= min(2,len(words)) if words else candidate==ticker
                units={('GBp','GBP'):.01,('GBp','GBX'):1.,('GBP','GBX'):100.,('GBP','GBP'):1.,('EUR','EUR'):1.}
                factor=units.get((actual,expected))
                if not identity or not match or factor is None:
                    evidence.append({'ticker':ticker,'candidate':candidate,'verified':False,'reason':'Listing identity, issuer or quote units could not be verified'})
                    continue
                evidence.append({'ticker':ticker,'yahoo_symbol':candidate,'verified':True,'provider_currency':actual,'holding_currency':expected,'price_scale':factor,'issuer':info.get('longName') or info.get('shortName')})
            except Exception:
                evidence.append({'ticker':ticker,'candidate':candidate,'verified':False,'reason':'Listing metadata unavailable'})
                continue
        output[ticker]=prices[candidate]*factor
    output.attrs.update(original_attrs)
    if isinstance(original_attrs.get('splits'),pd.DataFrame):
        output.attrs['splits']=original_attrs['splits'].rename(columns={v:k for k,v in candidates.items()})
    return output,{**meta,'missing':[t for t in tickers if t not in output or output[t].dropna().empty],'listing_evidence':evidence}
