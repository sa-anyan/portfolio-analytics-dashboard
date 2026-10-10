"""Deterministic analytics engine for the canonical Portfolio State."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from portfolio_analytics.core.fx import aligned_fx


#______________________________________________________________________________
# CONSTANTS
#______________________________________________________________________________

TRADING_DAYS = 252


def _drawdown(wealth: pd.Series) -> pd.Series:
    return wealth / wealth.cummax().clip(lower=1.0) - 1.0


def _validate_var_level(level: float) -> None:
    if not np.isfinite(level) or not 0.0 < level < 1.0:
        raise ValueError("VaR confidence must be strictly between zero and one.")


def _base_prices(state: dict[str, Any], prices: pd.DataFrame, fx_history: pd.DataFrame | None) -> pd.DataFrame:
    base = state.get("meta", {}).get("base_currency", "USD")
    result = prices.copy()
    # Transport metadata may contain DataFrames (split events). Keep it on the
    # source history, not arithmetic Series where pandas compares attrs on concat.
    result.attrs = {}
    result.columns = [str(c).upper() for c in result.columns]
    result.index = pd.to_datetime(result.index).normalize()
    # One common business-day calendar, including crypto and international assets.
    result = result.loc[result.index.dayofweek < 5]
    fx = aligned_fx(fx_history, result.index, base)
    positions = {str(row.get("Ticker") or "").upper(): str(row.get("Currency") or base).upper()
                 for key in ("holdings", "ledger") for row in state.get("inputs", {}).get("normalised_dataset", {}).get(key, [])}
    positions.update({str(row.get("ticker") or "").upper(): str(row.get("currency") or base).upper() for row in state.get("positions", [])})
    for ticker, ccy in positions.items():
        if ticker in result:
            result[ticker] = result[ticker] * (fx[ccy] if ccy in fx else np.nan)
    for ccy, amount in state.get("cash", {}).get("balances", {}).items():
        if ccy != base and abs(float(amount)) > 1e-12:
            result[f"CASH:{ccy}"] = fx[ccy] if ccy in fx else np.nan
    return result


def _coverage(state: dict[str, Any], returns: pd.DataFrame) -> dict[str, Any]:
    required = [str(row.get("ticker") or "").upper() for row in state.get("positions", []) if abs(float(row.get("quantity") or 0)) > 1e-15]
    base = state.get("meta", {}).get("base_currency", "USD")
    required += [f"CASH:{ccy}" for ccy, amount in state.get("cash", {}).get("balances", {}).items() if ccy != base and abs(float(amount)) > 1e-12]
    missing = [t for t in required if t not in returns or returns[t].notna().sum() < 2]
    common = returns[required].dropna() if required and not missing else pd.DataFrame()
    equity = float(state.get("totals", {}).get("equity") or 0)
    reason = None
    if state.get("meta", {}).get("missing_prices"):
        reason = "Current valuation is incomplete."
    elif equity <= 0:
        reason = "Positive portfolio equity is required for investor-return risk metrics."
    elif missing:
        reason = "Missing return/FX coverage: " + ", ".join(missing)
    elif required and len(common) < 2:
        reason = "Insufficient common observations for the complete portfolio."
    return {"available": reason is None, "required_tickers": required, "missing_tickers": missing,
            "common_observations": len(common), "reason": reason}


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def _returns_from_prices(price_history: pd.DataFrame) -> pd.DataFrame:
    if price_history is None or price_history.empty:
        return pd.DataFrame()
    prices = price_history.copy()
    prices.columns = [str(c).strip().upper() for c in prices.columns]
    prices.index = pd.to_datetime(prices.index, errors="coerce")
    prices = prices.loc[~prices.index.isna()].sort_index()
    prices = prices.apply(pd.to_numeric, errors="coerce")
    return prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna(how="all")


def _series_metrics(
    daily_returns: pd.Series,
    *,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, float | int | None]:
    _validate_var_level(var_level)
    r = pd.to_numeric(daily_returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if r.empty:
        return {
            "observations": 0,
            "annual_return": None,
            "annual_volatility": None,
            "sharpe": None,
            "var_pct": None,
            "expected_shortfall_pct": None,
            "max_drawdown": None,
        }

    mean_daily = float(r.mean())
    annual_return = mean_daily * TRADING_DAYS
    annual_volatility = float(r.std(ddof=1)) * np.sqrt(TRADING_DAYS) if len(r) > 1 else np.nan
    if not np.isfinite(risk_free_rate) or risk_free_rate <= -1:
        raise ValueError("Risk-free rate must be a finite effective annual rate greater than -100%.")
    daily_rf = (1.0 + float(risk_free_rate)) ** (1.0 / TRADING_DAYS) - 1.0
    sharpe = (
        (mean_daily - daily_rf) * TRADING_DAYS / annual_volatility
        if np.isfinite(annual_volatility) and annual_volatility > 0
        else np.nan
    )

    cutoff = float(r.quantile(1.0 - var_level))
    var_pct = max(0.0, -cutoff)
    tail = r[r <= cutoff]
    es_pct = max(0.0, -float(tail.mean())) if not tail.empty else np.nan

    wealth = (1.0 + r).cumprod()
    drawdown = _drawdown(wealth)

    return {
        "observations": int(len(r)),
        "annual_return": float(annual_return),
        "annual_return_method": "arithmetic mean daily return × 252",
        "geometric_annual_return": float(wealth.iloc[-1] ** (TRADING_DAYS / len(r)) - 1) if (r > -1).all() else None,
        "tail_observations": int(len(tail)),
        "annual_volatility": float(annual_volatility) if np.isfinite(annual_volatility) else None,
        "sharpe": float(sharpe) if np.isfinite(sharpe) else None,
        "var_pct": float(var_pct),
        "expected_shortfall_pct": float(es_pct) if np.isfinite(es_pct) else None,
        "max_drawdown": float(drawdown.min()) if (r > -1).all() else None,
        "compounding_available": bool((r > -1).all()),
    }


def _current_signed_weights(state: dict[str, Any], return_columns: list[str]) -> pd.Series:
    equity = float(state.get("totals", {}).get("equity") or 0.0)
    required = [str(row.get("ticker") or "").upper() for row in state.get("positions", [])]
    weights = pd.Series(0.0, index=list(dict.fromkeys(return_columns + required)), dtype=float)
    if equity <= 1e-12:
        return weights

    for row in state.get("positions", []):
        ticker = str(row.get("ticker") or "").upper()
        value = row.get("signed_market_value")
        if ticker in weights.index and value is not None:
            weights.loc[ticker] += float(value) / equity
    base = state.get("meta", {}).get("base_currency", "USD")
    for ccy, amount in state.get("cash", {}).get("balances", {}).items():
        if ccy != base and abs(float(amount)) > 1e-12:
            weights.loc[f"CASH:{ccy}"] = float(amount) * float(state["cash"]["valuation_fx"][ccy]) / equity
    return weights


def _static_portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    active = weights[weights.abs() > 1e-15]
    if active.empty:
        return pd.Series(0.0, index=returns.index, name="Portfolio Return")
    available = [
        ticker for ticker in active.index
        if ticker in returns.columns and pd.to_numeric(returns[ticker], errors="coerce").notna().sum() >= 2
    ]
    if len(available) != len(active):
        return pd.Series(dtype=float, name="Portfolio Return")
    if not available:
        return pd.Series(dtype=float, name="Portfolio Return")
    selected = returns[available].dropna(how="any")
    if selected.empty:
        return pd.Series(dtype=float, name="Portfolio Return")
    values = selected.to_numpy(dtype=float) @ active.loc[available].to_numpy(dtype=float)
    return pd.Series(values, index=selected.index, name="Portfolio Return")


def _risk_contribution(returns: pd.DataFrame, weights: pd.Series) -> list[dict[str, Any]]:
    active = weights[weights.abs() > 1e-15]
    available = [
        ticker for ticker in active.index
        if ticker in returns.columns and pd.to_numeric(returns[ticker], errors="coerce").notna().sum() >= 2
    ]
    if len(available) != len(active) or not available:
        return []
    selected = returns[available].dropna(how="any")
    if len(selected) < 2:
        return []

    cov = selected.cov().to_numpy(dtype=float) * TRADING_DAYS
    w = active.loc[available].to_numpy(dtype=float)
    variance = float(w @ cov @ w)
    if not np.isfinite(variance) or variance <= 0:
        return []

    sigma = np.sqrt(variance)
    marginal = cov @ w / sigma
    component = w * marginal
    raw_pct = component / sigma
    absolute_component = np.abs(component)
    absolute_total = float(absolute_component.sum())
    abs_share = absolute_component / absolute_total if absolute_total > 0 else np.zeros_like(component)

    return [
        {
            "ticker": ticker,
            "weight": float(weight),
            "risk_contribution": float(comp),
            "risk_contribution_pct": float(pct),
            "absolute_risk_share": float(abs_pct),
        }
        for ticker, weight, comp, pct, abs_pct in zip(available, w, component, raw_pct, abs_share)
    ]


def _correlation_matrix(returns: pd.DataFrame, tickers: list[str]) -> dict[str, dict[str, float | None]]:
    available = [ticker for ticker in tickers if ticker in returns.columns]
    if not available:
        return {}
    corr = returns[available].corr()
    output: dict[str, dict[str, float | None]] = {}
    for row_ticker in corr.index:
        output[str(row_ticker)] = {
            str(col): (float(corr.loc[row_ticker, col]) if pd.notna(corr.loc[row_ticker, col]) else None)
            for col in corr.columns
        }
    return output


#______________________________________________________________________________
# LEDGER HISTORICAL PERFORMANCE PATH
#______________________________________________________________________________

def _history_inputs(state, price_history, fx_history, base_currency):
    history = state.get("accounting_history", {})
    if history.get("missing_dated_basis"):
        raise ValueError("Incomplete dated holdings: " + ", ".join(history["missing_dated_basis"]))
    trades = pd.DataFrame(history.get("trades", state.get("inputs", {}).get("normalised_dataset", {}).get("ledger", [])))
    flows = pd.DataFrame(history.get("cashflows", state.get("inputs", {}).get("normalised_dataset", {}).get("cashflows", [])))
    prices = price_history.copy()
    if prices.empty:
        raise ValueError("Historical account prices are unavailable.")
    if prices.attrs.get("price_basis") == "total_return":
        raise ValueError("Account reconstruction requires accounting marks, not dividend-adjusted total-return prices.")
    prices.columns = [str(c).upper() for c in prices.columns]
    prices.index = pd.to_datetime(prices.index).normalize()
    prices = prices.groupby(level=0).last().sort_index().apply(pd.to_numeric, errors="coerce")
    prices = prices.loc[prices.index.dayofweek < 5]
    for events in (trades, flows):
        if not events.empty:
            events["Date"] = pd.to_datetime(events["Date"]).dt.normalize()
            # Weekend events are booked at the next business-day valuation.
            events["Date"] = events["Date"].map(lambda d: d + pd.offsets.BDay(0))
            events["Currency"] = events.get("Currency", pd.Series(base_currency, index=events.index)).fillna(base_currency).astype(str).str.upper()
    if trades.empty:
        raise ValueError("No security accounting events are available.")
    trades["Ticker"] = trades["Ticker"].astype(str).str.upper()
    tickers = sorted(trades["Ticker"].unique())
    missing = sorted(set(tickers) - set(prices.columns))
    if missing:
        raise ValueError("Missing historical prices: " + ", ".join(missing))
    first = trades["Date"].min()
    if not flows.empty:
        first = min(first, flows["Date"].min())
    last_event = max(trades["Date"].max(), flows["Date"].max() if not flows.empty else first)
    if last_event > prices.index.max():
        raise ValueError("Accounting events fall after the available market history.")
    timeline = pd.bdate_range(first, max(prices.index.max(), trades["Date"].max(), flows["Date"].max() if not flows.empty else first))
    prices = prices.reindex(prices.index.union(timeline)).ffill().reindex(timeline)[tickers]
    fx = aligned_fx(fx_history, timeline, base_currency)
    currencies = set(trades["Currency"]) | (set(flows["Currency"]) if not flows.empty else set())
    currencies |= set(state.get("cash", {}).get("balances", {}))
    missing_fx = currencies - set(fx.columns)
    if missing_fx:
        raise ValueError("Missing historical FX: " + ", ".join(sorted(missing_fx)))
    for ccy in currencies:
        if fx[ccy].isna().any():
            raise ValueError(f"Incomplete historical FX coverage for {ccy}.")
    currency_map = {}
    for ticker, group in trades.groupby("Ticker"):
        if group["Currency"].nunique() != 1:
            raise ValueError(f"Conflicting quote currencies for {ticker}.")
        currency_map[ticker] = group["Currency"].iloc[0]
    return trades, flows, prices, fx, currency_map


def _finish_curve(cash, values, external, opening, *, position_pnl=None):
    if values.isna().any().any() or cash.isna().any():
        raise ValueError("Incomplete active-position price/FX coverage; account performance was withheld.")
    equity = cash + values.sum(axis=1)
    previous = equity.shift(1)
    previous.iloc[0] = opening
    if (previous.dropna() < 0).any() or (equity < 0).any():
        raise ValueError("Non-positive account equity cannot support ordinary investor-return performance.")
    daily = (equity - previous - external) / previous.where(previous > 1e-12)
    wealth = (1 + daily.fillna(0.0)).cumprod()
    curve = pd.DataFrame({"cash": cash, "market_value": values.sum(axis=1), "equity": equity,
                          "external_flow": external, "daily_return": daily,
                          "cumulative_return": wealth - 1, "drawdown": _drawdown(wealth)})
    for ticker in values:
        curve[f"position__{ticker}"] = values[ticker]
        if position_pnl is not None:
            curve[f"pnl__{ticker}"] = position_pnl[ticker]
    curve.attrs["opening_capital"] = opening
    curve.attrs["cash_flow_timing"] = "end-of-day; weekend events booked next business day"
    return curve


def _ledger_equity_curve(state: dict[str, Any], price_history: pd.DataFrame, fx_history: pd.DataFrame | None = None, base_currency: str = "USD") -> pd.DataFrame:
    trades, flows, prices, fx, ccy_map = _history_inputs(state, price_history, fx_history, base_currency)
    timeline, tickers = prices.index, list(prices.columns)
    changes = pd.DataFrame(0.0, index=timeline, columns=tickers)
    trade_cash = pd.DataFrame(0.0, index=timeline, columns=tickers)
    native_changes = pd.DataFrame(0.0, index=timeline, columns=fx.columns)
    external = pd.Series(0.0, index=timeline)
    for _, row in trades.iterrows():
        date, ticker, ccy = row["Date"], row["Ticker"], row["Currency"]
        quantity, execution = float(row["Signed Quantity"]), float(row["Price"])
        fees = abs(float(row.get("Fees") or 0))
        cost = -quantity * execution - fees
        changes.at[date, ticker] += quantity
        native_changes.at[date, ccy] += cost
        trade_cash.at[date, ticker] += cost * fx.at[date, ccy]
    for _, row in flows.iterrows():
        date, ccy = row["Date"], row["Currency"]
        amount = abs(float(row["Amount"])) * (-1 if row["Type"] == "WITHDRAWAL" else 1)
        native_changes.at[date, ccy] += amount
        if row["Type"] in {"DEPOSIT", "WITHDRAWAL"}:
            external.at[date] += amount * fx.at[date, ccy]
    splits = price_history.attrs.get("splits", pd.DataFrame())
    if price_history.attrs.get("price_basis") == "split_adjusted_close" and not splits.empty:
        raise ValueError("Transaction replay needs contemporaneous marks with split events, not split-adjusted price levels.")
    quantities = pd.DataFrame(0.0, index=timeline, columns=tickers)
    current = pd.Series(0.0, index=tickers)
    prior_quantities = quantities.copy()
    for date in timeline:
        if date in splits.index:
            ratios = splits.loc[date].reindex(tickers).fillna(0)
            current *= ratios.where(ratios.ne(0), 1)
        prior_quantities.loc[date] = current
        current += changes.loc[date]
        quantities.loc[date] = current
    marks = pd.DataFrame({t: prices[t] * fx[ccy_map[t]] for t in tickers})
    values = quantities * marks
    values = values.mask(quantities.eq(0), 0)
    if ((quantities.ne(0)) & marks.isna()).any().any():
        raise ValueError("Missing marks for an active ledger position.")
    opening = float(state.get("cash", {}).get("starting", 0) or 0)
    native = native_changes.cumsum()
    native[base_currency] += opening
    cash = (native * fx).sum(axis=1)
    # Position P&L includes intraday execution-vs-close and fees, and is split neutral.
    previous_values = values.shift(1).fillna(0)
    pnl = values - previous_values + trade_cash
    return _finish_curve(cash, values, external, opening, position_pnl=pnl)


def _dated_holdings_snapshot_curve(state: dict[str, Any], price_history: pd.DataFrame, fx_history: pd.DataFrame | None = None, base_currency: str = "USD") -> pd.DataFrame:
    trades, _, prices, fx, ccy_map = _history_inputs(state, price_history, fx_history, base_currency)
    if state.get("accounting_history", {}).get("method") != "reconstructed dated holdings":
        return pd.DataFrame()
    values = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    entries = values.copy()
    for _, row in trades.iterrows():
        ticker, date = row["Ticker"], row["Date"]
        active = prices.index >= date
        lot = prices[ticker] * float(row["Signed Quantity"]) * fx[ccy_map[ticker]]
        if lot.loc[active].isna().any():
            raise ValueError(f"Missing active dated-holdings marks for {ticker}.")
        values.loc[active, ticker] += lot.loc[active]
        entries.at[prices.index[active][0], ticker] += float(lot.loc[active].iloc[0])
    balances = state.get("cash", {}).get("balances")
    if balances is None:
        balances = {base_currency: float(state.get("cash", {}).get("starting", 0) or 0) + float(state.get("cash", {}).get("explicit_snapshot_cash", 0) or 0)}
    cash = pd.Series(0.0, index=prices.index)
    for ccy, amount in balances.items():
        if ccy not in fx:
            raise ValueError(f"Missing historical FX for snapshot cash {ccy}.")
        cash += float(amount) * fx[ccy]
    opening = float(cash.iloc[0])
    pnl = values.diff().fillna(values.iloc[0]) - entries
    return _finish_curve(cash, values, entries.sum(axis=1), opening, position_pnl=pnl)


#______________________________________________________________________________
# PUBLIC ANALYTICS ENGINE
#______________________________________________________________________________

def run_analytics(
    state: dict[str, Any],
    price_history: pd.DataFrame,
    *,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
    fx_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Calculate current-book risk plus path-aware performance analytics."""
    returns = _returns_from_prices(_base_prices(state, price_history, fx_history))
    coverage = _coverage(state, returns)
    tickers = [str(row.get("ticker") or "").upper() for row in state.get("positions", [])]
    weights = _current_signed_weights(state, list(returns.columns))
    static_returns = _static_portfolio_returns(returns, weights) if coverage["available"] else pd.Series(dtype=float)
    risk_metrics = _series_metrics(static_returns, risk_free_rate=risk_free_rate, var_level=var_level)

    equity = float(state.get("totals", {}).get("equity") or 0.0)
    risk_metrics["var_value"] = (
        abs(equity) * float(risk_metrics["var_pct"])
        if risk_metrics.get("var_pct") is not None
        else None
    )
    risk_metrics["expected_shortfall_value"] = (
        abs(equity) * float(risk_metrics["expected_shortfall_pct"])
        if risk_metrics.get("expected_shortfall_pct") is not None
        else None
    )

    # Risk history uses total-return prices; account reconstruction is a separate call.
    performance_returns = static_returns
    performance_metrics = risk_metrics.copy()
    performance_method = "constant current-weight historical simulation (implied daily rebalancing)"
    if static_returns.empty or not risk_metrics.get("compounding_available", False):
        performance_series = pd.DataFrame()
    else:
        wealth = (1.0 + static_returns).cumprod()
        performance_series = pd.DataFrame({
            "daily_return": static_returns,
            "cumulative_return": wealth - 1.0,
            "drawdown": _drawdown(wealth),
        })
    actual_performance = {
        "available": False,
        "method": state.get("accounting_history", {}).get("method"),
        "metrics": {}, "portfolio_path": [],
        "reason": "Account performance requires a separate accounting-price history.",
    }

    risk_contribution = _risk_contribution(returns, weights) if coverage["available"] else []

    gross_exposure = float(state.get("totals", {}).get("gross_exposure") or 0.0)
    holdings_mix = []
    for row in state.get("positions", []):
        exposure = row.get("exposure")
        holdings_mix.append({
            "ticker": row.get("ticker"),
            "signed_market_value": row.get("signed_market_value"),
            "exposure": exposure,
            "exposure_share": float(exposure) / gross_exposure if exposure is not None and gross_exposure > 0 else 0.0,
            "equity_weight": (
                float(row["signed_market_value"]) / equity
                if row.get("signed_market_value") is not None and abs(equity) > 1e-12
                else None
            ),
        })

    # Historical combination-risk analytics. Pairs are the canonical default because
    # they remain exhaustive even for large portfolios. The UI can deterministically
    # recalculate larger user-selected groups without changing Portfolio State.
    from portfolio_analytics.analytics.combination_risk import calculate_combination_risk
    combination_risk = calculate_combination_risk(
        returns,
        tickers,
        combination_size=2,
        var_level=var_level,
    )

    asset_metrics: list[dict[str, Any]] = []
    for ticker in tickers:
        if ticker in returns.columns:
            metrics = _series_metrics(returns[ticker], risk_free_rate=risk_free_rate, var_level=var_level)
            asset_metrics.append({"ticker": ticker, **metrics})

    result = {
        "meta": {
            "var_level": float(var_level),
            "risk_free_rate": float(risk_free_rate),
            "base_currency": state.get("meta", {}).get("base_currency", "USD"),
            "coverage": coverage,
            "calendar": "common business-day observations; 252 periods/year",
            "return_method": "arithmetic annualised mean; geometric return reported separately",
            "risk_method": "static signed current-book base-currency total returns; base cash has zero return",
            "simulation": "constant current weights (implied daily rebalancing), not actual buy-and-hold performance",
            "performance_method": performance_method,
            "history_start": price_history.index.min().isoformat() if price_history is not None and not price_history.empty else None,
            "history_end": price_history.index.max().isoformat() if price_history is not None and not price_history.empty else None,
        },
        "exposure": {
            "long": state.get("totals", {}).get("long_exposure"),
            "short": state.get("totals", {}).get("short_exposure"),
            "gross": state.get("totals", {}).get("gross_exposure"),
            "net": state.get("totals", {}).get("net_exposure"),
            "gross_leverage": state.get("totals", {}).get("gross_leverage"),
            "net_leverage": state.get("totals", {}).get("net_leverage"),
        },
        "pnl": {
            "realised": state.get("totals", {}).get("realised_pnl"),
            "unrealised": state.get("totals", {}).get("unrealised_pnl"),
        },
        "risk": risk_metrics,
        "performance": performance_metrics,
        "actual_performance": actual_performance,
        "holdings_mix": holdings_mix,
        "risk_contribution": risk_contribution,
        "correlation": _correlation_matrix(returns, tickers),
        "asset_metrics": asset_metrics,
        "combination_risk": combination_risk,
        "series": {
            "portfolio_returns": [
                {"date": idx.isoformat(), "return": float(value)}
                for idx, value in performance_returns.dropna().items()
            ],
            "portfolio_path": [
                {
                    "date": idx.isoformat(),
                    **{
                        str(column): (float(value) if pd.notna(value) else None)
                        for column, value in row.items()
                    },
                }
                for idx, row in performance_series.iterrows()
            ],
        },
        "engine_data": {
            # Kept as JSON-serialisable records so scenario functions can be called
            # without hidden global state. Copilot context prunes this heavy block.
            "asset_returns": {
                ticker: [
                    {"date": idx.isoformat(), "return": float(value)}
                    for idx, value in returns[ticker].dropna().items()
                ]
                for ticker in returns.columns
            },
            "current_signed_weights": {str(k): float(v) for k, v in weights.items()},
        },
    }

    from portfolio_analytics.analytics.findings import generate_findings
    result["deep_findings"] = generate_findings(state, result)
    return result


