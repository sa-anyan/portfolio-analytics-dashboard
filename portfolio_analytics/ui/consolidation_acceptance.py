"""Reviewable financial candidate, followed by explicit atomic acceptance."""
from copy import deepcopy

import pandas as pd
import streamlit as st

from portfolio_analytics.input_engine.consolidate import selected_inputs
from portfolio_analytics.input_engine.review import review_view, _hash
from portfolio_analytics.ui.trust import render_trust
from portfolio_analytics.ui.insights import fallback_insights


def accept_candidate(session, batch, candidate, request_key):
    view = review_view(batch)
    if not view['review_confirmed'] or not candidate.get('ready') or candidate['review_digest'] != view['digest'] or candidate.get('request_key') != request_key:
        raise ValueError('Consolidation is stale, unconfirmed or failed validation. Reconfirm and consolidate before acceptance.')
    # All work is complete before replacing the canonical accepted state.
    updates = {'parsed': deepcopy(candidate['parsed']), 'portfolio_state': deepcopy(candidate['state']),
               'analytics': deepcopy(candidate['analytics']), 'market_metadata': deepcopy(candidate['market_metadata']),
               'latest_scenario': None, 'trust_attempt': None, 'copilot_messages': [],
               'copilot_insights': fallback_insights(candidate['state'], candidate['analytics'])}
    accepted = deepcopy(batch)
    accepted['canonical_acceptance'] = 'accepted'
    accepted.setdefault('review_history', []).append({'event': 'financial_acceptance', 'review_digest': view['digest'],
        'retained_record_ids': view['retained_record_ids'], 'excluded_record_ids': view['excluded_record_ids'],
        'reason': 'User accepted the financially validated canonical consolidation.', 'reconciliation': candidate['reconciliation']})
    updates['accepted_consolidation_batch'] = accepted
    for key, value in updates.items():
        session[key] = value


def render_acceptance(batch, prepare, *, use_live_prices, history_period, policy):
    view, records = selected_inputs(batch)
    if not view['review_confirmed']:
        return
    st.markdown('**Consolidate and validate**')
    st.caption('Reporting currency: USD. Complete ledgers are accounted independently before positions and native cash are combined. Snapshots establish current holdings only.')
    valuation = st.date_input('Common valuation date', key=f"consolidation_date_{batch['session_id']}")
    accounts = sorted({r['account_id'] for r in records})
    declarations = {'valuation_date': valuation.isoformat(), 'accounts': {}}
    with st.expander('Account history and statement-date declarations', expanded=True):
        for account in accounts:
            ledger = any(r['kind'] != 'holdings' for r in records if r['account_id'] == account)
            if ledger:
                complete = st.checkbox(f'{account}: complete ledger from inception, zero opening cash and positions', key=f"complete_{batch['session_id']}_{account}")
                declarations['accounts'][account] = {'complete_from_zero': complete}
            else:
                confirmed = st.checkbox(f'{account}: any undated snapshot is a statement at the common valuation date', key=f"snapshot_{batch['session_id']}_{account}")
                declarations['accounts'][account] = {'snapshot_date': valuation.isoformat() if confirmed else None}
        st.caption('Do not confirm missing history. Supply a current holdings statement instead. Explicit statement dates in files must agree; purchase dates are not statement dates.')
    overrides = {}
    with st.expander('Common security quotes (optional, in each listing’s trading currency)'):
        tickers = sorted({r['data'].get('Ticker') for r in records if r['data'].get('Ticker') and r['data'].get('Ticker') != 'CASH' and str(r['data'].get('Asset Class', '')).lower() != 'cash'})
        for ticker in tickers:
            text = st.text_input(f'{ticker}: common current price', key=f"quote_{batch['session_id']}_{ticker}")
            if text.strip():
                try:
                    number = float(text)
                    if number <= 0 or not pd.notna(number) or number == float('inf'):
                        raise ValueError()
                    overrides[ticker] = number
                except ValueError:
                    st.error(f'{ticker}: enter a finite positive price.')
                    return
        st.caption('Quotes are user-supplied assumptions at the common date. GBX quotes are pence. Live quotes, when enabled, take precedence.')
    request_key = _hash([view['digest'], declarations, overrides, use_live_prices, history_period, policy.__dict__])
    if st.button('Consolidate & validate', type='primary'):
        with st.spinner('Accounting for each account and reconciling the combined portfolio...'):
            candidate = prepare(batch, declarations, overrides, use_live_prices, history_period, policy)
        candidate['request_key'] = request_key
        st.session_state['consolidation_candidate'] = candidate
    candidate = st.session_state.get('consolidation_candidate')
    if not candidate or candidate.get('request_key') != request_key or candidate.get('review_digest') != view['digest']:
        st.caption('Consolidate this confirmed selection to preview its financial validation. Changes require a fresh preview.')
        return
    st.caption(f"Included accounts: {', '.join(accounts)}. {len(candidate['accounts'])} accounted accounts · {len(candidate['retained_record_ids'])} retained records · {len(candidate['excluded_record_ids'])} excluded records")
    valuation_valid = not candidate['errors'] and candidate.get('trust', {}).get('valuation_status') != 'blocked'
    if candidate['accounts'] and valuation_valid:
        st.dataframe(pd.DataFrame([{k: v for k, v in a.items() if k not in {'record_ids', 'cash_balances'}} for a in candidate['accounts']]), hide_index=True, use_container_width=True)
    for error in candidate['errors']:
        st.error(error)
    if candidate.get('state') and valuation_valid:
        st.dataframe(pd.DataFrame([{k: v for k, v in p.items() if k != 'account_provenance'} for p in candidate['state']['positions']]), hide_index=True, use_container_width=True)
        st.write({'Reporting currency': 'USD', 'Equity': candidate['state']['totals']['equity'], 'Cash': candidate['state']['cash']['current'], 'Native cash balances': candidate['state']['cash']['balances']})
    if candidate.get('reconciliation') and valuation_valid:
        with st.expander('Financial reconciliation residuals and provenance'):
            st.json(candidate['reconciliation'])
            st.caption('Account conservation checks are backed by independent numerical regression fixtures; they do not independently verify broker export completeness.')
    for message in candidate.get('unsupported', []):
        st.warning(message)
    if candidate.get('trust'):
        render_trust(candidate['trust'], controls=False, key_prefix='consolidation_')
    if st.button('Accept consolidated portfolio', disabled=not candidate.get('ready')):
        try:
            accept_candidate(st.session_state, batch, candidate, request_key)
            st.session_state['consolidation_accepted_key'] = request_key
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if st.session_state.get('consolidation_accepted_key') == request_key:
        st.success('Consolidated portfolio accepted. Dashboard, Deep Analytics, scenarios and Copilot use this canonical portfolio.')
