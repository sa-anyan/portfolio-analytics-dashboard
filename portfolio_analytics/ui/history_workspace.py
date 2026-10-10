"""Two explicit portfolio history modes, sharing the validated engine."""
import pandas as pd
import streamlit as st
from portfolio_analytics.core import market_data, fx
from portfolio_analytics.core.history_listings import fetch_listing_history, listing_rows
from portfolio_analytics.analytics.engine import returns_from_analytics, run_analytics, run_current_book_risk, _static_portfolio_returns, _drawdown, _series_metrics
from portfolio_analytics.ui.visuals import performance_figure, drawdown_figure, reconstructed_value_figure
from portfolio_analytics.ui.chart_guard import render_chart

SIMULATE="Simulate Today's Holdings"
ACTUAL='Actual Portfolio History'


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def market_window(tickers, period, currencies, base, listings=()):
    prices, metadata = fetch_listing_history(list(tickers), period=period, listings=listings)
    if prices.empty:
        return prices, pd.DataFrame(), metadata
    currency_history, _ = fx.fetch_fx_history(list(currencies), start=(prices.index.min()-pd.Timedelta(days=7)).date().isoformat(), base=base)
    return prices, currency_history, metadata


def select_history(state, analytics, mode, period, *, anchor=None):
    """Window existing base-currency returns or ledger path; no snapshot reconstruction."""
    anchor=pd.Timestamp(anchor).normalize() if anchor is not None else pd.Timestamp.today().normalize()
    start=anchor-pd.DateOffset(years=int(period[:-1]))
    if mode==ACTUAL:
        actual=analytics.get('actual_performance', {})
        if state.get('meta', {}).get('path')!='ledger' or not actual.get('available'):
            return {'available':False,'reason':'Actual history needs dated transactions and cash flows with sufficient price/FX coverage. Purchase dates or current prices alone are insufficient.'}
        frame=pd.DataFrame(actual.get('portfolio_path',[]))
        if frame.empty:
            return {'available':False,'reason':'No actual valuation observations are available.'}
        frame.index=pd.to_datetime(frame.pop('date'))
        frame=frame.loc[(frame.index>=start)&(frame.index<=anchor)].copy()
        daily=pd.to_numeric(frame.get('daily_return'),errors='coerce').dropna()
        if len(daily)<2:
            return {'available':False,'reason':'Insufficient actual observations in this window. Choose a longer period or supply complete dated history.'}
        metrics=_series_metrics(daily)
        wealth=(1+daily).cumprod()
        frame['cumulative_return']=wealth-1
        frame['drawdown']=_drawdown(wealth)
        view={**actual,'metrics':metrics,'portfolio_path':[{'date':d.isoformat(),**r.to_dict()} for d,r in frame.iterrows()]}
        return {'available':True,'actual':view,'analytics':{'series':{'portfolio_path':view['portfolio_path']}},'metrics':metrics}
    returns=returns_from_analytics(analytics)
    if returns.empty:
        returns.index=pd.DatetimeIndex([])
    returns=returns.loc[(returns.index>=start)&(returns.index<=anchor)]
    result=run_current_book_risk(state,returns)
    coverage=result['coverage']
    if not coverage['available'] or not result['risk'].get('compounding_available'):
        covered=[t for t in coverage['required_tickers'] if t not in coverage['missing_tickers']]
        return {'available':False,'reason':coverage.get('reason') or 'Insufficient observations for compounded growth.', 'coverage':coverage,'covered':covered}
    daily=_static_portfolio_returns(returns,pd.Series(result['weights']))
    wealth=(1+daily).cumprod()
    path=pd.DataFrame({'cumulative_return':wealth-1,'drawdown':_drawdown(wealth)})
    return {'available':True,'analytics':{'series':{'portfolio_path':[{'date':d.isoformat(),**r.to_dict()} for d,r in path.iterrows()]}},'metrics':result['risk'],'coverage':coverage}


def _retain_selection(key, fallback):
    if st.session_state.get(key) is None:
        st.session_state[key]=fallback
    preferences=st.session_state.setdefault('overview_history_preferences',{})
    preferences['mode' if key.endswith('_mode') else 'period']=st.session_state[key]


