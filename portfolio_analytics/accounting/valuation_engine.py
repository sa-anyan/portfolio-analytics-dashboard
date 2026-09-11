#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import pandas as pd

from portfolio_analytics.analytics.market_data import get_historical_prices, get_market_data


#______________________________________________________________________________
# VALUATION MODES
#______________________________________________________________________________

LIVE_MARKET = "Live Market"
HISTORICAL_AS_OF = "Historical As-Of"
FROZEN_SNAPSHOT = "Frozen Test Snapshot"


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def _standardise_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()

    aliases = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }

    return aliases.get(ticker, ticker)


def _normalise_price_series(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)

    result = pd.to_numeric(series, errors="coerce").dropna().copy()
    result.index = [
        _standardise_ticker(value)
        for value in result.index
    ]

    return result[~result.index.duplicated(keep="last")]


def frozen_snapshot_as_of(
    frozen_prices: pd.DataFrame | None,
) -> pd.Timestamp | None:
    if frozen_prices is None or frozen_prices.empty:
        return None

    if "As Of" not in frozen_prices.columns:
        return None

    dates = pd.to_datetime(
        frozen_prices["As Of"],
        errors="coerce",
    ).dropna()

    if dates.empty:
        return None

    return pd.Timestamp(dates.max())


def _frozen_series(
    frozen_prices: pd.DataFrame | None,
) -> pd.Series:
    if frozen_prices is None or frozen_prices.empty:
        return pd.Series(dtype=float)

    if not {"Ticker", "Price"}.issubset(
        frozen_prices.columns
    ):
        return pd.Series(dtype=float)

    data = frozen_prices.copy()
    data["Ticker"] = data["Ticker"].map(
        _standardise_ticker
    )
    data["Price"] = pd.to_numeric(
        data["Price"],
        errors="coerce",
    )

    data = data[
        data["Ticker"].ne("")
        & data["Price"].notna()
        & data["Price"].gt(0)
    ].copy()

    if data.empty:
        return pd.Series(dtype=float)

    if "As Of" in data.columns:
        data["As Of"] = pd.to_datetime(
            data["As Of"],
            errors="coerce",
        )
        data = data.sort_values(
            ["Ticker", "As Of"],
            kind="stable",
        )

    return (
        data.groupby("Ticker", sort=False)["Price"]
        .last()
    )


#______________________________________________________________________________
# RESOLVE VALUATION PRICES
#______________________________________________________________________________

def get_valuation_prices(
    tickers: list[str],
    mode: str,
    *,
    frozen_prices: pd.DataFrame | None = None,
    provided_prices: pd.Series | None = None,
    as_of_date: Any = None,
) -> tuple[pd.Series, list[str], dict[str, Any]]:
    """
    Return one deterministic set of prices for the selected valuation mode.

    Frozen mode never falls back to Yahoo Finance. A missing frozen price is
    therefore visible instead of silently making a reproducibility test live.
    """
    ordered_tickers = [
        _standardise_ticker(ticker)
        for ticker in tickers
    ]

    metadata: dict[str, Any] = {
        "Mode": mode,
        "Price Source": "",
        "Requested As Of": None,
        "Market As Of": None,
        "Strict": False,
    }

    if mode == LIVE_MARKET:
        closing_prices, _, valid, invalid = (
            get_market_data(ordered_tickers)
        )

        if valid:
            prices = (
                closing_prices[valid]
                .ffill()
                .iloc[-1]
            )
            market_as_of = pd.Timestamp(
                closing_prices.index[-1]
            )
        else:
            prices = pd.Series(dtype=float)
            market_as_of = None

        metadata.update({
            "Price Source": (
                "Yahoo Finance latest available market price"
            ),
            "Market As Of": market_as_of,
        })

        return (
            _normalise_price_series(prices),
            invalid,
            metadata,
        )

    if mode == HISTORICAL_AS_OF:
        if as_of_date is None:
            raise ValueError(
                "Historical valuation needs an as-of date."
            )

        prices, valid, invalid, market_as_of = (
            get_historical_prices(
                ordered_tickers,
                as_of_date,
            )
        )

        metadata.update({
            "Price Source": (
                "Yahoo Finance historical adjusted close"
            ),
            "Requested As Of": pd.Timestamp(
                as_of_date
            ),
            "Market As Of": market_as_of,
        })

        return (
            _normalise_price_series(prices),
            invalid,
            metadata,
        )

    if mode == FROZEN_SNAPSHOT:
        embedded = _frozen_series(
            frozen_prices
        )
        supplied = _normalise_price_series(
            provided_prices
        )

        # Embedded workbook test prices take precedence. File-supplied holding
        # prices can fill gaps, but no live market fallback is allowed.
        prices = embedded.copy()

        if not supplied.empty:
            prices = prices.combine_first(
                supplied
            )

        missing = [
            ticker
            for ticker in ordered_tickers
            if ticker not in prices.index
        ]

        source_parts = []

        if not embedded.empty:
            source_parts.append(
                "embedded frozen price snapshot"
            )

        if not supplied.empty:
            source_parts.append(
                "file-supplied holdings prices"
            )

        metadata.update({
            "Price Source": (
                " + ".join(source_parts)
                if source_parts
                else "no frozen price source found"
            ),
            "Market As Of": frozen_snapshot_as_of(
                frozen_prices
            ),
            "Strict": True,
        })

        return prices, missing, metadata

    raise ValueError(
        f"Unknown valuation mode: {mode}"
    )
