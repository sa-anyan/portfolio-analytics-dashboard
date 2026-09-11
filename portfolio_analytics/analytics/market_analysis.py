from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class MarketDataset:
    observations: pd.DataFrame
    prices: pd.DataFrame
    returns: pd.DataFrame
    price_field: str
    issues: pd.DataFrame


def _to_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype(str)
        .str.replace(r"[$£€,%]", "", regex=True)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"": np.nan, "nan": np.nan, "None": np.nan, "<NA>": np.nan})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def standardise_market_data(
    frame: pd.DataFrame,
    schema: dict[str, dict[str, Any]],
    *,
    inferred_ticker: str | None = None,
) -> MarketDataset:
    """Convert a recognised long-format price-history file into an analytics-ready matrix.

    The source frame is not mutated. Adjusted close is preferred when available,
    then close. Returns supplied by the file are retained only as a cross-check;
    analytics returns are derived from the chosen price series so the calculation
    is reproducible and consistent across files.
    """
    if frame is None or frame.empty:
        raise ValueError("Market-data file is empty.")

    date_role = "datetime" if "datetime" in schema else "date"
    date_col = schema.get(date_role, {}).get("column")
    ticker_col = schema.get("ticker", {}).get("column")

    if not date_col or date_col not in frame.columns:
        raise ValueError("A market-data date column is required.")
    if (not ticker_col or ticker_col not in frame.columns) and not inferred_ticker:
        raise ValueError(
            "A market-data ticker column is required unless a single-security symbol "
            "can be inferred from the source name."
        )

    price_role = None
    for candidate in ["adjusted_close", "close_price", "price"]:
        column = schema.get(candidate, {}).get("column")
        if column and column in frame.columns:
            price_role = candidate
            break
    if price_role is None:
        raise ValueError("Adjusted Close or Close price is required for return analytics.")

    price_col = schema[price_role]["column"]
    if ticker_col and ticker_col in frame.columns:
        ticker_series = frame[ticker_col].astype(str).str.strip().str.upper()
    else:
        ticker_series = pd.Series(
            [str(inferred_ticker).strip().upper()] * len(frame),
            index=frame.index,
            dtype="object",
        )

    data = pd.DataFrame({
        "Date": pd.to_datetime(frame[date_col], errors="coerce"),
        "Ticker": ticker_series,
        "Price": _to_numeric(frame[price_col]),
    })
    data["Source Row"] = np.arange(1, len(frame) + 1)

    issues: list[dict[str, Any]] = []

    invalid_date = data["Date"].isna()
    for row in data.loc[invalid_date, "Source Row"].tolist():
        issues.append({"Source Row": int(row), "Issue": "Invalid or missing date", "Severity": "REVIEW"})

    invalid_ticker = data["Ticker"].isin({"", "NAN", "NONE", "<NA>"})
    for row in data.loc[invalid_ticker, "Source Row"].tolist():
        issues.append({"Source Row": int(row), "Issue": "Missing ticker", "Severity": "REVIEW"})

    invalid_price = data["Price"].isna() | data["Price"].le(0)
    for row in data.loc[invalid_price, "Source Row"].tolist():
        issues.append({"Source Row": int(row), "Issue": "Invalid or missing price", "Severity": "REVIEW"})

    valid = data.loc[~(invalid_date | invalid_ticker | invalid_price)].copy()
    valid = valid.sort_values(["Ticker", "Date", "Source Row"])

    duplicate_mask = valid.duplicated(subset=["Ticker", "Date"], keep=False)
    for _, row in valid.loc[duplicate_mask, ["Source Row"]].iterrows():
        issues.append({
            "Source Row": int(row["Source Row"]),
            "Issue": "Duplicate ticker/date observation; latest source row kept",
            "Severity": "WARNING",
        })

    valid = valid.drop_duplicates(subset=["Ticker", "Date"], keep="last")

    prices = (
        valid.pivot(index="Date", columns="Ticker", values="Price")
        .sort_index()
        .sort_index(axis=1)
    )
    prices.index = pd.DatetimeIndex(prices.index)

    returns = prices.pct_change(fill_method=None)
    returns = returns.replace([np.inf, -np.inf], np.nan)

    # Keep rows where at least one asset has a valid return. Individual series
    # retain NaN where an asset lacks the necessary adjacent observation.
    returns = returns.dropna(how="all")

    return MarketDataset(
        observations=valid.reset_index(drop=True),
        prices=prices,
        returns=returns,
        price_field=price_role,
        issues=pd.DataFrame(issues, columns=["Source Row", "Issue", "Severity"]),
    )


