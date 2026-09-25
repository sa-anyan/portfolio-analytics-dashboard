"""Historical FX adapters for USD-standardised accounting.

Network access is isolated here. Accounting receives deterministic FX series.
"""
from __future__ import annotations
from typing import Any
import pandas as pd
from portfolio_analytics.core.market_data import _import_yfinance, _extract_close

BASE_CURRENCY = "USD"

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
