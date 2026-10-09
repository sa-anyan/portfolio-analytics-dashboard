"""Independent hand-calculated fixtures, account conservation, and safety gates."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from portfolio_analytics.input_engine.batch import ingest_files, append_files
from portfolio_analytics.input_engine.review import confirm_review, decide, review_view, reverse_decision
from portfolio_analytics.input_engine.consolidate import prepare_consolidation
from tests.test_duplicate_review import csv, issue, james_batch, resolve_james

TODAY = pd.Timestamp.today().date().isoformat()


def snapshot(account, qty, entry=80, date=TODAY, cash=0, ticker='NVDA', currency='USD'):
    rows = [dict(Account=account, Ticker=ticker, Quantity=qty, Currency=currency, **{'Average Entry Price': entry, 'Current Price': 100, 'Valuation Date': date})]
    if cash:
        rows.append(dict(Account=account, Ticker='CASH', Quantity=cash, Currency=currency, **{'Current Price': 1, 'Valuation Date': date}))
    return csv(rows)


def maya_reviewed():
    batch = ingest_files([('isa.csv', snapshot('ISA', 10)), ('pension.csv', snapshot('Pension', 20, 60, cash=200)),
                          ('gia.csv', snapshot('GIA', 5, 90, cash=50)), ('renamed.csv', snapshot('ISA', 10)),
                          ('isa-new.csv', snapshot('ISA', 12, cash=100))])
    batch = decide(batch, issue(batch, 'exact_file_reupload')['id'], 'exclude_sources', reason='Confirmed identical ISA statement.', targets=[batch['files'][3]['source_id']])
    batch = decide(batch, issue(batch, 'snapshot_conflict')['id'], 'choose_snapshot', reason='Authoritative corrected ISA snapshot.', targets=[batch['files'][4]['parts'][0]['part_id']])
    return confirm_review(batch)


def declarations(accounts=(), valuation=TODAY):
    return {'valuation_date': valuation, 'accounts': {a: {'complete_from_zero': True} for a in accounts}}


def james_reviewed():
    raw = james_batch()
    files = [(f['filename'], csv([{k: v for k, v in row.items() if k != '_provenance'} for row in f['raw_records']])) for f in raw['files'][:2]]
    files.append(('private.csv', csv({'Account': ['Private']*2, 'Ticker': ['UNPRICED', 'CASH'], 'Quantity': [1, 25],
        'Average Entry Price': [3, None], 'Current Price': [None, 1], 'Currency': ['EUR']*2, 'Valuation Date': [TODAY]*2})))
    return confirm_review(resolve_james(ingest_files(files)))


def james_fx():
    return pd.DataFrame({'GBP': [1.25, 1.25], 'EUR': [1.1, 1.1]}, index=pd.to_datetime(['2026-01-01', TODAY]))


JAMES_QUOTES = {'US': 12, 'UK': 18, 'PENCE': 600, 'FUND': 3, 'UNPRICED': 4}


def test_maya_independent_wealth_and_basis_fixture():
    batch = maya_reviewed()
    original = deepcopy(batch)
    candidate = prepare_consolidation(batch, declarations=declarations())
    assert candidate['ready'], candidate['errors']
    state = candidate['state']
    # Manually: 12+20+5 =37; value3700; cash100+200+50=350;
    # basis12*80+20*60+5*90=2610; equity4050; unrealised1090.
    assert state['positions'][0]['quantity'] == 37
    for name, expected in {'signed_market_value': 3700, 'cash': 350, 'equity': 4050, 'cost_basis': 2610,
                           'unrealised_pnl': 1090, 'long_exposure': 3700, 'short_exposure': 0}.items():
        assert state['totals'][name] == pytest.approx(expected)
    assert state['totals']['realised_pnl'] is None
    assert not state['accounting_history']['available'] and not state['accounting_history']['trades']
    assert len(state['consolidation']['records']) == 6 and len(candidate['excluded_record_ids']) == 2
    assert len(state['positions'][0]['account_provenance']) == 3
    assert candidate['reconciliation']['passed']
    assert all(v == pytest.approx(0) for v in candidate['reconciliation']['residuals'].values() if isinstance(v, float))
    assert batch == original
    json.dumps(candidate, default=str, allow_nan=False)


def test_james_unpriced_block_then_independent_multicurrency_fixture():
    batch = james_reviewed()
    d = declarations(['Broker'])
    blocked = prepare_consolidation(batch, declarations=d, latest_prices={k: v for k, v in JAMES_QUOTES.items() if k != 'UNPRICED'}, fx_history=james_fx())
    assert not blocked['ready'] and blocked['state'] is None
    assert any('UNPRICED' in e for e in blocked['errors'])
    assert any(i['code'] == 'price_missing' and 'UNPRICED' in i['holdings'] for i in blocked['trust']['issues'])
    candidate = prepare_consolidation(batch, declarations=d, latest_prices=JAMES_QUOTES, fx_history=james_fx())
    assert candidate['ready'], candidate['errors']
    state = candidate['state']
    assert {p['ticker']: p['quantity'] for p in state['positions']} == {'US': 1, 'UK': -2, 'PENCE': 2, 'FUND': 1, 'UNPRICED': 1}
    # Native cash: USD -10-2=-12; GBP +40; GBX -1000; EUR100+25=125.
    # USD cash=-12+40*1.25-1000*.0125+125*1.1=163.
    # Values: 12-2*18*1.25+2*600*.0125+3+4*1.1=-10.6.
    # Equity=152.4; gross=34.4+45=79.4; basis10+50+12.5+2+3.3=77.8.
    assert state['cash']['explicit_snapshot_cash'] == pytest.approx(27.5)
    assert state['cash']['balances'] == pytest.approx({'USD': -12, 'GBP': 40, 'GBX': -1000, 'EUR': 125})
    for name, expected in {'cash': 163, 'signed_market_value': -10.6, 'equity': 152.4, 'long_exposure': 34.4,
                           'short_exposure': 45, 'gross_exposure': 79.4, 'net_exposure': -10.6,
                           'cost_basis': 77.8, 'unrealised_pnl': 11.6}.items():
        assert state['totals'][name] == pytest.approx(expected)
    assert len(candidate['retained_record_ids']) == 8 and len(candidate['excluded_record_ids']) == 1
    assert candidate['reconciliation']['passed'] and state['totals']['realised_pnl'] is None


def test_account_cost_basis_is_not_cross_account_trade_netting_and_closed_pnl_survives():
    rows = [dict(Account='A', Date='2026-01-01', Type='DEPOSIT', Amount=100, Currency='USD'),
            dict(Account='A', Date='2026-01-02', Type='BUY', Ticker='X', Quantity=10, Price=5, Currency='USD'),
            dict(Account='A', Date='2026-01-03', Type='SELL', Ticker='X', Quantity=4, Price=8, Currency='USD'),
            dict(Account='B', Date='2026-02-01', Type='DEPOSIT', Amount=100, Currency='USD'),
            dict(Account='B', Date='2026-02-02', Type='BUY', Ticker='X', Quantity=2, Price=10, Currency='USD'),
            dict(Account='B', Date='2026-02-03', Type='BUY', Ticker='Y', Quantity=1, Price=3, Currency='USD'),
            dict(Account='B', Date='2026-02-04', Type='SELL', Ticker='Y', Quantity=1, Price=4, Currency='USD')]
    batch = confirm_review(ingest_files([('ledgers.csv', csv(rows))]))
    c = prepare_consolidation(batch, declarations=declarations(['A', 'B']), latest_prices={'X': 8})
    assert c['ready'], c['errors']
    # A: cash82, X6 basis30, realised12, unrealised18.
    # B: cash81, X2 basis20, realisedY1, unrealisedX-4.
    # Combined cash163,value64,equity227,basis50,realised13,unrealised14.
    for k, v in {'cash': 163, 'signed_market_value': 64, 'equity': 227, 'cost_basis': 50, 'realised_pnl': 13, 'unrealised_pnl': 14}.items():
        assert c['state']['totals'][k] == pytest.approx(v)
    assert c['state']['accounting_history']['available']
    assert c['state']['positions'][0]['average_entry_price'] == 6.25


@pytest.mark.parametrize('case', ['dates', 'opening', 'fx', 'unconfirmed', 'reversal', 'opposing', 'quotes', 'quote_currency'])
def test_unsafe_combinations_block_without_mutating_review(case):
    batch = maya_reviewed()
    d = declarations()
    prices, fx = {}, None
    if case == 'dates':
        d['valuation_date'] = '2026-01-01'
    elif case == 'opening':
        batch = james_reviewed()
        prices, fx = JAMES_QUOTES, james_fx()
    elif case == 'fx':
        batch, d, prices = james_reviewed(), declarations(['Broker']), JAMES_QUOTES
    elif case == 'unconfirmed':
        batch.pop('review_confirmation')
    elif case == 'reversal':
        identifier = next(i for i, v in batch['review_decisions'].items() if v['action'] == 'exclude_sources')
        batch = reverse_decision(batch, identifier, reason='Reopen identity review.')
    elif case == 'opposing':
        batch = confirm_review(ingest_files([('a.csv', snapshot('A', 2)), ('b.csv', snapshot('B', -2))]))
    elif case == 'quotes':
        second = pd.read_csv(__import__('io').BytesIO(snapshot('B', 2)))
        second.loc[0, 'Current Price'] = 101
        batch = confirm_review(ingest_files([('a.csv', snapshot('A', 2)), ('b.csv', second.to_csv(index=False).encode())]))
    elif case == 'quote_currency':
        batch = confirm_review(ingest_files([('a.csv', snapshot('A', 2)), ('b.csv', snapshot('B', 2, currency='EUR'))]))
    original = deepcopy(batch)
    c = prepare_consolidation(batch, declarations=d, latest_prices=prices, fx_history=fx)
    assert not c['ready'] and c['errors']
    assert batch == original


def test_cash_only_zero_quantity_missing_basis_and_empty_source():
    cash = csv({'Account': ['Cash'], 'Ticker': ['CASH'], 'Quantity': [50], 'Current Price': [1], 'Currency': ['EUR'], 'Valuation Date': [TODAY]})
    zero = ingest_files([('zero.csv', snapshot('Zero', 0, cash=10))])
    assert not prepare_consolidation(zero, declarations=declarations())['ready']
    usd_cash = csv({'Account': ['USD Cash'], 'Ticker': ['CASH'], 'Quantity': [10], 'Current Price': [1], 'Currency': ['USD'], 'Valuation Date': [TODAY]})
    batch = confirm_review(ingest_files([('cash.csv', cash), ('usd.csv', usd_cash)]))
    c = prepare_consolidation(batch, declarations=declarations(), fx_history=james_fx())
    assert c['ready'] and c['state']['positions'] == []
    assert c['state']['totals']['equity'] == pytest.approx(65)
    empty = ingest_files([('empty.csv', b'Account,Ticker,Quantity\n')])
    assert not prepare_consolidation(empty, declarations=declarations())['ready']
    unknown = confirm_review(ingest_files([('unknown.csv', snapshot('Unknown', 1, entry=None))]))
    c = prepare_consolidation(unknown, declarations=declarations())
    assert c['ready'] and c['state']['totals']['cost_basis'] is None and c['state']['totals']['unrealised_pnl'] is None


def test_large_realistic_accounts_and_duplicate_ids_across_accounts():
    # 12,000 real normalised ledger records; same IDs across accounts are legitimate.
    rows = [dict(Account=f'A{i}', Date=(pd.Timestamp('2023-01-01') + pd.Timedelta(days=j)).date().isoformat(), Type='BUY', Ticker='X', Quantity=1, Price=2,
                 Currency='USD', **{'Transaction ID': f'T{j}'}) for i in range(12) for j in range(1000)]
    batch = confirm_review(ingest_files([('large.csv', csv(rows))]))
    c = prepare_consolidation(batch, declarations=declarations([f'A{i}' for i in range(12)]), latest_prices={'X': 3})
    assert c['ready'] and c['state']['positions'][0]['quantity'] == 12000
    assert c['state']['totals']['equity'] == 12000
    assert c['reconciliation']['passed']


def test_short_closing_realised_pnl_and_fees_independent_fixture():
    rows = [dict(Account='Short', Date='2026-01-01', Type='SELL', Ticker='Z', Quantity=3, Price=10, Fees=1, Currency='USD'),
            dict(Account='Short', Date='2026-01-02', Type='BUY', Ticker='Z', Quantity=1, Price=8, Fees=.5, Currency='USD')]
    batch = confirm_review(ingest_files([('short.csv', csv(rows))]))
    c = prepare_consolidation(batch, declarations=declarations(['Short']), latest_prices={'Z': 9})
    assert c['ready'], c['errors']
    # Cash30-1-8-.5=20.5; position-2*9=-18; equity2.5;
    # realised1*(10-8)=2; unrealised-2*(9-10)=2. Gross P&L4 less fees1.5=equity2.5.
    for k, v in {'cash': 20.5, 'equity': 2.5, 'cost_basis': 20, 'short_exposure': 18, 'realised_pnl': 2, 'unrealised_pnl': 2}.items():
        assert c['state']['totals'][k] == pytest.approx(v)


def test_ambiguous_cross_source_trade_order_and_snapshot_history_are_not_invented():
    files = [('buy.csv', csv([dict(Account='A', Date='2026-01-01', Type='BUY', Ticker='X', Quantity=1, Price=2, Currency='USD')])),
             ('sell.csv', csv([dict(Account='A', Date='2026-01-01', Type='SELL', Ticker='X', Quantity=1, Price=3, Currency='USD')]))]
    batch = ingest_files(files)
    batch = decide(batch, issue(batch, 'ledger_period_overlap')['id'], 'keep_both', reason='Economically separate buy and sell.')
    batch = confirm_review(batch)
    assert not prepare_consolidation(batch, declarations=declarations(['A']), latest_prices={'X': 3})['ready']
    raw = pd.read_csv(__import__('io').BytesIO(snapshot('Dated', 1)))
    raw['Purchase Date'] = '2026-01-01'
    batch = confirm_review(ingest_files([('dated.csv', raw.to_csv(index=False).encode())]))
    c = prepare_consolidation(batch, declarations=declarations())
    assert c['ready'] and not c['state']['accounting_history']['trades']
    assert not c['state']['consolidation']['accounts']['Dated']['accounting_history']['trades']
    from portfolio_analytics.scenarios.engine import apply_price_shock
    shocked, _ = apply_price_shock(c['state'], 'NVDA', -10)
    assert shocked['totals']['realised_pnl'] is None


def test_different_statement_dates_stale_fx_and_cash_only_ledger():
    batch = confirm_review(ingest_files([('a.csv', snapshot('A', 1)), ('b.csv', snapshot('B', 1, date='2026-01-01'))]))
    assert not prepare_consolidation(batch, declarations=declarations())['ready']
    c = prepare_consolidation(james_reviewed(), declarations=declarations(['Broker']), latest_prices=JAMES_QUOTES, fx_history=james_fx().iloc[:1])
    assert not c['ready'] and any(i['code'] == 'fx_stale' for i in c['trust']['issues'])
    batch = confirm_review(ingest_files([('cash.csv', csv([dict(Account='Cash', Date='2026-01-01', Type='DEPOSIT', Amount=100, Currency='EUR')]))]))
    c = prepare_consolidation(batch, declarations=declarations(['Cash']), fx_history=james_fx())
    assert c['ready'] and c['state']['totals']['equity'] == pytest.approx(110)
    assert c['state']['cash']['external_net_flows'] == pytest.approx(110)


def test_atomic_acceptance_rejects_stale_failed_or_unconfirmed_candidate():
    from portfolio_analytics.ui.consolidation_acceptance import accept_candidate
    from portfolio_analytics.analytics.engine import run_analytics
    batch = maya_reviewed()
    candidate = prepare_consolidation(batch, declarations=declarations())
    candidate['analytics'] = run_analytics(candidate['state'], pd.DataFrame())
    candidate.update(request_key='current', market_metadata={})
    session = {'portfolio_state': {'old': True}, 'analytics': {'old': True}, 'copilot_messages': ['old']}
    baseline = deepcopy(session)
    with pytest.raises(ValueError):
        accept_candidate(session, batch, candidate, 'stale')
    assert session == baseline
    failed = {**candidate, 'ready': False}
    with pytest.raises(ValueError):
        accept_candidate(session, batch, failed, 'current')
    assert session == baseline
    revoked = deepcopy(batch)
    revoked.pop('review_confirmation')
    with pytest.raises(ValueError):
        accept_candidate(session, revoked, candidate, 'current')
    assert session == baseline
    accept_candidate(session, batch, candidate, 'current')
    assert session['portfolio_state']['totals']['equity'] == 4050 and session['copilot_messages'] == []
    assert session['accepted_consolidation_batch']['canonical_acceptance'] == 'accepted'
