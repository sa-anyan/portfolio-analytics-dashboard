"""Visual perspectives on accepted engine output. No alternative accounting engine."""
import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from portfolio_analytics.ui.chart_guard import render_chart
from portfolio_analytics.ui.visuals import (
    _layout, PREMIUM_SEQUENCE, GREEN, RED, performance_figure, drawdown_figure,
    risk_contribution_donut, correlation_heatmap, scenario_comparison_bar,
)
from portfolio_analytics.diagnostics.trust import risk_values_for_display


def positions_with_labels(state, parsed):
    sources = (parsed or {}).get('user_dataset', {}).get('records', [])
    classifications = {}
    for row in sources:
        ticker = str(row.get('Ticker', row.get('Symbol', ''))).strip().upper()
        for label in ('Sector', 'Country'):
            value = str(row.get(label) or 'Unclassified').strip()
            classifications.setdefault((ticker, label), set()).add(value)
    rows = []
    for position in state.get('positions', []):
        row = dict(position)
        for label in ('Sector', 'Country'):
            values = classifications.get((row['ticker'], label), set())
            row[label] = next(iter(values)) if len(values) == 1 else 'Ambiguous' if values else 'Unclassified'
        row['Asset class'] = row.get('asset_class') or 'Unclassified'
        rows.append(row)
    cash = state.get('totals', {}).get('cash')
    if cash is not None and cash != 0:
        cash_sources = [r for r in sources if str(r.get('Ticker', '')).upper() == 'CASH' or str(r.get('Asset Class', '')).lower() == 'cash']
        def cash_label(label):
            values = {str(r.get(label) or 'Unclassified') for r in cash_sources}
            return next(iter(values)) if len(values) == 1 else 'Ambiguous' if values else 'Unclassified'
        rows.append({'ticker': 'CASH', 'asset_class': 'Cash', 'Asset class': 'Cash',
                     'Sector': cash_label('Sector'), 'Country': cash_label('Country'),
                     'signed_market_value': cash, 'exposure': abs(cash), 'unrealised_pnl': None})
    return pd.DataFrame(rows)


def allocation(frame, label, kind='bar'):
    if frame.empty:
        return None
    data = frame.groupby(label, dropna=False, as_index=False).exposure.sum(min_count=1).dropna()
    data = data[data.exposure > 0].sort_values('exposure', ascending=False)
    if data.empty:
        return None
    if kind == 'donut':
        fig = px.pie(data, names=label, values='exposure', hole=.65, color_discrete_sequence=PREMIUM_SEQUENCE)
    elif kind == 'tree':
        fig = px.treemap(data, path=[label], values='exposure', color_discrete_sequence=PREMIUM_SEQUENCE)
    else:
        fig = px.bar(data, x='exposure', y=label, orientation='h', color_discrete_sequence=PREMIUM_SEQUENCE)
    return _layout(fig, f'{label} · gross holdings & cash')


def _plot(fig):
    if fig is not None:
        render_chart(fig, use_container_width=True, config={'displaylogo': False})


