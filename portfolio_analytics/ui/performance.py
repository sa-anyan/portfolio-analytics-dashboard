"""Instrument comparisons reuse accepted historical returns; rate loading is explicit."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from portfolio_analytics.analytics.engine import returns_from_analytics
from portfolio_analytics.analytics.macro import fetch_us_policy_rate, monthly_comparison, SOURCE
from portfolio_analytics.ui.visuals import _layout, PREMIUM_SEQUENCE, performance_figure, drawdown_figure
from portfolio_analytics.ui.chart_guard import render_chart


@st.cache_data(ttl=86400)
def rate_data():
    return fetch_us_policy_rate()


def render_performance(state, analytics, frame):
    scope = st.radio('Historical perspective', ['Whole portfolio', 'Instrument', 'Sector', 'Asset class'], horizontal=True, key='bi_history_scope')
    if scope == 'Whole portfolio':
        actual = analytics.get('actual_performance', {})
        st.caption('Current-weight historical simulation of today’s positions; daily rebalancing and cash assumptions apply. This is not realised account performance.')
        render_chart(performance_figure(analytics), use_container_width=True, config={'displaylogo': False})
        render_chart(drawdown_figure(analytics), use_container_width=True, config={'displaylogo': False})
        if actual.get('available') and state.get('meta', {}).get('path') == 'ledger':
            with st.expander('Actual dated ledger performance'):
                actual_analytics = {'series': {'portfolio_path': actual['portfolio_path']}}
                render_chart(performance_figure(actual_analytics), use_container_width=True)
        return
    returns = returns_from_analytics(analytics)
    if returns.empty or frame.empty:
        st.caption('Instrument comparisons need price history. Accepted snapshot allocations and P&L remain available.')
        return
    if scope == 'Instrument':
        tickers = st.multiselect('Instruments', sorted(returns.columns), default=[returns.columns[0]], key='bi_history_tickers')
    else:
        dimension = scope
        choices = sorted(frame[dimension].unique())
        selected = st.selectbox(dimension, choices, key='bi_history_category')
        tickers = [ticker for ticker in frame.loc[frame[dimension] == selected, 'ticker'] if ticker in returns.columns]
        st.caption('Separate instrument curves within this category; no invented sector or class account performance.')
    benchmark_options = ['None'] + [t for t in frame.loc[frame['Asset class'].str.upper().eq('ETF'), 'ticker'] if t in returns]
    benchmark = st.selectbox('Compare with an available ETF benchmark', benchmark_options, key='bi_benchmark')
    if benchmark != 'None' and benchmark not in tickers:
        tickers = tickers + [benchmark]
    st.caption('Benchmarks use existing accepted history. ETF returns include fees and tracking differences; they are not a pure market index.')
    if not tickers:
        st.caption('Choose an instrument with available historical data.')
        return
    window = st.radio('Window', ['All available', '1 year', '3 months', '1 month'], horizontal=True, key='bi_history_window')
    selected_returns = returns[tickers].copy()
    if window != 'All available':
        months = {'1 year': 12, '3 months': 3, '1 month': 1}[window]
        selected_returns = selected_returns.loc[selected_returns.index > selected_returns.index.max() - pd.DateOffset(months=months)]
    mode = st.radio('Comparison', ['Indexed growth', 'Daily returns'], horizontal=True, key='bi_history_comparison')
    fig = go.Figure()
    for index, ticker in enumerate(tickers):
        series = selected_returns[ticker].dropna()
        if len(series) < 2:
            continue
        # Each curve has its own disclosed observation span, no filled missing returns.
        y = (1 + series).cumprod() * 100 if mode == 'Indexed growth' else series * 100
        if mode == 'Indexed growth':
            y = pd.concat([pd.Series([100.], index=[series.index[0] - pd.Timedelta(days=1)]), y])
        fig.add_trace(go.Scatter(x=y.index, y=y, name=ticker, line={'color': PREMIUM_SEQUENCE[index % len(PREMIUM_SEQUENCE)]}))
    fig.update_yaxes(title='Index · 100 before first return' if mode == 'Indexed growth' else 'Daily investment return (%)')
    render_chart(_layout(fig, f'Instrument histories · {state["meta"]["base_currency"]}'), use_container_width=True)
    eligible_rate_tickers = [t for t in tickers if t in frame.loc[frame.get('currency', pd.Series(index=frame.index, dtype=str)).eq('USD'), 'ticker'].values]
    if not eligible_rate_tickers:
        st.caption('Policy-rate comparisons currently cover US / USD listings. UK and euro-area policy series are not yet supported.')
        return
    with st.expander('Compare with US / USD policy rates'):
        st.caption('US effective federal funds rate · monthly average of daily rates. Suitable for US monetary-policy questions; this is not a UK or euro-area policy rate.')
        st.markdown(f'[FRED FEDFUNDS provenance]({SOURCE})')
        if st.button('Load US policy-rate history', key='macro_load'):
            try:
                st.session_state['macro_rates'], st.session_state['macro_provenance'] = rate_data()
            except Exception:
                st.info('Policy-rate source unavailable. Instrument analysis remains available; try again later.')
        rates = st.session_state.get('macro_rates')
        if rates is None:
            return
        ticker = st.selectbox('Rate-comparison instrument', eligible_rate_tickers, key='bi_macro_ticker')
        aligned, summary = monthly_comparison(selected_returns[ticker], rates)
        st.caption(f"{summary['observations']} overlapping complete months · {summary['start'] or 'unavailable'} to {summary['end'] or 'unavailable'}")
        if not aligned.empty:
            dual = make_subplots(specs=[[{'secondary_y': True}]])
            dual.add_trace(go.Bar(x=aligned.index, y=aligned['return'] * 100, name=f'{ticker} monthly return (%)'), secondary_y=False)
            dual.add_trace(go.Scatter(x=aligned.index, y=aligned.rate_level, name='US policy rate level (%)'), secondary_y=True)
            dual.update_yaxes(title_text='Monthly investment return (%)', secondary_y=False)
            dual.update_yaxes(title_text='Policy-rate level (%)', secondary_y=True)
            render_chart(_layout(dual, 'Investment returns and rate levels · separate axes'), use_container_width=True)
        if summary['available']:
            st.write(f"Return / rate-change correlation: {summary['correlation']:.2f}")
        else:
            st.caption(summary['reason'])
        st.caption(summary['method'] + ' ' + summary['limitation'])
        st.write(st.session_state['macro_provenance'])