def _max_drawdown_from_returns(daily_returns: pd.Series) -> float:
    series = pd.to_numeric(daily_returns, errors="coerce").dropna()
    if series.empty:
        return float("nan")
    wealth = (1.0 + series).cumprod()
    peak = wealth.cummax()
    drawdown = wealth / peak - 1.0
    return float(drawdown.min())


def series_metrics(daily_returns: pd.Series, risk_free_rate: float = 0.0, var_level: float = 0.95) -> dict[str, float]:
    r = pd.to_numeric(daily_returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if r.empty:
        return {
            "observations": 0, "annual_return": np.nan, "annual_volatility": np.nan,
            "sharpe": np.nan, "var": np.nan, "expected_shortfall": np.nan,
            "max_drawdown": np.nan,
        }

    mean_daily = float(r.mean())
    annual_return = mean_daily * TRADING_DAYS
    annual_volatility = float(r.std(ddof=1)) * np.sqrt(TRADING_DAYS) if len(r) > 1 else np.nan
    sharpe = (
        (annual_return - float(risk_free_rate)) / annual_volatility
        if np.isfinite(annual_volatility) and annual_volatility > 0
        else np.nan
    )

    tail_cutoff = float(r.quantile(1.0 - var_level))
    var = max(0.0, -tail_cutoff)
    tail = r[r <= tail_cutoff]
    expected_shortfall = max(0.0, -float(tail.mean())) if not tail.empty else np.nan

    return {
        "observations": int(len(r)),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility),
        "sharpe": float(sharpe) if np.isfinite(sharpe) else np.nan,
        "var": float(var),
        "expected_shortfall": float(expected_shortfall) if np.isfinite(expected_shortfall) else np.nan,
        "max_drawdown": _max_drawdown_from_returns(r),
    }


def asset_metrics(returns: pd.DataFrame, risk_free_rate: float = 0.0, var_level: float = 0.95) -> pd.DataFrame:
    rows = []
    for ticker in returns.columns:
        metrics = series_metrics(returns[ticker], risk_free_rate=risk_free_rate, var_level=var_level)
        rows.append({"Ticker": ticker, **metrics})
    return pd.DataFrame(rows)


def normalise_weights(weights: pd.Series, tickers: list[str]) -> pd.Series:
    w = pd.Series(weights, dtype=float).reindex(tickers).fillna(0.0)
    total = float(w.sum())
    if not np.isfinite(total) or total <= 0:
        w[:] = 1.0 / len(tickers)
        return w
    return w / total


def portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    if returns.empty:
        return pd.Series(dtype=float, name="Portfolio Return")
    cols = [str(c) for c in returns.columns]
    w = normalise_weights(weights, cols)
    # A portfolio return requires all selected assets to have a return on that
    # date. This avoids silently treating missing asset returns as zero.
    selected = returns.loc[:, w[w > 0].index].dropna(how="any")
    if selected.empty:
        return pd.Series(dtype=float, name="Portfolio Return")
    values = selected.to_numpy(dtype=float) @ w.loc[selected.columns].to_numpy(dtype=float)
    return pd.Series(values, index=selected.index, name="Portfolio Return")


def portfolio_metrics(
    returns: pd.DataFrame,
    weights: pd.Series,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, float]:
    p_returns = portfolio_returns(returns, weights)
    return series_metrics(p_returns, risk_free_rate=risk_free_rate, var_level=var_level)


def risk_contribution(returns: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    if returns.empty:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])
    tickers = [str(c) for c in returns.columns]
    w = normalise_weights(weights, tickers)
    selected = returns.loc[:, w[w > 0].index].dropna(how="any")
    if len(selected) < 2:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])

    cov = selected.cov().to_numpy(dtype=float) * TRADING_DAYS
    wv = w.loc[selected.columns].to_numpy(dtype=float)
    variance = float(wv @ cov @ wv)
    if not np.isfinite(variance) or variance <= 0:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])
    sigma = np.sqrt(variance)
    marginal = cov @ wv / sigma
    component = wv * marginal
    pct = component / sigma

    return pd.DataFrame({
        "Ticker": selected.columns,
        "Weight": wv,
        "Risk Contribution": component,
        "Risk Contribution %": pct,
    })




