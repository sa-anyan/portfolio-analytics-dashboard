#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


#______________________________________________________________________________
# NORMALISE YAHOO CLOSE DATA
#______________________________________________________________________________

def _download_closing_prices(
    tickers: list[str],
    *,
    start: Any,
    end: Any = None,
) -> pd.DataFrame:
    market_data = yf.download(
        tickers,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if market_data.empty:
        return pd.DataFrame()

    closing_prices = market_data["Close"]

    if isinstance(closing_prices, pd.Series):
        column_name = (
            tickers[0]
            if len(tickers) == 1
            else str(closing_prices.name)
        )
        closing_prices = closing_prices.to_frame(
            name=column_name
        )

    if (
        len(tickers) == 1
        and tickers[0] not in closing_prices.columns
        and len(closing_prices.columns) == 1
    ):
        closing_prices = closing_prices.rename(
            columns={
                closing_prices.columns[0]: tickers[0]
            }
        )

    return closing_prices.sort_index()


#______________________________________________________________________________
# DOWNLOAD MARKET DATA
#______________________________________________________________________________

def get_market_data(tickers):
    closing_prices = _download_closing_prices(
        tickers,
        start="2021-01-01",
    )

    valid_tickers = [
        ticker
        for ticker in tickers
        if ticker in closing_prices.columns
        and not closing_prices[ticker].dropna().empty
    ]

    invalid_tickers = [
        ticker
        for ticker in tickers
        if ticker not in valid_tickers
    ]

    closing_prices = closing_prices[
        valid_tickers
    ]

    closing_prices = closing_prices.ffill()

    daily_returns = (
        closing_prices
        .pct_change()
        .dropna()
    )

    return (
        closing_prices,
        daily_returns,
        valid_tickers,
        invalid_tickers,
    )


#______________________________________________________________________________
# DOWNLOAD MARKET DATA FOR A CUSTOM DATE RANGE
#______________________________________________________________________________

def get_market_data_range(
    tickers: list[str],
    start: Any,
    end: Any = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """Return adjusted closing prices and returns for an explicit date range."""
    ordered = list(dict.fromkeys(str(t).strip() for t in tickers if str(t).strip()))

    if not ordered:
        return pd.DataFrame(), pd.DataFrame(), [], []

    closing_prices = _download_closing_prices(
        ordered,
        start=start,
        end=end,
    )

    valid_tickers = [
        ticker
        for ticker in ordered
        if ticker in closing_prices.columns
        and not closing_prices[ticker].dropna().empty
    ]

    invalid_tickers = [
        ticker
        for ticker in ordered
        if ticker not in valid_tickers
    ]

    if not valid_tickers:
        return pd.DataFrame(), pd.DataFrame(), [], invalid_tickers

    closing_prices = closing_prices[valid_tickers].ffill()
    daily_returns = closing_prices.pct_change().dropna(how="all")

    return closing_prices, daily_returns, valid_tickers, invalid_tickers


#______________________________________________________________________________
# HISTORICAL AS-OF PRICE SNAPSHOT
#______________________________________________________________________________

def get_historical_prices(
    tickers: list[str],
    as_of_date: Any,
) -> tuple[
    pd.Series,
    list[str],
    list[str],
    pd.Timestamp | None,
]:
    """
    Return the latest available adjusted close on or before the requested date.

    A short lookback window handles weekends and ordinary market holidays.
    Yahoo's end date is exclusive, so one calendar day is added to the request.
    """
    as_of = pd.Timestamp(as_of_date).normalize()
    start = as_of - pd.Timedelta(days=14)
    end = as_of + pd.Timedelta(days=1)

    closing_prices = _download_closing_prices(
        tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
    )

    if closing_prices.empty:
        return (
            pd.Series(dtype=float),
            [],
            list(tickers),
            None,
        )

    closing_prices = closing_prices.loc[
        closing_prices.index <= as_of
    ]

    if closing_prices.empty:
        return (
            pd.Series(dtype=float),
            [],
            list(tickers),
            None,
        )

    valid_tickers = [
        ticker
        for ticker in tickers
        if ticker in closing_prices.columns
        and not closing_prices[ticker].dropna().empty
    ]

    invalid_tickers = [
        ticker
        for ticker in tickers
        if ticker not in valid_tickers
    ]

    usable = closing_prices[
        valid_tickers
    ].ffill()

    prices = (
        usable.iloc[-1]
        if not usable.empty
        else pd.Series(dtype=float)
    )

    market_as_of = (
        pd.Timestamp(usable.index[-1])
        if not usable.empty
        else None
    )

    return (
        prices,
        valid_tickers,
        invalid_tickers,
        market_as_of,
    )
