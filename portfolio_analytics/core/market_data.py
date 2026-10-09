"""Market-data adapters for valuation and historical analytics.

Network access is isolated here so accounting and analytics remain deterministic
when supplied with explicit prices/history during tests or reproducible runs.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import pandas as pd


#______________________________________________________________________________
# HELPERS
#______________________________________________________________________________

RESERVED_NON_MARKET_TICKERS = {"CASH"}


def _clean_tickers(tickers: list[str]) -> list[str]:
    """Return unique market tickers, excluding reserved non-market assets."""
    cleaned = (str(t).strip().upper() for t in tickers if str(t).strip())
    return list(dict.fromkeys(t for t in cleaned if t not in RESERVED_NON_MARKET_TICKERS))


def _import_yfinance():
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is not installed. Install project requirements or provide prices manually."
        ) from exc
    return yf


def _extract_close(
    downloaded: pd.DataFrame,
    tickers: list[str],
    *,
    prefer_adjusted: bool = False,
) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()

    data = downloaded.copy()

    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(map(str, data.columns.get_level_values(0)))
        candidates = ["Adj Close", "Close"] if prefer_adjusted else ["Close", "Adj Close"]
        price_field = next((field for field in candidates if field in level0), None)
        if price_field is None:
            return pd.DataFrame()
        close = data[price_field].copy()
    else:
        candidates = ["Adj Close", "Close"] if prefer_adjusted else ["Close", "Adj Close"]
        price_col = next((field for field in candidates if field in data.columns), None)
        if price_col is None:
            return pd.DataFrame()
        close = data[[price_col]].copy()
        if len(tickers) == 1:
            close.columns = [tickers[0]]

    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0] if tickers else "Price")

    close.columns = [str(c).strip().upper() for c in close.columns]
    close.index = pd.to_datetime(close.index, errors="coerce")
    close = close.loc[~close.index.isna()].sort_index()
    return close.apply(pd.to_numeric, errors="coerce")


#______________________________________________________________________________
# LIVE VALUATION
#______________________________________________________________________________

def fetch_latest_prices(tickers: list[str]) -> tuple[dict[str, float], dict[str, Any]]:
    ordered = _clean_tickers(tickers)
    if not ordered:
        return {}, {"source": "none", "as_of": None, "missing": []}

    yf = _import_yfinance()
    downloaded = yf.download(
        tickers=ordered,
        period="10d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
        timeout=15,
    )
    close = _extract_close(downloaded, ordered, prefer_adjusted=False)

    latest: dict[str, float] = {}
    observations: dict[str, dict[str, Any]] = {}
    if not close.empty:
        for ticker in ordered:
            if ticker in close.columns:
                series = pd.to_numeric(close[ticker], errors="coerce").dropna()
                if not series.empty:
                    latest[ticker] = float(series.iloc[-1])
                    observations[ticker] = {"as_of": series.index[-1].isoformat(), "source": "Yahoo Finance"}

    missing = [ticker for ticker in ordered if ticker not in latest]
    as_of = close.index.max() if not close.empty else None

    return latest, {
        "source": "Yahoo Finance",
        "as_of": as_of.isoformat() if as_of is not None else None,
        "missing": missing,
        "observations": observations,
    }


#______________________________________________________________________________
# HISTORICAL PRICES
#______________________________________________________________________________

def fetch_price_history(
    tickers: list[str],
    *,
    period: str = "3y",
    start: Any = None,
    end: Any = None,
    price_basis: str = "total_return",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if price_basis not in {"total_return", "accounting", "split_adjusted_close"}:
        raise ValueError("Unknown historical price basis.")
    ordered = _clean_tickers(tickers)
    if not ordered:
        return pd.DataFrame(), {"source": "none", "missing": []}

    yf = _import_yfinance()
    kwargs: dict[str, Any] = {
        "tickers": ordered,
        "interval": "1d",
        "auto_adjust": False,
        "progress": False,
        "group_by": "column",
        "threads": True, "timeout": 15,
        "actions": True,
    }
    if start is not None:
        kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
    else:
        kwargs["period"] = period

    downloaded = yf.download(**kwargs)
    fields = set(downloaded.columns.get_level_values(0)) if isinstance(downloaded.columns, pd.MultiIndex) else set(downloaded.columns)
    if price_basis == "total_return" and not downloaded.empty and "Adj Close" not in fields:
        raise ValueError("Adjusted-close total-return data is unavailable; raw closes cannot silently replace it.")
    close = _extract_close(downloaded, ordered, prefer_adjusted=price_basis == "total_return")
    splits = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    if isinstance(downloaded.columns, pd.MultiIndex) and "Stock Splits" in downloaded.columns.get_level_values(0):
        splits = downloaded["Stock Splits"].reindex(index=close.index, columns=close.columns).fillna(0.0)
    elif len(ordered) == 1 and "Stock Splits" in downloaded:
        splits[ordered[0]] = downloaded["Stock Splits"].fillna(0.0)
    if price_basis == "accounting":
        # Yahoo Close is split-adjusted retrospectively. Undo only future splits
        # so historical executions are valued in the actual share units that day.
        factors = splits.where(splits.ne(0), 1.0).iloc[::-1].cumprod().iloc[::-1]
        factors = factors / splits.where(splits.ne(0), 1.0)
        close = close * factors
    close.attrs["price_basis"] = price_basis
    splits.index = pd.to_datetime(splits.index).normalize()
    close.attrs["splits"] = splits.loc[splits.ne(0).any(axis=1)]
    missing = [ticker for ticker in ordered if ticker not in close.columns or close[ticker].dropna().empty]

    return close, {
        "source": "Yahoo Finance",
        "price_basis": price_basis,
        "start": close.index.min().isoformat() if not close.empty else None,
        "end": close.index.max().isoformat() if not close.empty else None,
        "missing": missing,
    }
