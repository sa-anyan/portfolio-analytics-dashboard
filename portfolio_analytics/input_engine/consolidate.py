"""Compose independently accounted accounts using the existing canonical engine.

No trade accounting lives here. Original reviewed records remain authoritative.
"""
from collections import defaultdict
from copy import deepcopy
import math

import pandas as pd

from .review import review_view, _index, _hash
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.diagnostics.trust import diagnose_trust


def selected_inputs(batch):
    view = review_view(batch)
    kept = set(view['retained_record_ids'])
    records = [deepcopy(r) for r in view['records'] if r['record_id'] in kept]
    return view, records


def consolidation_key(batch, declarations, prices):
    return _hash([review_view(batch)['digest'], declarations, prices])


def prepare_consolidation(batch, *, declarations, latest_prices=None, market_metadata=None,
                          fx_history=None, split_history=None, policy=None):
    """Return a reviewable candidate or diagnostics; never mutate accepted state.

    Ledgers must be complete from inception, including all funding, with no
    opening positions/cash. Missing opening history is blocked, not inferred.
    """
    view, records = selected_inputs(batch)
    result = {'review_digest': view['digest'], 'accounts': [], 'errors': [], 'warnings': [],
              'state': None, 'parsed': None, 'trust': None, 'ready': False,
              'retained_record_ids': view['retained_record_ids'], 'excluded_record_ids': view['excluded_record_ids'],
              'key': consolidation_key(batch, declarations, latest_prices or {})}
    def block(message):
        result['errors'].append(message)
    if not view['review_confirmed']:
        block('Confirm the current duplicate review before consolidation.')
        return result
    _, parts, _, _ = _index(batch)
    grouped = defaultdict(list)
    for record in records:
        grouped[record['account_id']].append(record)
    as_of = pd.to_datetime(declarations.get('valuation_date'), errors='coerce')
    if pd.isna(as_of) or as_of.normalize() > pd.Timestamp.now().normalize():
        block('Supply a valid, non-future common valuation date.')
        return result
    as_of = as_of.normalize()
    kinds = {}
    part_objects = {p['part_id']: p for f in batch['files'] for p in f['parts']}
    prices = {str(t).upper(): float(v) for t, v in (latest_prices or {}).items() if v is not None and math.isfinite(float(v)) and float(v) > 0}
    supplied = defaultdict(set)
    for account, rows in grouped.items():
        pids = {r['part_id'] for r in rows}
        formats = {parts[p]['format'] for p in pids}
        if len(formats) != 1 or ('holdings' in formats and len(pids) != 1):
            block(f'{account}: select one snapshot or an authoritative ledger; overlapping formats cannot be reconciled.')
            continue
        kind = kinds[account] = next(iter(formats))
        declaration = declarations.get('accounts', {}).get(account, {})
        for pid in pids:
            parsed_part = part_objects[pid]['parsed']
            currency_column = parsed_part.get('column_map', {}).get('currency')
            if not currency_column or any(not str(r.get(currency_column) or '').strip() for r in parsed_part['user_dataset']['records']):
                result['warnings'].append(f'{account}: missing input quote currencies defaulted to USD by the existing normaliser; verify the broker listing currency.')
        simultaneous = defaultdict(list)
        for r in rows:
            if r['kind'] == 'ledger':
                simultaneous[(r['data']['Ticker'], r['data']['Date'])].append(r)
        for (ticker, date), events in simultaneous.items():
            if len({e['part_id'] for e in events}) > 1 and len({e['data']['Type'] for e in events}) > 1:
                block(f'{account}: {ticker} has opposite trades at the same timestamp across sources ({date}); provide execution timestamps or one authoritative chronological ledger before cost/P&L accounting.')
        if kind == 'holdings':
            dates = {d for p in pids for d in parts[p]['snapshot_dates']}
            if not dates:
                declared = pd.to_datetime(declaration.get('snapshot_date'), errors='coerce')
                if pd.isna(declared):
                    block(f'{account}: snapshot valuation date is missing; explicitly supply the statement date.')
                else:
                    dates = {declared.date().isoformat()}
            if dates != {as_of.date().isoformat()}:
                block(f'{account}: snapshot date {sorted(dates)} does not match the common valuation date {as_of.date().isoformat()}. Supply aligned statements.')
        elif declaration.get('complete_from_zero') is not True:
            block(f'{account}: missing opening balances/history. Confirm a complete ledger from inception with zero opening cash and positions, or supply a current snapshot.')
        for record in rows:
            row = record['data']
            if kind == 'ledger' and pd.Timestamp(row['Date']).normalize() > as_of:
                block(f'{account}: transaction after the valuation date; no future event may enter the current book.')
            if record['kind'] == 'holdings' and row.get('Current Price') is not None and row.get('Ticker') != 'CASH' and str(row.get('Asset Class', '')).lower() != 'cash':
                supplied[row['Ticker']].add(float(row['Current Price']))
    for ticker, values in supplied.items():
        if ticker not in prices:
            if len(values) != 1:
                block(f'{ticker}: conflicting supplied prices; provide one common validated quote.')
            else:
                prices[ticker] = next(iter(values))
    # One ticker must refer to the same quoted security across accounts.
    currencies = defaultdict(set)
    for r in records:
        row = r['data']
        if row.get('Ticker') and row['Ticker'] != 'CASH' and str(row.get('Asset Class', '')).lower() != 'cash':
            currencies[row['Ticker']].add(row.get('Currency', 'USD'))
    for ticker, ccys in currencies.items():
        if len(ccys) > 1:
            block(f'{ticker}: inconsistent quote currencies {sorted(ccys)}; correct security identity before combining.')
    if result['errors']:
        return result
    normalised = {k: [] for k in ('holdings', 'ledger', 'cashflows')}
    for r in records:
        row = {**r['data'], '_provenance': {k: v for k, v in r.items() if k != 'data'}}
        # Review serialises evidence as ISO strings; restore the normaliser's timestamp
        # type once rather than triggering pandas format inference for every execution.
        if row.get('Date'):
            row['Date'] = pd.Timestamp(row['Date'])
        normalised[r['kind']].append(row)
    parsed = {'classification': 'consolidated', 'confidence': 1.0, 'issues': [],
              'source': {'kind': 'reviewed_account_consolidation', 'filename': 'Reviewed account files'},
              'inputs': {'starting_cash': 0.0}, 'normalised_dataset': normalised,
              'column_map': {'currency': 'Currency'},
              'user_dataset': {'records': [deepcopy(row) for rows in normalised.values() for row in rows]}, 'variables': {'provided_prices': prices, 'tickers': sorted(currencies)}}
    result['parsed'] = parsed
    if split_history is not None and not split_history.empty:
        split_history = split_history.loc[split_history.index <= as_of]
    account_states = {}
    for account, rows in sorted(grouped.items()):
        account_parsed = {**parsed, 'classification': kinds[account],
            'user_dataset': {'records': [{**r['data'], '_provenance': {k: v for k, v in r.items() if k != 'data'}} for r in rows]},
            'normalised_dataset': {k: [r for r in normalised[k] if r['_provenance']['account_id'] == account] for k in normalised}}
        try:
            state = build_portfolio_state(account_parsed, latest_prices=prices, market_metadata=market_metadata,
                                          fx_history=fx_history, split_history=split_history if kinds[account] == 'ledger' else None)
            if kinds[account] == 'holdings':
                state['totals']['realised_pnl'] = None
                for position in state['positions']:
                    position['realised_pnl'] = None
                state['accounting_history'] = {'available': False, 'method': None, 'trades': [], 'cashflows': [],
                    'missing_dated_basis': [], 'reason': 'A snapshot supplies current holdings, not actual transaction history.'}
            if any(p['cost_basis'] is None for p in state['positions']):
                state['totals']['cost_basis'] = None
                state['totals']['unrealised_pnl'] = None
            account_states[account] = state
            result['accounts'].append({'account': account, 'format': kinds[account], **state['totals'],
                                       'cash_balances': state['cash']['balances'], 'record_ids': [r['record_id'] for r in rows]})
        except (ValueError, KeyError, TypeError) as exc:
            block(f'{account}: {exc}')
    if result['errors']:
        result['trust'] = diagnose_trust(None, parsed=parsed, latest_prices=prices, market_metadata=market_metadata,
                                         fx_history=fx_history, as_of=as_of, policy=policy, failure='; '.join(result['errors']))
        # Ledger-only missing prices also need position-level evidence, not just a generic failure.
        for account in grouped:
            if account not in account_states:
                for ticker in {r['data'].get('Ticker') for r in grouped[account]} - {None, '', 'CASH'}:
                    if ticker not in prices:
                        result['trust']['issues'].append({'code': 'price_missing', 'severity': 'blocker', 'title': f'{ticker}: security price is missing',
                            'explanation': 'Complete valuation and acceptance are withheld; this security is not excluded.',
                            'affects': ['valuation', 'allocation', 'risk', 'performance', 'scenarios'], 'holdings': [ticker], 'evidence': {'account': account}})
        return result
    derived = []
    directions = defaultdict(set)
    for account, state in account_states.items():
        for row in state['positions']:
            directions[row['ticker']].add(row['side'])
            derived.append({'Ticker': row['ticker'], 'Quantity': row['quantity'], 'Current Price': row['current_price'],
                            'Average Entry Price': row['average_entry_price'], 'Currency': 'USD',
                            'Asset Name': row['asset_name'], 'Asset Class': row['asset_class'],
                            'Duration': row['duration'], 'Rate Sensitivity': row['rate_sensitivity']})
        for ccy, amount in state['cash']['balances'].items():
            derived.append({'Ticker': 'CASH', 'Quantity': amount, 'Current Price': 1.0, 'Currency': ccy, 'Asset Class': 'Cash'})
    for ticker, sides in directions.items():
        if len(sides) > 1:
            block(f'{ticker}: offsetting long/short account positions cannot be represented safely by the current single-position analytics. Gross ownership would be hidden; acceptance is blocked.')
    if result['errors']:
        return result
    composition = deepcopy(parsed)
    composition['classification'] = 'holdings'
    composition['normalised_dataset'] = {'holdings': derived, 'ledger': [], 'cashflows': []}
    composition['variables']['provided_prices'] = {r['Ticker']: r['Current Price'] for r in derived if r['Ticker'] != 'CASH'}
    state = build_portfolio_state(composition, fx_history=fx_history, market_metadata=market_metadata)
    state['cash']['external_net_flows'] = sum(s['cash']['external_net_flows'] for s in account_states.values())
    state['cash']['explicit_snapshot_cash'] = sum(s['cash'].get('explicit_snapshot_cash', 0) for s in account_states.values())
    # Preserve account-level realised P&L; snapshots cannot establish realised history.
    all_ledgers = all(kind == 'ledger' for kind in kinds.values())
    state['totals']['realised_pnl'] = sum(s['totals']['realised_pnl'] for s in account_states.values()) if all_ledgers else None
    if any(any(p['cost_basis'] is None for p in s['positions']) for s in account_states.values()):
        state['totals']['cost_basis'] = None
        state['totals']['unrealised_pnl'] = None
        result['warnings'].append('Cost basis and unrealised P&L are unavailable for the complete portfolio because at least one open position lacks basis.')
    for position in state['positions']:
        ticker = position['ticker']
        position['currency'] = next(iter(currencies[ticker]))
        position['local_current_price'] = prices[ticker]
        position['account_provenance'] = [r for r in records if r['data'].get('Ticker') == ticker]
        constituent_positions = [p for s in account_states.values() for p in s['positions'] if p['ticker'] == ticker]
        position['realised_pnl'] = (sum(p['realised_pnl'] for p in constituent_positions)
            if all(p['realised_pnl'] is not None for p in constituent_positions) else None)
    state['inputs']['normalised_dataset'] = deepcopy(normalised)
    state['inputs']['user_dataset'] = deepcopy(parsed['user_dataset'])
    state['accounting_history'] = {'available': all_ledgers, 'method': 'complete reviewed account ledgers' if all_ledgers else None,
        'trades': deepcopy(normalised['ledger']) if all_ledgers else [], 'cashflows': deepcopy(normalised['cashflows']) if all_ledgers else [],
        'missing_dated_basis': [], 'reason': None if all_ledgers else 'Holdings snapshots establish current ownership only; consolidated actual historical performance and realised P&L are unavailable.'}
    state['accounting_log'] = [{'account_id': a, **event} for a, s in account_states.items() for event in s['accounting_log']]
    state['meta'].update(path='consolidated', valuation_as_of=as_of.date().isoformat(), warnings=sorted(set(result['warnings'] + [w for s in account_states.values() for w in s['meta']['warnings']])))
    state['consolidation'] = {'review_digest': view['digest'], 'declarations': deepcopy(declarations), 'records': records,
        'excluded_record_ids': view['excluded_record_ids'], 'review_history': deepcopy(batch.get('review_history', [])),
        'sources': [{'source_id': f['source_id'], 'filename': f['filename'], 'fingerprint': f['fingerprint']} for f in batch['files']],
        'accounts': account_states}
    # Account sums are a conservation check, not an independent proof of the accounting formulas.
    residuals = {}
    for metric in ('signed_market_value', 'cash', 'equity', 'long_exposure', 'short_exposure', 'gross_exposure', 'net_exposure', 'cost_basis', 'unrealised_pnl', 'realised_pnl'):
        values = [s['totals'].get(metric) for s in account_states.values()]
        observed = state['totals'].get(metric)
        residuals[metric] = observed - sum(values) if observed is not None and all(v is not None for v in values) else None
    quantities = defaultdict(float)
    native_cash = defaultdict(float)
    for s in account_states.values():
        for p in s['positions']:
            quantities[p['ticker']] += p['quantity']
        for c, v in s['cash']['balances'].items():
            native_cash[c] += v
    residuals['quantities'] = {p['ticker']: p['quantity'] - quantities[p['ticker']] for p in state['positions']}
    residuals['native_cash'] = {c: state['cash']['balances'].get(c, 0) - v for c, v in native_cash.items()}
    numbers = [v for v in residuals.values() if isinstance(v, (int, float))] + list(residuals['quantities'].values()) + list(residuals['native_cash'].values())
    reconciled = all(math.isfinite(v) and abs(v) <= 1e-7 * max(1, abs(state['totals']['equity'])) for v in numbers)
    result['reconciliation'] = {'passed': reconciled, 'residuals': residuals, 'account_sum_equity': sum(s['totals']['equity'] for s in account_states.values())}
    if not reconciled:
        block('Account-to-consolidated reconciliation failed; accepted portfolio remains unchanged.')
    result['trust'] = diagnose_trust(state, parsed=parsed, market_metadata=market_metadata, fx_history=fx_history, as_of=as_of, policy=policy)
    if result['trust']['valuation_status'] == 'blocked':
        block('Currency/valuation Trust diagnostics block acceptance. Inspect affected observations.')
    state['scenario_baseline'] = {k: deepcopy(state[k]) for k in ('positions', 'cash', 'totals')}
    result['state'] = state
    result['unsupported'] = ([] if all_ledgers else ['Complete historical performance and realised P&L cannot be established from holdings snapshots.']) + result['warnings']
    result['ready'] = not result['errors']
    return result
