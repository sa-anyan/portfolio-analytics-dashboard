"""Deterministic analytics engine for the canonical Portfolio State."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


#______________________________________________________________________________
# CONSTANTS
#______________________________________________________________________________

TRADING_DAYS = 252


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
    sharpe = (
        (annual_return - float(risk_free_rate)) / annual_volatility
        if np.isfinite(annual_volatility) and annual_volatility > 0
        else np.nan
    )

    cutoff = float(r.quantile(1.0 - var_level))
    var_pct = max(0.0, -cutoff)
    tail = r[r <= cutoff]
    es_pct = max(0.0, -float(tail.mean())) if not tail.empty else np.nan

    wealth = (1.0 + r).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0

    return {
        "observations": int(len(r)),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility) if np.isfinite(annual_volatility) else None,
        "sharpe": float(sharpe) if np.isfinite(sharpe) else None,
        "var_pct": float(var_pct),
        "expected_shortfall_pct": float(es_pct) if np.isfinite(es_pct) else None,
        "max_drawdown": float(drawdown.min()),
    }


def _current_signed_weights(state: dict[str, Any], return_columns: list[str]) -> pd.Series:
    equity = float(state.get("totals", {}).get("equity") or 0.0)
    weights = pd.Series(0.0, index=return_columns, dtype=float)
    if abs(equity) < 1e-12:
        return weights

    for row in state.get("positions", []):
        ticker = str(row.get("ticker") or "").upper()
        value = row.get("signed_market_value")
        if ticker in weights.index and value is not None:
            weights.loc[ticker] += float(value) / equity
    return weights


def _static_portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    active = weights[weights.abs() > 1e-15]
    if active.empty:
        return pd.Series(dtype=float, name="Portfolio Return")
    available = [
        ticker for ticker in active.index
        if ticker in returns.columns and pd.to_numeric(returns[ticker], errors="coerce").notna().sum() >= 2
    ]
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
    if not available:
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

def _ledger_equity_curve(state: dict[str, Any], price_history: pd.DataFrame, fx_history: pd.DataFrame | None = None, base_currency: str = "USD") -> pd.DataFrame:
    history = state.get("accounting_history", {})
    if history.get("available"):
        trades = pd.DataFrame(history.get("trades", []))
        cashflows = pd.DataFrame(history.get("cashflows", []))
    else:
        normalised = state.get("inputs", {}).get("normalised_dataset", {})
        trades = pd.DataFrame(normalised.get("ledger", []))
        cashflows = pd.DataFrame(normalised.get("cashflows", []))
    if trades.empty or price_history.empty:
        return pd.DataFrame()

    prices = price_history.copy()
    prices.columns = [str(c).strip().upper() for c in prices.columns]
    prices.index = pd.to_datetime(prices.index, errors="coerce").normalize()
    prices = prices.loc[~prices.index.isna()].sort_index()

    trades["Date"] = pd.to_datetime(trades["Date"], errors="coerce").dt.normalize()
    trades["Signed Quantity"] = pd.to_numeric(trades["Signed Quantity"], errors="coerce")
    trades["Price"] = pd.to_numeric(trades["Price"], errors="coerce")
    trades["Fees"] = pd.to_numeric(trades.get("Fees", 0.0), errors="coerce").fillna(0.0)
    trades["Currency"] = trades.get("Currency", base_currency)
    trades["Currency"] = trades["Currency"].fillna(base_currency).astype(str).str.upper().replace({"": base_currency})
    trades = trades.dropna(subset=["Date", "Ticker", "Signed Quantity", "Price"])

    if trades.empty:
        return pd.DataFrame()

    if not cashflows.empty:
        cashflows["Date"] = pd.to_datetime(cashflows["Date"], errors="coerce").dt.normalize()
        cashflows["Amount"] = pd.to_numeric(cashflows["Amount"], errors="coerce")
        cashflows["Currency"] = cashflows.get("Currency", base_currency)
        cashflows["Currency"] = cashflows["Currency"].fillna(base_currency).astype(str).str.upper().replace({"": base_currency})
        cashflows = cashflows.dropna(subset=["Date", "Amount"])

    tickers = sorted(set(trades["Ticker"].astype(str).str.upper()) & set(prices.columns))
    if not tickers:
        return pd.DataFrame()

    trades = trades[trades["Ticker"].astype(str).str.upper().isin(tickers)].copy()
    trades["Ticker"] = trades["Ticker"].astype(str).str.upper()

    first_date = trades["Date"].min()
    if not cashflows.empty:
        first_date = min(first_date, cashflows["Date"].min())

    event_dates = pd.DatetimeIndex(trades["Date"].unique())
    if not cashflows.empty:
        event_dates = event_dates.union(pd.DatetimeIndex(cashflows["Date"].unique()))
    timeline = prices.index.union(event_dates).sort_values()
    timeline = timeline[timeline >= first_date]
    if timeline.empty:
        return pd.DataFrame()

    px = prices.reindex(timeline)[tickers].ffill()

    # Currency is an accounting invariant: every mark, execution and cash flow is
    # converted into USD before values are added together. Missing currency is
    # explicitly treated as USD by the normaliser. GBX is pence, so its USD rate
    # is GBPUSD / 100.
    fx = pd.DataFrame(index=timeline)
    if fx_history is not None and not fx_history.empty:
        raw_fx = fx_history.copy()
        raw_fx.columns = [str(c).strip().upper() for c in raw_fx.columns]
        raw_fx.index = pd.to_datetime(raw_fx.index, errors="coerce").normalize()
        raw_fx = raw_fx.loc[~raw_fx.index.isna()].sort_index()
        fx = raw_fx.reindex(timeline).ffill()
    fx[base_currency.upper()] = 1.0
    if "GBP" in fx.columns and "GBX" not in fx.columns:
        fx["GBX"] = fx["GBP"] / 100.0

    ticker_currency = {}
    for ticker, group in trades.groupby("Ticker"):
        currencies = group["Currency"].dropna().astype(str).str.upper()
        ticker_currency[str(ticker).upper()] = currencies.iloc[-1] if not currencies.empty else base_currency.upper()

    px_base = pd.DataFrame(index=timeline, columns=tickers, dtype=float)
    for ticker in tickers:
        ccy = ticker_currency.get(ticker, base_currency.upper())
        rate = fx[ccy] if ccy in fx.columns else pd.Series(index=timeline, dtype=float)
        px_base[ticker] = px[ticker] * rate

    position_changes = pd.DataFrame(0.0, index=timeline, columns=tickers)
    cash_changes = pd.Series(0.0, index=timeline, dtype=float)
    external_flows = pd.Series(0.0, index=timeline, dtype=float)

    for _, row in trades.iterrows():
        date = row["Date"]
        if date not in timeline:
            continue
        ticker = str(row["Ticker"])
        signed_qty = float(row["Signed Quantity"])
        trade_price = float(row["Price"])
        fees = float(row["Fees"])
        ccy = str(row.get("Currency") or base_currency).upper()
        rate = float(fx.at[date, ccy]) if ccy in fx.columns and pd.notna(fx.at[date, ccy]) else np.nan
        if not np.isfinite(rate):
            continue
        trade_price *= rate
        fees *= rate
        position_changes.at[date, ticker] += signed_qty
        cash_changes.at[date] += -(signed_qty * trade_price) - fees

    for _, row in cashflows.iterrows():
        date = row["Date"]
        if date not in timeline:
            continue
        amount = abs(float(row["Amount"]))
        event_type = str(row["Type"]).upper()
        ccy = str(row.get("Currency") or base_currency).upper()
        rate = float(fx.at[date, ccy]) if ccy in fx.columns and pd.notna(fx.at[date, ccy]) else np.nan
        if not np.isfinite(rate):
            continue
        amount *= rate
        if event_type in {"DEPOSIT", "DIVIDEND"}:
            cash_changes.at[date] += amount
        elif event_type == "WITHDRAWAL":
            cash_changes.at[date] -= amount
        if event_type == "DEPOSIT":
            external_flows.at[date] += amount
        elif event_type == "WITHDRAWAL":
            external_flows.at[date] -= amount

    positions = position_changes.cumsum()
    starting_cash = float(state.get("cash", {}).get("starting", 0.0) or 0.0)
    cash = starting_cash + cash_changes.cumsum()
    missing_open_price = ((positions.abs() > 1e-15) & px_base.isna()).any(axis=1)
    market_value = (positions * px_base).sum(axis=1, min_count=1)
    equity = cash + market_value
    equity.loc[missing_open_price] = np.nan

    previous = equity.shift(1)
    daily_return = (equity - previous - external_flows) / previous
    daily_return.loc[previous.isna() | (previous.abs() < 1e-12)] = np.nan
    first_valid = equity.first_valid_index()
    if first_valid is not None:
        daily_return.loc[first_valid] = 0.0

    # Do not turn unresolved market-data gaps into artificial 0% return days.
    wealth = (1.0 + daily_return.dropna()).cumprod().reindex(timeline)
    drawdown = wealth / wealth.cummax() - 1.0

    return pd.DataFrame({
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "external_flow": external_flows,
        "daily_return": daily_return,
        "cumulative_return": wealth - 1.0,
        "drawdown": drawdown,
    })


#______________________________________________________________________________
# PUBLIC ANALYTICS ENGINE
#______________________________________________________________________________

def run_analytics(
    state: dict[str, Any],
    price_history: pd.DataFrame,
    *,
    risk_free_rate: float = 0.0,
    var_level: float = 0.95,
) -> dict[str, Any]:
    """Calculate current-book risk plus path-aware performance analytics."""
    returns = _returns_from_prices(price_history)
    tickers = [str(row.get("ticker") or "").upper() for row in state.get("positions", [])]
    weights = _current_signed_weights(state, list(returns.columns))
    static_returns = _static_portfolio_returns(returns, weights)
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

    ledger_curve = pd.DataFrame()
    if state.get("accounting_history", {}).get("available"):
        ledger_curve = _ledger_equity_curve(state, price_history)

    use_account_path_as_default = state.get("meta", {}).get("path") == "ledger"
    if use_account_path_as_default and not ledger_curve.empty and ledger_curve["daily_return"].dropna().shape[0] >= 2:
        performance_returns = ledger_curve["daily_return"]
        performance_metrics = _series_metrics(
            performance_returns,
            risk_free_rate=risk_free_rate,
            var_level=var_level,
        )
        performance_method = "actual ledger path with external cash-flow adjustment"
        performance_series = ledger_curve
    else:
        performance_returns = static_returns
        performance_metrics = _series_metrics(
            performance_returns,
            risk_free_rate=risk_free_rate,
            var_level=var_level,
        )
        performance_method = "static current-book historical simulation"
        if static_returns.empty:
            performance_series = pd.DataFrame()
        else:
            wealth = (1.0 + static_returns).cumprod()
            performance_series = pd.DataFrame({
                "daily_return": static_returns,
                "cumulative_return": wealth - 1.0,
                "drawdown": wealth / wealth.cummax() - 1.0,
            })

    actual_performance = {
        "available": False,
        "method": state.get("accounting_history", {}).get("method"),
        "metrics": {},
        "portfolio_path": [],
        "reason": None,
    }
    if not ledger_curve.empty and ledger_curve["daily_return"].dropna().shape[0] >= 2:
        actual_metrics = _series_metrics(
            ledger_curve["daily_return"],
            risk_free_rate=risk_free_rate,
            var_level=var_level,
        )
        actual_performance = {
            "available": True,
            "method": state.get("accounting_history", {}).get("method"),
            "metrics": actual_metrics,
            "portfolio_path": [
                {
                    "date": idx.isoformat(),
                    **{str(column): (float(value) if pd.notna(value) else None) for column, value in row.items()},
                }
                for idx, row in ledger_curve.iterrows()
            ],
            "reason": None,
        }
    elif state.get("accounting_history", {}).get("available"):
        actual_performance["reason"] = "Dated accounting information was detected, but sufficient historical market prices were not available to reconstruct the path."

    risk_contribution = _risk_contribution(returns, weights)

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

    return {
        "meta": {
            "var_level": float(var_level),
            "risk_free_rate": float(risk_free_rate),
            "risk_method": "static signed current-book historical returns; cash is a zero-return residual",
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


#______________________________________________________________________________
# DATED HOLDINGS SNAPSHOT RECONSTRUCTION
#______________________________________________________________________________

def _dated_holdings_snapshot_curve(
    state: dict[str, Any],
    price_history: pd.DataFrame,
    fx_history: pd.DataFrame | None = None,
    base_currency: str = "USD",
) -> pd.DataFrame:
    """Reconstruct the currently-held book from purchase dates.

    This is deliberately different from ledger replay. A holdings snapshot does
    not prove the historical cash ledger. Each current position is therefore
    absent before its supplied purchase date and is marked to historical market
    prices afterwards. On its entry day, the first market value is treated as an
    external capital addition for performance chaining, so the difference
    between an execution price and Yahoo's daily close cannot manufacture a
    return. Supplied purchase price remains cost-basis information.
    """
    history = state.get("accounting_history", {})
    if history.get("method") != "reconstructed dated holdings":
        return pd.DataFrame()
    trades = pd.DataFrame(history.get("trades", []))
    if trades.empty or price_history is None or price_history.empty:
        return pd.DataFrame()

    prices = price_history.copy()
    prices.columns = [str(c).strip().upper() for c in prices.columns]
    prices.index = pd.to_datetime(prices.index, errors="coerce").normalize()
    prices = prices.loc[~prices.index.isna()].sort_index().apply(pd.to_numeric, errors="coerce")

    trades["Date"] = pd.to_datetime(trades["Date"], errors="coerce").dt.normalize()
    trades["Signed Quantity"] = pd.to_numeric(trades["Signed Quantity"], errors="coerce")
    trades["Currency"] = trades.get("Currency", base_currency)
    trades["Currency"] = trades["Currency"].fillna(base_currency).astype(str).str.upper().replace({"": base_currency})
    trades["Ticker"] = trades["Ticker"].astype(str).str.upper()
    trades = trades.dropna(subset=["Date", "Ticker", "Signed Quantity"])
    tickers = sorted(set(trades["Ticker"]) & set(prices.columns))
    if not tickers:
        return pd.DataFrame()
    trades = trades[trades["Ticker"].isin(tickers)].copy()

    first_date = trades["Date"].min()
    timeline = prices.index[prices.index >= first_date]
    if timeline.empty:
        return pd.DataFrame()
    px = prices.reindex(timeline)[tickers].ffill()

    fx = pd.DataFrame(index=timeline)
    if fx_history is not None and not fx_history.empty:
        raw_fx = fx_history.copy()
        raw_fx.columns = [str(c).strip().upper() for c in raw_fx.columns]
        raw_fx.index = pd.to_datetime(raw_fx.index, errors="coerce").normalize()
        raw_fx = raw_fx.loc[~raw_fx.index.isna()].sort_index().apply(pd.to_numeric, errors="coerce")
        fx = raw_fx.reindex(timeline).ffill()
    fx[base_currency.upper()] = 1.0
    if "GBP" in fx.columns and "GBX" not in fx.columns:
        fx["GBX"] = fx["GBP"] / 100.0

    values = pd.DataFrame(0.0, index=timeline, columns=tickers)
    entry_flow = pd.Series(0.0, index=timeline, dtype=float)

    for _, row in trades.iterrows():
        ticker = row["Ticker"]
        purchase_date = row["Date"]
        qty = float(row["Signed Quantity"])
        ccy = str(row.get("Currency") or base_currency).upper()
        if ccy not in fx.columns:
            continue
        rate = fx[ccy]
        local_value = px[ticker] * qty
        usd_value = local_value * rate
        active = timeline >= purchase_date
        values.loc[active, ticker] = usd_value.loc[active]

        # Neutralise the acquisition in the return series using the first actual
        # market mark on/after the supplied purchase date, not the execution price.
        first_marks = usd_value.loc[active].dropna()
        if not first_marks.empty:
            entry_flow.at[first_marks.index[0]] += float(first_marks.iloc[0])

    # A holdings snapshot gives only today's cash, not its historical cash ledger.
    # Keep supplied cash constant as an explicitly approximate balance, matching
    # the snapshot reconstruction convention rather than inventing cash events.
    cash_value = float(state.get("cash", {}).get("explicit_snapshot_cash", 0.0) or 0.0)
    portfolio_value = values.sum(axis=1, min_count=1) + cash_value

    previous = portfolio_value.shift(1)
    daily_return = (portfolio_value - previous - entry_flow) / previous
    daily_return.loc[previous.isna() | (previous.abs() < 1e-12)] = np.nan
    first_valid = portfolio_value.first_valid_index()
    if first_valid is not None:
        daily_return.loc[first_valid] = 0.0

    wealth = (1.0 + daily_return.dropna()).cumprod().reindex(timeline)
    drawdown = wealth / wealth.cummax() - 1.0
    curve = pd.DataFrame({
        "cash": cash_value,
        "market_value": values.sum(axis=1, min_count=1),
        "equity": portfolio_value,
        "external_flow": entry_flow,
        "daily_return": daily_return,
        "cumulative_return": wealth - 1.0,
        "drawdown": drawdown,
    })
    # Keep per-position USD marks inside deterministic Python so historical
    # attribution can answer "what caused this drawdown?" without asking the LLM
    # to infer contribution from headline metrics.
    for ticker in values.columns:
        curve[f"position__{ticker}"] = values[ticker]
    return curve

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

    Transaction ledgers use their supplied executions. Dated holdings use the
    inferred acquisition events created by Portfolio State from purchase dates
    and entry prices. The same equity-curve and metric helpers are used for both.
    """
    history = state.get("accounting_history", {})
    if not history.get("available"):
        return {"available": False, "method": history.get("method"), "metrics": {}, "portfolio_path": [], "reason": "No dated accounting history is available."}

    if history.get("method") == "reconstructed dated holdings":
        curve = _dated_holdings_snapshot_curve(state, price_history, fx_history=fx_history, base_currency=base_currency)
    else:
        curve = _ledger_equity_curve(state, price_history, fx_history=fx_history, base_currency=base_currency)
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
        "gain_after_external_flows": (ending_equity - net_external_flow) if ending_equity is not None else None,
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