#______________________________________________________________________________
# ACCOUNT-PATH PERFORMANCE FROM DATED ACCOUNTING EVENTS
#______________________________________________________________________________

def run_account_performance(
    state: dict[str, Any],
    price_history: pd.DataFrame,
    *,
    fx_history: pd.DataFrame | None = None,
    base_currency: str = "USD",
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, Any]:
    """Reconstruct performance from Portfolio State accounting events.

    Ledgers use contemporaneous marks, supplied executions/dividends and split
    events. Dated snapshots use split-adjusted closes and current share quantities;
    entry marks are neutralised and historical currency cash balances approximated.
    """
    history = state.get("accounting_history", {})
    if not history.get("available"):
        return {"available": False, "method": history.get("method"), "metrics": {}, "portfolio_path": [], "reason": "No dated accounting history is available."}

    try:
        if history.get("method") == "reconstructed dated holdings":
            curve = _dated_holdings_snapshot_curve(state, price_history, fx_history=fx_history, base_currency=base_currency)
        else:
            curve = _ledger_equity_curve(state, price_history, fx_history=fx_history, base_currency=base_currency)
    except ValueError as exc:
        return {"available": False, "method": history.get("method"), "metrics": {}, "portfolio_path": [], "reason": str(exc)}
    if curve.empty or curve["daily_return"].dropna().shape[0] < 2:
        return {
            "available": False,
            "method": history.get("method"),
            "metrics": {},
            "portfolio_path": [],
            "reason": "Sufficient historical market prices were not available to reconstruct the dated portfolio path.",
        }

    metrics = _series_metrics(curve["daily_return"], risk_free_rate=risk_free_rate, var_level=var_level)

    # Keep accounting cash flows separate from investment performance. Purchases
    # in a dated holdings snapshot are funded by inferred external contributions;
    # those contributions increase account equity but are removed from daily return.
    external_contributions = float(curve["external_flow"].clip(lower=0).sum())
    external_withdrawals = float(-curve["external_flow"].clip(upper=0).sum())
    ending_equity_series = pd.to_numeric(curve["equity"], errors="coerce").dropna()
    ending_equity = float(ending_equity_series.iloc[-1]) if not ending_equity_series.empty else None
    net_external_flow = external_contributions - external_withdrawals
    accounting_summary = {
        "history_start": curve.index.min().isoformat() if not curve.empty else None,
        "history_end": curve.index.max().isoformat() if not curve.empty else None,
        "external_contributions": external_contributions,
        "external_withdrawals": external_withdrawals,
        "net_external_flow": net_external_flow,
        "ending_equity": ending_equity,
        "opening_capital": float(curve.attrs.get("opening_capital", 0.0)),
        "gain_after_external_flows": (ending_equity - net_external_flow - float(curve.attrs.get("opening_capital", 0))) if ending_equity is not None else None,
        "cash_flow_timing": curve.attrs.get("cash_flow_timing"),
    }

    return {
        "available": True,
        "method": history.get("method"),
        "base_currency": base_currency.upper(),
        "metrics": metrics,
        "accounting_summary": accounting_summary,
        "portfolio_path": [
            {
                "date": idx.isoformat(),
                **{str(column): (float(value) if pd.notna(value) else None) for column, value in row.items()},
            }
            for idx, row in curve.iterrows()
        ],
        "reason": None,
    }



