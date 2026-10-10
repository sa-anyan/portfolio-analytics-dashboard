"""Validate reporting-currency snapshot evidence before canonical accounting.

This validates supplied identities and amounts; valuation remains in portfolio_state.
Purchase dates and supplied annualised returns never establish account history here.
"""
from copy import deepcopy
import math
import pandas as pd

from portfolio_analytics.input_engine.normalizer import clean_number


def reported_snapshot(parsed, valuation_date):
    raw = parsed.get('user_dataset', {}).get('records', [])
    if parsed.get('classification') != 'holdings' or not raw or 'Market Value (GBP)' not in raw[0]:
        return None
    if valuation_date is None:
        raise ValueError("Declare the statement valuation date.")
    date = pd.Timestamp(valuation_date)
    if pd.isna(date) or date > pd.Timestamp.today().normalize():
        raise ValueError('Declare a valid non-future snapshot valuation date.')
    date = date.normalize()
    candidate = deepcopy(parsed)
    holdings = candidate['normalised_dataset']['holdings']
    if len(raw) != len(holdings):
        raise ValueError('Snapshot has rejected rows; resolve them before valuation.')
    rates, residuals, quotes = {'GBP': 1.0}, [], {}
    for index, (source, row) in enumerate(zip(raw, holdings), 2):
        ticker, currency = row['Ticker'], row['Currency']
        def required(label):
            value = clean_number(source.get(label))
            if value is None or not math.isfinite(value):
                raise ValueError(f'{ticker}, row {index}: missing or invalid {label}.')
            return value
        quantity = float(row['Quantity'])
        price = row.get('Current Price')
        if price is None or not math.isfinite(float(price)) or price <= 0:
            raise ValueError(f'{ticker}: supply a positive current quote in its stated trading units.')
        if ticker in quotes and not math.isclose(quotes[ticker], float(price), rel_tol=1e-8):
            raise ValueError(f'{ticker}: conflicting snapshot quotes across lots.')
        quotes[ticker] = float(price)
        rate = required('FX to GBP')
        if rate <= 0:
            raise ValueError(f'{ticker}: FX to GBP must be positive.')
        expected_base_rate = rate * 100 if currency == 'GBX' else rate
        parent = 'GBP' if currency == 'GBX' else currency
        if parent in rates and not math.isclose(rates[parent], expected_base_rate, rel_tol=1e-8, abs_tol=1e-8):
            raise ValueError(f'{ticker}: conflicting {currency}/GBP conversion rates.')
        rates[parent] = expected_base_rate
        market = required('Market Value (GBP)')
        residual = quantity * float(price) * rate - market
        tolerance = max(.02, abs(market) * 1e-8)
        if abs(residual) > tolerance:
            raise ValueError(f'{ticker}: quantity × local quote × FX does not reconcile to supplied GBP market value ({residual:,.2f} GBP). Check quote units.')
        if ticker == 'CASH' or row.get('Asset Class', '').lower() == 'cash':
            if not math.isclose(float(price), 1.0):
                raise ValueError(f'{ticker}: cash must use one currency unit per quantity.')
        else:
            cost = required('Cost Basis (GBP)')
            pnl = required('Unrealised P/L (GBP)')
            if cost <= 0 or abs((market - math.copysign(cost, quantity)) - pnl) > tolerance:
                raise ValueError(f'{ticker}: supplied cost basis and unrealised P&L do not reconcile.')
            row['Reported Cost Basis'] = cost
            row['Reported Cost Currency'] = 'GBP'
        residuals.append({'ticker': ticker, 'market_value_residual': residual})
    total = sum(clean_number(r['Market Value (GBP)']) for r in raw)
    if total > 0:
        for source in raw:
            if source.get('Weight') is not None:
                weight = clean_number(source['Weight'])
                if weight is None or abs(weight - clean_number(source['Market Value (GBP)']) / total) > 1e-5:
                    raise ValueError('Supplied weights do not reconcile to signed GBP snapshot values.')
    # Source rates are dated valuation evidence only, never fabricated historical FX.
    fx = pd.DataFrame([rates], index=[date])
    candidate['variables']['history_capability'] = {'available': False, 'method': None, 'dated_positions': 0}
    candidate['inputs']['snapshot_only'] = True
    candidate['source']['snapshot_validation'] = {'reporting_currency': 'GBP', 'valuation_date': date.date().isoformat(),
        'supplied_total': total, 'residuals': residuals, 'cost_basis': 'Supplied GBP cost basis; historical FX not inferred'}
    return candidate, fx