def render_intelligence(state, analytics, perspective):
    if not state or not analytics:
        st.info('Upload and analyse a portfolio to explore its investment story.')
        return
    trust = analytics.get('trust_diagnostics', {})
    if trust.get('valuation_status') == 'blocked':
        st.error('Valuation requires attention. Resolve the blocking Trust issues before interpreting portfolio totals.')
        return
    base = state.get('meta', {}).get('base_currency', 'USD')
    frame = positions_with_labels(state, st.session_state.get('parsed'))
    st.caption(f'{base} reporting currency · accepted portfolio · allocations use absolute holding values, including cash. Country labels come from the source, not economic currency exposure.')
    if perspective == 'Overview':
        totals = state.get('totals', {})
        concentration = analytics.get('deep_findings', {}).get('concentration', {})
        available = [('Portfolio value', totals.get('equity'), 'money'),
                     ('Unrealised P&L', totals.get('unrealised_pnl'), 'money'),
                     ('Cash', totals.get('cash'), 'money'),
                     ('Top five concentration', concentration.get('top_five_share'), 'pct')]
        available = [(label, value, kind) for label, value, kind in available if value is not None and math.isfinite(float(value))]
        if available:
            for column, (label, value, kind) in zip(st.columns(len(available)), available):
                display = f'{value:.1%}' if kind == 'pct' else f'{value:,.2f} {base}'
                tone = 'positive' if label == 'Unrealised P&L' and value >= 0 else 'risk' if label == 'Unrealised P&L' else 'neutral'
                column.markdown(f'<div class="pa-finance-kpi" data-tone="{tone}"><div class="pa-finance-kpi-label">{label}</div><div class="pa-finance-kpi-value">{display}</div></div>', unsafe_allow_html=True)
        left, right = st.columns(2)
        with left: _plot(allocation(frame, 'Asset class', 'donut'))
        with right: _plot(allocation(frame, 'Sector', 'tree'))
        left, right = st.columns(2)
        with left: _plot(allocation(frame, 'Country'))
        with right:
            if not frame.empty:
                ranked = frame.loc[frame['Asset class'] != 'Cash'].dropna(subset=['exposure']).nlargest(8, 'exposure')
                _plot(_layout(px.bar(ranked, x='exposure', y='ticker', orientation='h', color_discrete_sequence=PREMIUM_SEQUENCE), 'Largest holdings · gross exposure'))
        findings = analytics.get('deep_findings', {}).get('findings', [])
        for finding in findings[:3]:
            if finding.get('id') == 'risk_unavailable':
                st.caption('Historical risk interpretation needs common price and FX observations.')
            else:
                sentence = str(finding.get('explanation', '')).partition('. ')[0]
                if sentence:
                    st.caption(sentence + ('' if sentence.endswith('.') else '.'))
            with st.expander(str(finding.get('title', 'Finding'))):
                st.write(finding.get('explanation', ''))
                st.write(finding.get('evidence', {}))
                st.caption(finding.get('investigate', ''))
    elif perspective == 'Drivers':
        st.caption('Unrealised P&L at the accepted valuation. These are gains versus supplied cost basis, not time-weighted return attribution.')
        if frame.empty or frame.unrealised_pnl.notna().sum() == 0:
            st.caption('Provide cost basis to explore unrealised gains and losses.')
            return
        dimension = st.radio('Break down by', ['Sector', 'Asset class', 'ticker'], horizontal=True, key='bi_drivers_dimension')
        values = frame.groupby(dimension, as_index=False).unrealised_pnl.sum(min_count=1).dropna()
        values['Direction'] = values.unrealised_pnl.map(lambda x: 'Gain' if x >= 0 else 'Loss')
        fig = _layout(px.bar(values, x=dimension, y='unrealised_pnl', color='Direction', custom_data=[dimension], color_discrete_map={'Gain': GREEN, 'Loss': RED}), f'Unrealised P&L · {base}')
        selected = render_chart(fig, use_container_width=True, on_select='rerun', key='bi_drivers_chart')
        points = selected.selection.points if selected is not None else []
        if points:
            category = points[0].get('customdata', [None])[0]
            if category in frame[dimension].values:
                st.session_state['bi_driver_selection'] = (dimension, category)
        detail = frame.dropna(subset=['unrealised_pnl']).sort_values('unrealised_pnl')
        selection = st.session_state.get('bi_driver_selection')
        if selection and selection[0] == dimension and selection[1] in frame[dimension].values:
            detail = detail.loc[detail[dimension] == selection[1]]
            st.caption(f'Selected {dimension}: {selection[1]}. The waterfall shows this selection only.')
            if st.button('Show all positions', key='bi_clear_driver'):
                st.session_state.pop('bi_driver_selection', None)
                st.rerun()
        if len(detail) > 12:
            others = detail.iloc[5:-5].unrealised_pnl.sum()
            detail = pd.concat([detail.head(5), pd.DataFrame([{'ticker': 'Other positions', 'unrealised_pnl': others}]), detail.tail(5)], ignore_index=True)
        _plot(_layout(go.Figure(go.Waterfall(x=detail.ticker, y=detail.unrealised_pnl, measure=['relative'] * len(detail), increasing={'marker': {'color': GREEN}}, decreasing={'marker': {'color': RED}})), f'Position unrealised P&L · {base}'))
        with st.expander('Position evidence'):
            st.dataframe(frame, hide_index=True, use_container_width=True)
    elif perspective == 'Risk':
        risk = risk_values_for_display(analytics)
        valid = [(name, risk.get(key)) for name, key in [('Annualised volatility', 'annual_volatility'), ('1-day VaR', 'var_value'), ('1-day expected shortfall', 'expected_shortfall_value')] if risk.get(key) is not None]
        if valid:
            for col, (name, value) in zip(st.columns(len(valid)), valid):
                col.metric(name, f'{value:.2%}' if name == 'Annualised volatility' else f'{value:,.2f} {base}')
            _plot(risk_contribution_donut(analytics))
            _plot(correlation_heatmap(analytics))
            st.caption(f"Common historical observations: {analytics.get('meta', {}).get('coverage', {}).get('common_observations', 0)}. Historical estimates are not forecasts.")
        else:
            st.caption('Portfolio risk needs sufficient common price and FX history. Snapshot valuation remains available.')
        if st.session_state.get('latest_scenario'):
            _plot(scenario_comparison_bar(st.session_state.latest_scenario))
        st.button('Investigate scenarios', on_click=_scenario)
    elif perspective == 'Performance & Macro':
        from portfolio_analytics.ui.performance import render_performance
        render_performance(state, analytics, frame)


