"""Explicit daily adjusted-price import; portfolio snapshots never imply history."""
import numpy as np
import pandas as pd
from .parser import read_portfolio_file


def spreadsheet_price_history(data, filename, tickers, period, *, as_of=None):
    frame = read_portfolio_file(data, filename)
    if not {'Date', 'Ticker', 'Adjusted Close'}.issubset(frame.columns):
        raise ValueError('Price history requires Date, Ticker and Adjusted Close columns.')
    frame = frame[['Date', 'Ticker', 'Adjusted Close']].copy()
    frame['Date'] = pd.to_datetime(frame.Date, format='ISO8601', errors='coerce').dt.normalize()
    frame['Ticker'] = frame.Ticker.astype(str).str.strip().str.upper()
    frame['Adjusted Close'] = pd.to_numeric(frame['Adjusted Close'], errors='coerce')
    today = pd.Timestamp.today().normalize()
    if frame.empty or frame.Date.isna().any() or (frame.Date > today).any():
        raise ValueError('Use non-future ISO dates (YYYY-MM-DD) for daily observations.')
    if not np.isfinite(frame['Adjusted Close']).all() or not frame['Adjusted Close'].gt(0).all():
        raise ValueError('Adjusted prices must be finite and positive; omit unavailable observations.')
    if frame.duplicated(['Date', 'Ticker']).any():
        raise ValueError('Duplicate date/security observations require correction.')
    history = frame.pivot(index='Date', columns='Ticker', values='Adjusted Close').sort_index()
    anchor = pd.Timestamp(as_of).normalize() if as_of is not None else today
    history = history.loc[(history.index >= anchor - pd.DateOffset(years=int(period[:-1]))) & (history.index <= anchor)]
    history = history.reindex(columns=list(tickers))
    history.attrs['price_basis'] = 'total_return'
    return history, {'source': 'User spreadsheet adjusted-price history', 'price_basis': 'total_return',
                     'period': period, 'missing': [t for t in tickers if history[t].dropna().empty],
                     'assumption': 'User declares daily total-return adjusted prices in listing currency/units; no fabricated observations'}
