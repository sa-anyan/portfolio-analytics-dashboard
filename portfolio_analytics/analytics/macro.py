"""Explicit, frequency-aligned historical instrument/rate comparisons."""
from io import StringIO
from urllib.request import urlopen
import ssl
import certifi
import numpy as np
import pandas as pd

SOURCE = 'https://fred.stlouisfed.org/series/FEDFUNDS'
DOWNLOAD = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS'


def fetch_us_policy_rate():
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(DOWNLOAD, timeout=10, context=context) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Policy-rate response exceeds the allowed size.')
    frame = pd.read_csv(StringIO(data.decode('utf-8')))
    if list(frame.columns) not in (['observation_date', 'FEDFUNDS'], ['DATE', 'FEDFUNDS']):
        raise ValueError('Unexpected FRED policy-rate schema.')
    dates = pd.to_datetime(frame.iloc[:, 0], errors='coerce')
    values = pd.to_numeric(frame.FEDFUNDS, errors='coerce')
    series = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(dates), name='US effective federal funds rate (%)')
    series = series[series.index.notna() & np.isfinite(series.to_numpy())]
    if series.empty or series.index.duplicated().any():
        raise ValueError('Policy-rate observations are empty or ambiguous.')
    return series.sort_index(), {'source': SOURCE, 'series': 'FEDFUNDS', 'frequency': 'Monthly average of daily figures',
        'units': 'Percent rate level, not an investment return', 'retrieved_at': pd.Timestamp.now(tz='UTC').isoformat(),
        'start': series.index.min().date().isoformat(), 'end': series.index.max().date().isoformat(),
        'geography': 'United States / USD monetary policy'}


def monthly_comparison(daily_returns, monthly_rates, *, minimum=12):
    returns = pd.to_numeric(daily_returns, errors='coerce').sort_index()
    returns.index = pd.to_datetime(returns.index)
    returns = returns[np.isfinite(returns) & returns.ge(-1)]
    # Exclude boundary months: exported history may start/end mid-month.
    if returns.empty:
        return pd.DataFrame(), {'available': False, 'observations': 0, 'reason': 'Instrument return history unavailable.'}
    monthly = (1 + returns).groupby(returns.index.to_period('M')).prod() - 1
    monthly = monthly.loc[(monthly.index > returns.index.min().to_period('M')) & (monthly.index < returns.index.max().to_period('M'))]
    rates = pd.to_numeric(monthly_rates, errors='coerce').copy()
    rates.index = pd.to_datetime(rates.index).to_period('M')
    if rates.index.duplicated().any():
        raise ValueError('Multiple rate observations per month; supply a validated monthly series.')
    rates = rates[np.isfinite(rates)].sort_index()
    # Never carry missing months across a gap or calculate a multi-month change as monthly.
    rates = rates.reindex(pd.period_range(rates.index.min(), rates.index.max(), freq='M')) if not rates.empty else rates
    frame = pd.concat([monthly.rename('return'), rates.rename('rate_level'), rates.diff().rename('rate_change_pp')], axis=1).dropna()
    correlation = None
    if len(frame) >= minimum and frame['return'].std() > 0 and frame.rate_change_pp.std() > 0:
        correlation = float(frame['return'].corr(frame.rate_change_pp))
    summary = {'available': correlation is not None, 'observations': len(frame), 'correlation': correlation,
               'start': str(frame.index.min()) if not frame.empty else None, 'end': str(frame.index.max()) if not frame.empty else None,
               'method': 'Monthly investment returns versus month-to-month changes in monthly average US policy rate (percentage points).',
               'reason': None if correlation is not None else f'At least {minimum} overlapping months and variable series are required.',
               'limitation': 'Correlation is not causation. Publication revisions and release lags are not modelled; this is descriptive, not a trading signal.'}
    frame.index = frame.index.to_timestamp(how='end').normalize()
    return frame, summary