def signed_portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Return a static current-book return series using signed weights as supplied.

    Unlike ``portfolio_returns`` this function deliberately does not normalise
    weights or remove negative exposures.  It is therefore suitable for a
    long/short open-position book where weights may represent current signed
    market value divided by account equity (cash then behaves as a zero-return
    residual).
    """
    if returns.empty:
        return pd.Series(dtype=float, name="Portfolio Return")

    cols = [str(c) for c in returns.columns]
    w = pd.Series(weights, dtype=float).reindex(cols).fillna(0.0)
    active = w[w != 0.0]
    if active.empty:
        return pd.Series(dtype=float, name="Portfolio Return")

    selected = returns.loc[:, active.index].dropna(how="any")
    if selected.empty:
        return pd.Series(dtype=float, name="Portfolio Return")

    values = selected.to_numpy(dtype=float) @ active.loc[selected.columns].to_numpy(dtype=float)
    return pd.Series(values, index=selected.index, name="Portfolio Return")


def signed_portfolio_metrics(
    returns: pd.DataFrame,
    weights: pd.Series,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, float]:
    """Risk/performance metrics for a static signed long/short current book."""
    p_returns = signed_portfolio_returns(returns, weights)
    return series_metrics(p_returns, risk_free_rate=risk_free_rate, var_level=var_level)


def signed_risk_contribution(returns: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    """Component volatility contribution for signed long/short weights.

    Contributions may be negative when an exposure hedges portfolio volatility.
    The percentages sum to 100% when portfolio volatility is positive.
    """
    if returns.empty:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])

    cols = [str(c) for c in returns.columns]
    w = pd.Series(weights, dtype=float).reindex(cols).fillna(0.0)
    active = w[w != 0.0]
    if active.empty:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])

    selected = returns.loc[:, active.index].dropna(how="any")
    if len(selected) < 2:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])

    cov = selected.cov().to_numpy(dtype=float) * TRADING_DAYS
    wv = active.loc[selected.columns].to_numpy(dtype=float)
    variance = float(wv @ cov @ wv)
    if not np.isfinite(variance) or variance <= 0:
        return pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])

    sigma = np.sqrt(variance)
    marginal = cov @ wv / sigma
    component = wv * marginal
    pct = component / sigma

    return pd.DataFrame({
        "Ticker": selected.columns,
        "Weight": wv,
        "Risk Contribution": component,
        "Risk Contribution %": pct,
    })


def monte_carlo_portfolios(
    returns: pd.DataFrame,
    risk_free_rate: float = 0.0,
    simulations: int = 10_000,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate long-only fully-invested portfolios for multi-asset market data."""
    complete = returns.dropna(how="any")
    if complete.shape[1] < 2 or len(complete) < 2:
        return pd.DataFrame(), pd.DataFrame()

    tickers = list(complete.columns)
    mean = complete.mean().to_numpy(dtype=float) * TRADING_DAYS
    cov = complete.cov().to_numpy(dtype=float) * TRADING_DAYS

    rng = np.random.default_rng(seed)
    raw = rng.random((int(simulations), len(tickers)))
    w = raw / raw.sum(axis=1, keepdims=True)

    annual_returns = w @ mean
    variances = np.einsum("ij,jk,ik->i", w, cov, w)
    volatility = np.sqrt(np.clip(variances, 0.0, None))
    sharpe = np.divide(
        annual_returns - float(risk_free_rate),
        volatility,
        out=np.full_like(volatility, np.nan),
        where=volatility > 0,
    )

    simulations_df = pd.DataFrame({
        "Annual Return": annual_returns,
        "Annual Volatility": volatility,
        "Sharpe": sharpe,
    })
    for idx, ticker in enumerate(tickers):
        simulations_df[f"Weight {ticker}"] = w[:, idx]

    candidates = []
    if not simulations_df.empty:
        indices = {
            "Max Sharpe": simulations_df["Sharpe"].idxmax(),
            "Min Volatility": simulations_df["Annual Volatility"].idxmin(),
            "Max Return": simulations_df["Annual Return"].idxmax(),
        }
        for label, index in indices.items():
            row = simulations_df.loc[index]
            result = {
                "Portfolio": label,
                "Annual Return": row["Annual Return"],
                "Annual Volatility": row["Annual Volatility"],
                "Sharpe": row["Sharpe"],
            }
            for ticker in tickers:
                result[ticker] = row[f"Weight {ticker}"]
            candidates.append(result)

    return simulations_df, pd.DataFrame(candidates)
