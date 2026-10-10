"""Bundled synthetic demos; portfolio acceptance stays in the canonical app path."""
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_analytics.analytics.lookthrough import eligibility_declaration
from portfolio_analytics.input_engine.constituents import identity_catalog, stage_constituents, accept_constituents

DATASETS = {
    'holdings': ('Portfolio Holdings', 'Current positions, concentration and hypothetical history.', 'demo_portfolio_holdings.csv'),
    'transactions': ('Transactions', 'Dated trades, cash flows, accounting and actual history.', 'demo_transactions.csv'),
    'SPY': ('SPY Constituents', 'Partial synthetic SPY weights and hidden equity exposure.', 'constituents/demo_spy_constituents.csv'),
    'QQQ': ('QQQ Constituents', 'Partial synthetic QQQ weights and overlapping exposure.', 'constituents/demo_qqq_constituents.csv'),
}


@lru_cache(maxsize=4)
def dataset(key):
    name, description, filename = DATASETS[key]
    data = (Path(__file__).resolve().parents[2] / 'examples' / filename).read_bytes()
    return {'name': name, 'description': description, 'filename': Path(filename).name,
            'data': data, 'records': len(pd.read_csv(BytesIO(data)))}


def constituent_bundle(state, parsed, parents, *, store=None, declarations=None, as_of=None, maximum=90):
    """Validate each fund separately; publish only after the whole selection succeeds."""
    catalog = identity_catalog(state, parsed=parsed)
    result, eligibility = deepcopy(store or {}), deepcopy(declarations or {})
    for parent in parents:
        source = dataset(parent)
        draft = stage_constituents([(source['filename'], source['data'])], selected_parent=parent)
        result = accept_constituents(result, draft, catalog,
            decisions={'weight_format': 'percentage', 'completeness': 'partial'},
            as_of=as_of, max_age_days=maximum)
        eligibility[parent] = eligibility_declaration(catalog[parent],
            'Explicit synthetic demo: ordinary equity ETF capital-allocation weights; partial coverage, not live issuer holdings.')
    return result, eligibility


def _queue(key, full=False):
    st.session_state['demo_request'] = {'portfolio': key, 'parents': ['SPY', 'QQQ'] if full else []}
    st.session_state['workspace'] = 'Portfolio dashboard'
    st.session_state['input_method'] = 'Upload file'
    st.session_state['portfolio_starting_cash'] = 0.0
    st.session_state['use_live_prices'] = key != 'holdings'
    st.session_state['use_spreadsheet_history'] = False
    st.session_state.pop('demo_notice', None)


def _apply(parents):
    try:
        store, declarations = constituent_bundle(st.session_state.get('portfolio_state'),
            st.session_state.get('parsed'), parents, store=st.session_state.get('fund_constituents'),
            declarations=st.session_state.get('fund_eligibility'),
            maximum=st.session_state.get('constituent_max_age', 90))
        st.session_state.update(fund_constituents=store, fund_eligibility=declarations,
            demo_notice=('success', 'Applied ' + ' + '.join(parents) + ' synthetic partial constituent data. Owned holdings are unchanged.'))
        st.session_state.pop('exposure_selected_identity', None)
        st.session_state.pop('exposure_security', None)
    except ValueError as exc:
        st.session_state['demo_notice'] = ('error', str(exc))


def render_demo_portfolios():
    st.markdown('**Demo Portfolios**')
    st.caption('Synthetic QA data, not investment examples or live issuer constituents. Holdings and Transactions are independent scenarios; constituent coverage is partial.')
    held = {p['ticker'] for p in (st.session_state.get('portfolio_state') or {}).get('positions', [])}
    for column, key in zip(st.columns(4), DATASETS):
        source = dataset(key)
        with column, st.container(border=True):
            st.markdown('**' + source['name'] + '**')
            st.caption(source['description'])
            st.caption(f"{source['records']} records")
            st.download_button('Download CSV', source['data'], file_name=source['filename'],
                mime='text/csv', key='demo_download_' + key, use_container_width=True)
            if key in {'holdings', 'transactions'}:
                st.button('Load ' + ('Holdings' if key == 'holdings' else 'Transactions'),
                    key='demo_load_' + key, on_click=_queue, args=(key,), use_container_width=True)
            else:
                st.button('Apply ' + key, key='demo_apply_' + key, on_click=_apply, args=([key],),
                    disabled=key not in held, use_container_width=True)
    left, right = st.columns(2)
    left.button('Full ETF Analysis Demo', key='demo_full', on_click=_queue, args=('holdings', True),
        help='Loads Portfolio Holdings and applies both synthetic partial constituent datasets.')
    right.button('Apply both constituent datasets', key='demo_apply_both', on_click=_apply,
        args=(['SPY', 'QQQ'],), disabled=not {'SPY', 'QQQ'}.issubset(held))
    if not {'SPY', 'QQQ'}.issubset(held):
        st.caption('Load Holdings, Transactions or Full ETF Analysis Demo to enable the matching constituent buttons.')
    notice = st.session_state.get('demo_notice')
    if notice:
        getattr(st, notice[0])(notice[1])
