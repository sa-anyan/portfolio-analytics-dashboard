"""Historical FX adapters for USD-standardised accounting.

Network access is isolated here. Accounting receives deterministic FX series.
"""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd
from portfolio_analytics.core.market_data import _import_yfinance, _extract_close

BASE_CURRENCY = "USD"


def aligned_fx(history: pd.DataFrame | None, dates: pd.DatetimeIndex, base: str = BASE_CURRENCY) -> pd.DataFrame:
    """Align FX without look-ahead. GBX means GBP/100, never GBP."""
    dates = pd.DatetimeIndex(dates).normalize()
    result = pd.DataFrame(index=dates)
    if history is not None and not history.empty:
        source = history.copy()
        source.index = pd.to_datetime(source.index).normalize()
        source.columns = [str(c).upper() for c in source.columns]
        source = source.groupby(level=0).last().sort_index().apply(pd.to_numeric, errors="coerce")
        source = source.where(np.isfinite(source) & source.gt(0))
        result = source.reindex(source.index.union(dates)).ffill().reindex(dates)
    result[base.upper()] = 1.0
    if "GBP" in result:
        result["GBX"] = result["GBP"] / 100.0
    return result


def fx_rate(currency: str, *, date: Any = None, history: pd.DataFrame | None = None,
            latest: dict[str, float] | None = None, base: str = BASE_CURRENCY) -> float:
    currency = str(currency or base).upper()
    if currency == base.upper():
        return 1.0
    if date is not None and pd.notna(date):
        frame = aligned_fx(history, pd.DatetimeIndex([pd.Timestamp(date)]), base)
        rate = frame[currency].iloc[0] if currency in frame else np.nan
    else:
        rates = {str(k).upper(): v for k, v in (latest or {}).items()}
        if history is not None and not history.empty:
            for column in history:
                valid = pd.to_numeric(history[column], errors="coerce").dropna()
                if not valid.empty:
                    rates.setdefault(str(column).upper(), float(valid.iloc[-1]))
        if "GBP" in rates:
            rates.setdefault("GBX", float(rates["GBP"]) / 100.0)
        rate = rates.get(currency, np.nan)
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError(f"Missing {currency}/{base} FX rate" + (f" on {pd.Timestamp(date).date()}" if date is not None else " for valuation"))
    return float(rate)

def _pair_ticker(currency: str, base: str = BASE_CURRENCY) -> str:
    return f"{currency.upper()}{base.upper()}=X"

def fetch_fx_history(currencies: list[str], *, start: Any, end: Any = None, base: str = BASE_CURRENCY) -> tuple[pd.DataFrame, dict[str, Any]]:
    base = base.upper()
    needed = sorted({str(c or base).upper() for c in currencies if str(c or base).upper() not in {base, "GBX", "GBPENCE"}} | ({"GBP"} if any(str(c).upper() in {"GBX", "GBPENCE"} for c in currencies) else set()))
    frames: dict[str, pd.Series] = {}
    missing: list[str] = []
    if needed:
        yf = _import_yfinance()
        for ccy in needed:
            found = None
            for ticker, invert in ((_pair_ticker(ccy, base), False), (_pair_ticker(base, ccy), True)):
                kwargs={"tickers":[ticker],"start":start,"interval":"1d","auto_adjust":False,"progress":False,"group_by":"column","threads":False}
                if end is not None: kwargs["end"] = end
                data=yf.download(**kwargs)
                close=_extract_close(data,[ticker],prefer_adjusted=True)
                if not close.empty and not close.iloc[:,0].dropna().empty:
                    series=pd.to_numeric(close.iloc[:,0],errors="coerce")
                    found=(1.0/series) if invert else series
                    break
            if found is None: missing.append(ccy)
            else: frames[ccy]=found
    fx=pd.DataFrame(frames).sort_index() if frames else pd.DataFrame()
    fx[base]=1.0
    if "GBP" in fx.columns: fx["GBX"] = fx["GBP"] / 100.0
    return fx, {"source":"Yahoo Finance","base_currency":base,"missing":missing}