def render_history_workspace(state, analytics):
    st.markdown('**Portfolio history**')
    preferences=st.session_state.setdefault('overview_history_preferences',{})
    mode=st.segmented_control('Historical analysis mode',[SIMULATE,ACTUAL],default=preferences.get('mode',SIMULATE),key='overview_history_mode',label_visibility='collapsed',on_change=_retain_selection,args=('overview_history_mode',SIMULATE)) or SIMULATE
    accepted=st.session_state.get('market_metadata') or {}
    original_period=accepted.get('history',{}).get('period','3y')
    periods=['1y','3y','5y','10y']
    initial_period=preferences.get('period',original_period if original_period in periods else '3y')
    period=st.segmented_control('Historical analysis period',periods,format_func=lambda p:p.upper(),default=initial_period,key='overview_history_period',label_visibility='collapsed',on_change=_retain_selection,args=('overview_history_period',initial_period)) or initial_period
    preferences.update(mode=mode,period=period)
    st.caption(f"Simulate holding today's portfolio over the past {period.upper()}" if mode==SIMULATE else 'Use my actual portfolio history')
    source=analytics
    cache=st.session_state.setdefault('overview_history_results',{})
    spreadsheet='spreadsheet' in str(accepted.get('history',{}).get('source','')).lower()
    force=st.session_state.pop('overview_history_refresh',False)
    if mode==SIMULATE and (period!=original_period or period in cache or force) and not spreadsheet:
        if force:
            cache.pop(period,None)
            market_window.clear()
        if period not in cache:
            try:
                tickers=tuple(p['ticker'] for p in state.get('positions',[]))
                currencies=tuple(sorted({p.get('currency','USD') for p in state.get('positions',[])}|set(state.get('cash',{}).get('balances',{}))))
                prices,currency_history,metadata=market_window(tickers,period,currencies,state['meta']['base_currency'],listing_rows(state.get('inputs',{}).get('user_dataset',{}).get('records',[])))
                cache[period]=run_analytics(state,prices,fx_history=currency_history)
                cache[period]['meta']['listing_evidence']=metadata.get('listing_evidence',[])
            except Exception:
                cache[period]=None
        source=cache[period]
    view=select_history(state,source or {},mode,period)
    columns=st.columns(2)
    titles=['Actual portfolio equity / growth','Actual drawdown'] if mode==ACTUAL else ['Hypothetical portfolio growth','Hypothetical drawdown']
    for index,column in enumerate(columns):
        with column:
            st.markdown('**'+titles[index]+'**')
            if view['available']:
                figure=reconstructed_value_figure(view['actual']) if mode==ACTUAL and index==0 else (performance_figure(view['analytics']) if index==0 else drawdown_figure(view['analytics']))
                figure.update_layout(title=titles[index])
                render_chart(figure,use_container_width=True,config={'displaylogo':False})
            else:
                st.caption(view['reason'])
    if view['available']:
        metrics=view['metrics']
        for column,(label,key) in zip(st.columns(3),[('Annualised historical return','annual_return'),('Annualised volatility','annual_volatility'),('Maximum drawdown','max_drawdown')]):
            value=metrics.get(key)
            if value is not None:
                column.metric(label,f'{value:.2%}')
        rows=view['analytics']['series']['portfolio_path']
        st.caption(f"Observed period: {rows[0]['date'][:10]} to {rows[-1]['date'][:10]} · {len(rows)} observations. Only available observations are used.")
    elif mode==SIMULATE:
        c=view.get('coverage',{})
        st.caption(f"Coverage: {len(view.get('covered',[]))}/{len(c.get('required_tickers',[]))} required assets; {c.get('common_observations',0)} complete common observations. Verify exact Yahoo listing identifiers and quote currency/units, or supply dated adjusted prices. No assets are silently discarded.")
    with st.expander('History assumptions and evidence'):
        if mode==SIMULATE:
            st.caption('Hypothetical current signed weights with daily rebalancing. Adjusted security prices include dividend/split adjustments; aligned FX translates returns into the reporting currency. Base cash earns zero interest; foreign cash includes FX returns. No transaction costs or financing costs. This is not actual account performance.')
        else:
            st.caption('Dated ledger accounting; external cash flows use the existing end-of-day convention. Supplied dividends are included once. Missing transactions, flows, prices or FX limit reconstruction. Equity includes flows; growth/drawdown and returns use the flow-adjusted account return path.')
        evidence=(source or {}).get('meta',{}).get('listing_evidence') or accepted.get('history',{}).get('listing_evidence')
        if evidence:
            st.write(evidence)
        if mode==SIMULATE and not spreadsheet and st.button('Refresh selected history',key='overview_history_refresh_button'):
            st.session_state['overview_history_refresh']=True
            st.rerun()
        if mode==ACTUAL and view['available']:
            render_chart(performance_figure(view['analytics']),use_container_width=True)