#______________________________________________________________________________
# HISTORICAL ATTRIBUTION
#______________________________________________________________________________

def historical_attribution(actual_performance: dict[str, Any], *, start_date: str | None = None,
                           end_date: str | None = None, analysis_mode: str = "drawdown") -> dict[str, Any]:
    """Link position P&L / prior account equity to the exact selected period return."""
    if not actual_performance.get("available"):
        return {"available": False, "reason": "Account history is unavailable."}
    frame = pd.DataFrame(actual_performance.get("portfolio_path", []))
    if frame.empty or "date" not in frame:
        return {"available": False, "reason": "No account path is available."}
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    start = pd.to_datetime(start_date, errors="coerce") if start_date else frame.index.min()
    end = pd.to_datetime(end_date, errors="coerce") if end_date else frame.index.max()
    if pd.isna(start) or pd.isna(end):
        return {"available": False, "reason": "Invalid attribution dates."}
    start, end = min(start, end), max(start, end)
    window = frame.loc[start:end].copy()
    if len(window) < 2:
        return {"available": False, "reason": "At least two valuations are required."}
    daily = pd.to_numeric(window["daily_return"], errors="coerce")
    # Return from the first selected valuation to the last; no pre-window return.
    daily.iloc[0] = 0.0
    if daily.isna().any() or (daily <= -1).any():
        return {"available": False, "reason": "Incomplete or invalid account returns in attribution window."}
    wealth = (1 + daily).cumprod()
    dd = _drawdown(wealth)
    mode = str(analysis_mode or "drawdown").lower()
    if mode in {"drawdown", "drawdown_attribution"}:
        trough = dd.idxmin()
        peak = wealth.loc[:trough].idxmax()
        attr_start, attr_end = peak, trough
    else:
        attr_start, attr_end = window.index.min(), window.index.max()
    segment = frame.loc[attr_start:attr_end].copy()
    cols = [c for c in segment if str(c).startswith("position__")]
    if not cols or len(segment) < 2:
        return {"available": False, "reason": "No non-zero attribution interval with position values is available."}
    r = pd.to_numeric(segment["daily_return"].iloc[1:], errors="coerce")
    previous_equity = pd.to_numeric(segment["equity"], errors="coerce").shift(1).iloc[1:]
    if r.isna().any() or previous_equity.le(0).any():
        return {"available": False, "reason": "Positive prior account equity and complete returns are required."}
    account_wealth = (1 + r).cumprod()
    link = account_wealth.shift(1).fillna(1.0)
    rows, daily_contributions = [], pd.DataFrame(index=r.index)
    for column in cols:
        ticker = column.removeprefix("position__")
        pnl_column = f"pnl__{ticker}"
        marks = pd.to_numeric(segment[column], errors="coerce")
        if pnl_column in segment:
            pnl = pd.to_numeric(segment[pnl_column], errors="coerce").iloc[1:]
        else:
            # Legacy paths contain no trade information; exclude first acquisition mark.
            pnl = marks.diff().where(marks.shift(1).ne(0), 0).iloc[1:]
        if marks.isna().any() or pnl.isna().any():
            return {"available": False, "reason": f"Incomplete attribution marks for {ticker}."}
        contribution = pnl / previous_equity
        daily_contributions[ticker] = contribution
        start_value, end_value = float(marks.iloc[0]), float(marks.iloc[-1])
        rows.append({"ticker": ticker, "contribution_pct_points": float((contribution * link).sum() * 100),
                     "start_value_usd": start_value, "end_value_usd": end_value,
                     "period_return_pct": (end_value / start_value - 1) * 100 if start_value > 0 else None})
    rows.sort(key=lambda row: row["contribution_pct_points"])
    residual = r - daily_contributions.sum(axis=1)
    residual_pp = float((residual * link).sum() * 100)
    attr_return = float(account_wealth.iloc[-1] - 1)
    total_pp = sum(row["contribution_pct_points"] for row in rows) + residual_pp
    return {
        "available": True, "analysis_mode": mode,
        "requested_period": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "attribution_period": {"start": attr_start.date().isoformat(), "end": attr_end.date().isoformat()},
        "period_return_pct": float((wealth.iloc[-1] - 1) * 100),
        "attribution_return_pct": attr_return * 100,
        "max_drawdown_pct": float(dd.min() * 100),
        "contributors": rows,
        "largest_negative_contributors": [row for row in rows if row["contribution_pct_points"] < 0][:10],
        "largest_positive_contributors": [row for row in reversed(rows) if row["contribution_pct_points"] > 0][:10],
        "cash_and_other_contribution_pct_points": residual_pp,
        "reconciliation_error_pct_points": total_pp - attr_return * 100,
        "method": "position P&L / prior account equity; daily contributions linked by prior cumulative wealth",
        "limitations": ["Snapshot history covers current holdings only; historical cash balances are approximated.",
                        "Cash FX, dividends not assigned to a ticker and other account effects appear in the explicit residual.",
                        "Attribution describes portfolio arithmetic and does not infer news or fundamental causes."],
    }