def historical_attribution(
    actual_performance: dict[str, Any],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    analysis_mode: str = "drawdown",
) -> dict[str, Any]:
    """Deterministically explain a dated holdings period or drawdown.

    Attribution uses reconstructed USD position marks. Daily position returns are
    multiplied by prior-day portfolio weights, so capital entering the portfolio
    is not labelled as investment performance. Results are descriptive historical
    attribution, not causal claims about news or company fundamentals.
    """
    if not actual_performance.get("available"):
        return {"available": False, "reason": "Actual/reconstructed portfolio history is unavailable."}

    records = actual_performance.get("portfolio_path", [])
    if not records:
        return {"available": False, "reason": "No reconstructed portfolio path is available."}

    frame = pd.DataFrame(records)
    if frame.empty or "date" not in frame:
        return {"available": False, "reason": "No dated reconstructed observations are available."}
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).set_index("date").sort_index()

    start = pd.to_datetime(start_date, errors="coerce") if start_date else frame.index.min()
    end = pd.to_datetime(end_date, errors="coerce") if end_date else frame.index.max()
    if pd.isna(start) or pd.isna(end):
        return {"available": False, "reason": "The requested historical dates could not be interpreted."}
    if start > end:
        start, end = end, start

    window = frame.loc[(frame.index >= start) & (frame.index <= end)].copy()
    if window.shape[0] < 2:
        return {"available": False, "reason": "There are not enough reconstructed observations in the requested period."}

    daily = pd.to_numeric(window.get("daily_return"), errors="coerce")
    wealth = (1.0 + daily.fillna(0.0)).cumprod()
    running_peak = wealth.cummax()
    dd = wealth / running_peak - 1.0

    mode = str(analysis_mode or "drawdown").lower()
    if mode in {"drawdown", "drawdown_attribution"}:
        trough = dd.idxmin()
        peak = wealth.loc[:trough].idxmax()
        attr_start, attr_end = peak, trough
    else:
        attr_start, attr_end = window.index.min(), window.index.max()

    pos_cols = [c for c in frame.columns if str(c).startswith("position__")]
    if not pos_cols:
        return {"available": False, "reason": "Per-position reconstructed values are unavailable for attribution."}

    segment = frame.loc[(frame.index >= attr_start) & (frame.index <= attr_end), pos_cols].apply(pd.to_numeric, errors="coerce")
    segment = segment.fillna(0.0)
    if segment.shape[0] < 2:
        return {"available": False, "reason": "The selected attribution interval is too short."}

    position_returns = segment.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    prior_values = segment.shift(1)
    prior_total = prior_values.sum(axis=1).replace(0.0, np.nan)
    weights = prior_values.div(prior_total, axis=0)
    contributions = (weights * position_returns).fillna(0.0)

    # A new position has zero prior value. Its first mark is therefore naturally
    # excluded from return attribution rather than being treated as profit/loss.
    summed = contributions.sum(axis=0)
    rows = []
    for column, contribution in summed.items():
        ticker = str(column).replace("position__", "", 1)
        start_value = float(segment[column].iloc[0])
        end_value = float(segment[column].iloc[-1])
        ticker_return = None
        if abs(start_value) > 1e-12:
            ticker_return = end_value / start_value - 1.0
        rows.append({
            "ticker": ticker,
            "contribution_pct_points": float(contribution * 100.0),
            "start_value_usd": start_value,
            "end_value_usd": end_value,
            "period_return_pct": (float(ticker_return * 100.0) if ticker_return is not None else None),
        })
    rows.sort(key=lambda x: x["contribution_pct_points"])

    period_return = float(wealth.iloc[-1] - 1.0)
    drawdown_value = float(dd.min())
    return {
        "available": True,
        "analysis_mode": mode,
        "requested_period": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "attribution_period": {"start": attr_start.date().isoformat(), "end": attr_end.date().isoformat()},
        "period_return_pct": period_return * 100.0,
        "max_drawdown_pct": drawdown_value * 100.0,
        "largest_negative_contributors": rows[:10],
        "largest_positive_contributors": list(reversed(rows[-10:])),
        "method": "prior-day reconstructed USD position weight × daily USD position return",
        "limitations": [
            "Attribution covers currently-held positions with supplied purchase dates.",
            "Positions sold before the uploaded holdings snapshot cannot be reconstructed.",
            "Contribution identifies which holdings drove portfolio return; it does not infer news or fundamental causes.",
        ],
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
    portfolio_returns = _static_portfolio_returns(clean_returns, weights)
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
        "risk": risk,
        "risk_contribution": _risk_contribution(clean_returns, weights),
        "holdings_mix": holdings_mix,
        "correlation": _correlation_matrix(clean_returns, tickers),
        "weights": {str(k): float(v) for k, v in weights.items()},
    }
