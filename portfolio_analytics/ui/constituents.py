"""Supplementary input review and read-only underlying exposure workspace."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_analytics.input_engine.constituents import (
    identity_catalog, stage_constituents, validate_constituents, accept_constituents, saved_coverage,
)


def percent(value):
    if value is None:
        return 'Unknown / unresolved'
    number = float(value)
    return f'{number*100:g}%'


def render_exposure(state):
    st.caption('Exposure · Known underlying securities. Owned holdings and canonical equity remain unchanged.')
    catalog = identity_catalog(state, parsed=st.session_state.get('parsed'), accepted_batch=st.session_state.get('accepted_consolidation_batch'))
    if not catalog:
        st.info('Accept a portfolio before linking constituent data to its funds.')
        return
    store = st.session_state.get('fund_constituents', {})
    maximum = int(st.number_input('Maximum constituent age (calendar days)', min_value=0, max_value=3650, value=90, step=1, key='constituent_max_age'))
    st.caption('Freshness is an explicit calendar-day review rule, not an issuer update schedule or a guarantee.')
    render_lookthrough(state, store, catalog, maximum)
    with st.expander('Accepted constituent coverage and funds without data'):
        rows = []
        for ticker, item in catalog.items():
            snapshot = store.get(ticker)
            fund = any(w in item['asset_class'].lower() for w in ('fund', 'etf', 'trust'))
            if not snapshot and not fund:
                continue
            if not snapshot:
                rows.append({'Fund': ticker, 'Status': 'No constituent data — unknown coverage', 'Known weight': 'Unknown', 'Unknown weight': 'Unknown'})
            else:
                coverage = saved_coverage(snapshot, catalog, max_age_days=maximum)
                rows.append({'Fund': ticker, 'Status': coverage['completeness'], 'Known weight': percent(coverage['known_weight_coverage']),
                    'Unknown weight': percent(coverage['unreported_weight']), 'As of': coverage['as_of'], 'Constituents': coverage['constituent_count'],
                    'Source': ', '.join(coverage['source_labels']) or 'Unknown provider',
                    'Identity issues': len(coverage['unresolved_identity_records']),
                    'Freshness': 'Stale / unverifiable' if coverage['stale'] else 'Within configured rule',
                    'Parent identity': 'Current holding' if coverage['parent_current'] else 'Absent / changed — review linkage'})
        for ticker in set(store)-set(catalog):
            rows.append({'Fund': ticker, 'Status': 'Parent no longer held; metadata retained, not applied'})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption('No verified fund classifications or accepted constituent snapshots. Select a held security and explicitly identify it as a fund if necessary.')
    options = sorted(catalog, key=lambda t: (not any(w in catalog[t]['asset_class'].lower() for w in ('fund', 'etf', 'trust')), t))
    parent = st.selectbox('Select parent ETF / fund', options, key='constituent_parent')
    st.caption('Parent identifiers must match accepted holdings or receive explicit review. Similar names are never used to merge identities.')
    with st.expander('CSV template and accepted format'):
        template = Path(__file__).resolve().parents[2] / 'examples' / 'constituents' / 'synthetic_voo.csv'
        st.download_button('Download synthetic constituent template', template.read_bytes(), file_name='synthetic_voo_constituents.csv', mime='text/csv', key='constituent_template')
        st.caption('Weight + As Of Date (ISO YYYY-MM-DD) are required. Include parent/constituent ticker, ISIN or security ID, name, percentage/decimal format and source. Only the first XLSX sheet is read. Template values are synthetic, not live issuer holdings.')
    if parent in store:
        with st.expander(f'Accepted constituent evidence · {parent}'):
            snapshot = store[parent]
            st.caption(f"Snapshot {snapshot['coverage']['as_of']} · {len(snapshot.get('history', []))} archived previous versions. Constituents remain supplementary metadata.")
            st.dataframe(pd.DataFrame([{'Name': r['name'], 'Identifiers': str(r['identifiers']), 'Resolved identifiers': str(r['resolved_identifiers']),
                'Weight fraction': r['weight'], 'Direct-security match': r['matched_direct_security'] or 'No exact direct match',
                'As of': r['as_of'], 'Source': r['source'] or 'Unknown', 'Type': r['asset_class'], 'Currency': r['currency']} for r in snapshot['records']]), hide_index=True, use_container_width=True)
            for f in snapshot['sources']:
                st.caption(f"{f['filename']} · SHA-256 {f['fingerprint']}")
            st.json(snapshot['decisions'])
    uploaded = st.file_uploader('Upload constituent CSV/XLSX files', type=['csv', 'xlsx'], accept_multiple_files=True, key='constituent_upload')
    if st.button('Review constituent data', type='primary'):
        if not uploaded:
            st.warning('Select constituent files first.')
        else:
            st.session_state['constituent_draft'] = stage_constituents([(f.name, f.getvalue()) for f in uploaded], selected_parent=parent)
            st.session_state['constituent_review_nonce'] = st.session_state.get('constituent_review_nonce', 0) + 1
            st.rerun()
    draft = st.session_state.get('constituent_draft')
    if not draft or draft['selected_parent'] != parent:
        st.caption('Select fund → upload constituent data → review exceptions → accept data.')
        return
    prefix = f"constituent_{st.session_state.get('constituent_review_nonce', 0)}_"
    decisions = {}
    with st.expander('Input declarations and identity review', expanded=True):
        unit = st.selectbox('Weight format when absent from file', ['Use file format / explicit % suffix', 'percentage', 'decimal'], key=prefix+'unit')
        decisions['weight_format'] = None if unit.startswith('Use file') else unit
        decisions['completeness'] = st.selectbox('Dataset completeness declaration', ['unspecified', 'complete', 'partial'], key=prefix+'completeness')
        decisions['source_label'] = st.text_input('Provider/source if absent from file (optional)', key=prefix+'source')
        if not any(w in catalog[parent]['asset_class'].lower() for w in ('fund', 'etf', 'trust')):
            decisions['fund_confirmed'] = st.checkbox('Confirm this held security is an ETF / investment fund', key=prefix+'fund')
            decisions['fund_reason'] = st.text_input('Fund identification reason', key=prefix+'fund_reason')
        provisional = validate_constituents(draft, catalog, decisions=decisions, max_age_days=maximum)
        if len(provisional['snapshots']) > 1:
            decisions['snapshot_id'] = st.selectbox('Constituent snapshot to retain', provisional['snapshots'], index=None, placeholder='Choose one source/date snapshot', key=prefix+'snapshot')
            decisions['snapshot_reason'] = st.text_input('Snapshot selection reason', key=prefix+'snapshot_reason')
        sources = {f['source_id']: f for f in draft['files']}
        excluded_sources = st.multiselect('Source files to exclude', list(sources), format_func=lambda s: f"{sources[s]['filename']} · {s}", key=prefix+'exclude_sources')
        source_reason = st.text_input('Reason for source exclusions', key=prefix+'exclude_source_reason') if excluded_sources else ''
        decisions['excluded_sources'] = {s: source_reason for s in excluded_sources}
        lookup = {r['record_id']: r for r in draft['records']}
        rids = st.multiselect('Constituent records to exclude', list(lookup), format_func=lambda r: f"{lookup[r]['source_file']} row {lookup[r]['source_row']} · {lookup[r]['fields'].get('ticker') or lookup[r]['fields'].get('isin') or lookup[r]['fields'].get('name', '')}", key=prefix+'exclude')
        reason = st.text_input('Reason for record exclusions', key=prefix+'exclude_reason') if rids else ''
        decisions['excluded_records'] = {r: reason for r in rids}
        current = validate_constituents(draft, catalog, decisions=decisions, max_age_days=maximum)
        if any(i['code'] == 'parent_ambiguous' for i in current['issues']):
            confirmed = st.checkbox(f'Explicitly assign unresolved parent identifiers to {parent}', key=prefix+'parent_confirm')
            reason = st.text_input('Parent assignment reason', key=prefix+'parent_reason')
            if confirmed:
                decisions['parent_assignment'] = {'target': parent, 'reason': reason}
        with st.expander('Optional constituent identifier mappings'):
            st.caption('Review one record at a time using exact security evidence. Similar names never establish identity.')
            key = prefix+'identity_decisions'
            mappings = st.session_state.get(key, {})
            if current['records']:
                rows = {r['record_id']: r for r in current['records']}
                rid = st.selectbox('Constituent identity to review', list(rows),
                    format_func=lambda r: f"{rows[r]['source_file']} row {rows[r]['source_row']} · {rows[r]['name']}", key=prefix+'identity_row')
                target = st.selectbox('Resolved constituent identity', ['Keep reported identifiers'] + sorted(catalog), key=prefix+'identity_'+rid)
                reason = st.text_input('Identity mapping reason', key=prefix+'identity_reason_'+rid)
                if st.button('Save constituent identity decision'):
                    if not reason.strip():
                        st.error('A reason is required for an identity decision.')
                    else:
                        updated = deepcopy(mappings)
                        previous = updated.pop(rid, None)
                        if target != 'Keep reported identifiers':
                            updated[rid] = {'target': target, 'reason': reason.strip()}
                        st.session_state[key] = updated
                        st.session_state.setdefault(prefix+'identity_history', []).append({'record_id': rid, 'previous': previous,
                            'decision': target, 'reason': reason.strip()})
                        st.rerun()
            decisions['identity_mappings'] = deepcopy(mappings)
            decisions['identity_history'] = deepcopy(st.session_state.get(prefix+'identity_history', []))
    checked = validate_constituents(draft, catalog, decisions=decisions, max_age_days=maximum)
    coverage = checked['coverage']
    st.markdown('**Constituent data review**')
    st.caption(f"{parent} · {coverage['constituent_count']} retained constituents · as of {coverage['as_of'] or 'unresolved'} · reported subtotal {percent(coverage['reported_weight'])} · {coverage['completeness']}")
    st.caption(f"Known reported coverage: {percent(coverage['known_weight_coverage'])}. Unknown/unreported weight: {percent(coverage['unreported_weight'])}. Use original whole-fund weights. Weights are not normalised or converted into portfolio exposure.")
    grouped_issues = {}
    for issue in checked['issues']:
        key = (issue['severity'], issue['code'], issue['explanation'])
        grouped_issues.setdefault(key, set()).update(issue['record_ids'])
    for (severity, code, explanation), affected in list(grouped_issues.items())[:8]:
        suffix = f' · {len(affected)} affected records' if len(affected) > 1 else ''
        (st.error if severity == 'blocker' else st.warning)(explanation + suffix)
    if len(grouped_issues) > 8:
        st.caption(f'{len(grouped_issues)-8} further diagnostic groups are available in source evidence below.')
    if store.get(parent):
        st.caption('Acceptance replaces this fund’s supplementary snapshot and archives its previous evidence; owned portfolio holdings stay unchanged.')
        if any(f['fingerprint'] in {s['fingerprint'] for s in store[parent]['sources']} for f in draft['files']):
            st.info('This source content was previously accepted. It will replace metadata, never add economic positions.')
    with st.expander('Weights, mappings and original source evidence'):
        st.dataframe(pd.DataFrame([{'Record ID': r['record_id'], 'Name': r['name'], 'Reported identifiers': str(r['identifiers']),
            'Direct-security match': r['matched_direct_security'] or 'No exact direct match; identifier retained', 'Identity': r['identity_status'],
            'Weight fraction': r['weight'], 'As of': r['as_of'], 'Type': r['asset_class'], 'Currency': r['currency'], 'Source': r['source'] or 'Unknown'} for r in checked['records']]), hide_index=True, use_container_width=True)
        for f in draft['files']:
            st.caption(f"{f['filename']} · SHA-256 {f['fingerprint']}")
            if f['raw_records']:
                st.dataframe(pd.DataFrame(f['raw_records']), hide_index=True, use_container_width=True)
        st.json({'decisions': decisions, 'validation': checked['issues']})
    if st.button('Accept constituent data', disabled=not checked['ready']):
        st.session_state['fund_constituents'] = accept_constituents(store, draft, catalog, decisions=decisions, max_age_days=maximum)
        st.session_state['constituent_draft'] = None
        st.rerun()
    if st.button('Reject constituent draft'):
        st.session_state.setdefault('rejected_constituent_drafts', []).append(deepcopy({'draft': draft, 'decisions': decisions}))
        st.session_state['constituent_draft'] = None
        st.rerun()


def render_lookthrough(state, store, catalog, maximum):
    from portfolio_analytics.analytics.lookthrough import calculate_lookthrough, eligibility_declaration, is_fund
    from portfolio_analytics.ui.discover import queue_copilot_question
    declarations = st.session_state.get('fund_eligibility', {})
    funds = [t for t, item in catalog.items() if is_fund(item) or t in store]
    with st.expander('Fund eligibility · review instrument structure and weight basis'):
        st.caption('Only ordinary long-only equity funds with whole-fund capital-allocation weights are supported. Leveraged, inverse, derivative and nested structures are unavailable. This is an attestation, not issuer verification.')
        if funds:
            parent = st.selectbox('Fund eligibility to review', funds, key='eligibility_parent')
            confirmed = st.checkbox('Confirm ordinary equity fund and whole-fund capital weights', key='eligibility_confirm_'+parent)
            reason = st.text_input('Eligibility evidence / reason', key='eligibility_reason_'+parent)
            if st.button('Save fund eligibility', disabled=not confirmed or not reason.strip()):
                updated = deepcopy(declarations)
                updated[parent] = eligibility_declaration(catalog[parent], reason)
                st.session_state['fund_eligibility'] = updated
                st.rerun()
            if parent in declarations and st.button('Revoke fund eligibility'):
                updated = deepcopy(declarations); updated.pop(parent)
                st.session_state['fund_eligibility'] = updated
                st.rerun()
    result = calculate_lookthrough(state, store, catalog, declarations=declarations,
        trust=(st.session_state.get('analytics') or {}).get('trust_diagnostics', {}), max_age_days=maximum)
    st.markdown('**Largest known underlying exposures**')
    currency = result['reporting_currency']
    st.caption(f'Reporting currency: {currency}. Legal holdings remain in the dashboard. Security/share-class exposure does not merge separate share classes into an issuer total.')
    if not result['valuation_valid']:
        st.error('Canonical valuation is invalid; monetary look-through and equity percentages are unavailable.')
    if not result['complete_ranking']:
        st.warning('Limited composition: this is a ranking of known exposures, not a complete economic concentration ranking. Unreported holdings may include securities already shown. Unknown is not zero.')
    else:
        st.caption('Complete reported composition within the supported equity scope; excluded assets and source assumptions remain disclosed below.')
    securities = result['securities']
    if securities:
        rows = [{'Security': r['name'], 'Direct': float(r['direct']), 'Indirect': float(sum(Decimal(v) for v in r['indirect_by_fund'].values())),
                 'Known total': float(r['known_total']), 'Equity %': float(r['equity_fraction'])*100 if r['equity_fraction'] is not None else None,
                 'Funds': ', '.join(r['indirect_by_fund'])} for r in securities[:10]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        keys = [' / '.join(v[0] for v in r['identifiers'].values() if v) for r in securities]
        index = st.selectbox('Investigate underlying security', range(len(securities)), format_func=lambda i: securities[i]['name']+' · '+keys[i], key='exposure_security')
        selected = securities[index]
        st.write(f"Known {selected['name']} exposure: {float(selected['known_total']):,.2f} {currency} · direct {float(selected['direct']):,.2f} {currency}.")
        if selected['indirect_by_fund']:
            st.dataframe(pd.DataFrame([{'Parent fund': p, 'Indirect exposure': float(v), 'Currency': currency} for p,v in selected['indirect_by_fund'].items()]), hide_index=True, use_container_width=True)
        with st.expander('Supporting calculations and identity evidence'):
            st.caption('Each indirect amount uses the current canonical parent value × original constituent weight. Constituent listing currency is not economic currency exposure. No additional FX is applied.')
            st.json(selected)
        st.button('Prepare exposure question for Copilot', on_click=queue_copilot_question,
            args=(f"Explain the known direct and indirect exposure to {selected['name']}, fund contributions and coverage limitations using lookthrough evidence. Do not infer unavailable exposures.",))
    else:
        st.info('No supported underlying exposures are available. Review valuation, fund eligibility and constituent inputs below.')
    with st.expander('Exposure findings, coverage and reconciliation'):
        for finding in result['findings']:
            st.write((finding.get('security') or finding.get('parent') or 'Portfolio')+' · '+finding['explanation'])
        st.json({'funds': result['funds'], 'reconciliation': result['reconciliation'], 'known_is_lower_bound': result['known_is_lower_bound'], 'methodology': result['methodology']})
        st.caption('A lower-bound interpretation requires positive supported capital exposures and usable identity/date evidence. It is not a current-price guarantee or a risk estimate. Arithmetic reconciliation cannot verify an unverified composition.')