#______________________________________________________________________________
# REBUILD RETURNS FROM ANALYTICS DICTIONARY
#______________________________________________________________________________

def returns_from_analytics(analytics: dict[str, Any]) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for ticker, records in analytics.get("engine_data", {}).get("asset_returns", {}).items():
        series = pd.Series(
            {pd.Timestamp(row["date"]): float(row["return"]) for row in records},
            name=ticker,
            dtype=float,
        )
        columns[str(ticker)] = series
    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(columns).sort_index()

#______________________________________________________________________________
# CURRENT-BOOK ANALYTICS FROM AN EXISTING RETURNS MATRIX
#______________________________________________________________________________

def run_current_book_risk(
    state: dict[str, Any],
    returns: pd.DataFrame,
    *,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, Any]:
    """Recalculate current-book risk without rebuilding historical ledger performance.

    This is the scenario engine's analytical entry point. It deliberately uses
    the same metric helpers and signed-weight methodology as ``run_analytics``.
    """
    clean_returns = returns.copy()
    clean_returns.columns = [str(c).strip().upper() for c in clean_returns.columns]
    tickers = [str(row.get("ticker") or "").upper() for row in state.get("positions", [])]
    weights = _current_signed_weights(state, list(clean_returns.columns))
    coverage = _coverage(state, clean_returns)
    portfolio_returns = _static_portfolio_returns(clean_returns, weights) if coverage["available"] else pd.Series(dtype=float)
    risk = _series_metrics(portfolio_returns, risk_free_rate=risk_free_rate, var_level=var_level)
    equity = float(state.get("totals", {}).get("equity") or 0.0)
    risk["var_value"] = abs(equity) * float(risk["var_pct"]) if risk.get("var_pct") is not None else None
    risk["expected_shortfall_value"] = (
        abs(equity) * float(risk["expected_shortfall_pct"])
        if risk.get("expected_shortfall_pct") is not None
        else None
    )

    gross = float(state.get("totals", {}).get("gross_exposure") or 0.0)
    holdings_mix = []
    for row in state.get("positions", []):
        exposure = row.get("exposure")
        holdings_mix.append({
            "ticker": row.get("ticker"),
            "exposure": exposure,
            "exposure_share": float(exposure) / gross if exposure is not None and gross > 0 else 0.0,
            "equity_weight": (
                float(row["signed_market_value"]) / equity
                if row.get("signed_market_value") is not None and abs(equity) > 1e-12
                else None
            ),
        })

    return {
        "coverage": coverage,
        "meta": {"coverage": coverage, "base_currency": state.get("meta", {}).get("base_currency", "USD"),
                 "risk_free_rate": risk_free_rate, "var_level": var_level},
        "engine_data": {"asset_returns": {
            ticker: [{"date": date.isoformat(), "return": float(value)} for date, value in clean_returns[ticker].dropna().items()]
            for ticker in clean_returns.columns
        }},
        "risk": risk,
        "risk_contribution": _risk_contribution(clean_returns, weights) if coverage["available"] else [],
        "holdings_mix": holdings_mix,
        "correlation": _correlation_matrix(clean_returns, tickers),
        "weights": {str(k): float(v) for k, v in weights.items()},
    }