def render_exposure_visuals(state, analytics):
    if analytics.get('trust_diagnostics', {}).get('valuation_status') == 'blocked':
        st.error('Resolve blocking valuation issues before interpreting exposure.')
        return
    frame = positions_with_labels(state, st.session_state.get('parsed'))
    left, right = st.columns(2)
    with left: _plot(allocation(frame, 'Sector', 'tree'))
    with right: _plot(allocation(frame, 'Asset class', 'donut'))
    result = analytics.get('lookthrough', {})
    securities = result.get('securities', [])
    if securities and result.get('valuation_valid'):
        data = pd.DataFrame([{'Security': r['name'], 'Known exposure': float(r['known_total'])} for r in securities[:12]])
        _plot(_layout(px.bar(data, x='Known exposure', y='Security', orientation='h', color_discrete_sequence=PREMIUM_SEQUENCE), 'Largest known direct + indirect exposures'))
    if not result.get('complete_ranking'):
        st.caption('ETF look-through is incomplete or unavailable. Rankings cover known exposures only; unknown is not zero. Expand sources to investigate.')
    if frame.empty:
        return
    ranked = frame.loc[frame['Asset class'] != 'Cash'].dropna(subset=['exposure']).nlargest(12, 'exposure')
    figure = _layout(px.bar(ranked, x='exposure', y='ticker', orientation='h', custom_data=['ticker']), 'Select a holding to investigate')
    event = render_chart(figure, use_container_width=True, on_select='rerun', key='bi_exposure_chart')
    points = event.selection.points if event is not None else []
    if points:
        ticker = points[0].get('customdata', [None])[0]
        if ticker in frame.ticker.values:
            st.session_state['bi_selected_ticker'] = ticker
    chosen = st.session_state.get('bi_selected_ticker')
    if chosen in frame.ticker.values:
        row = frame.loc[frame.ticker == chosen].iloc[0]
        st.markdown(f"**{chosen}** · {row['Sector']} · {row['Asset class']}")
        st.write({'Market value': row['signed_market_value'], 'Unrealised P&L': row['unrealised_pnl'], 'Reporting currency': state['meta']['base_currency']})


def _scenario():
    st.session_state['workspace'] = 'Portfolio dashboard'
    st.session_state['dashboard_focus'] = 'scenario'
