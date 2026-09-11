
#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import yfinance as yf

from portfolio_analytics.quality.auto_clean import (
    auto_clean_ledger,
    combine_cash_events,
    extract_cash_events,
)
from portfolio_analytics.quality.repair_engine import (
    diagnose_repairs,
    apply_repairs,
)
from portfolio_analytics.quality.pattern_learning import profile_summary_table
from portfolio_analytics.analytics.market_data import get_market_data, get_market_data_range
from portfolio_analytics.analytics.market_analysis import (
    standardise_market_data,
    asset_metrics,
    portfolio_metrics as market_portfolio_metrics,
    portfolio_returns as market_portfolio_returns,
    risk_contribution as market_risk_contribution,
    monte_carlo_portfolios,
)
from portfolio_analytics.ingestion.input_parser import (
    apply_ledger_ticker_map,
    clean_transaction_ledger,
    build_data_quality_report,
    flag_potential_duplicates,
    exclude_unresolved_securities,
    standardise_ledger_for_accounting,
    finalise_holdings,
    prepare_holdings_for_review,
    read_upload,
    resolve_security,
    resolve_security_candidates,
    validate_holdings,
    validate_ledger_tickers,
)
from portfolio_analytics.accounting.today_engine import (
    build_current_account,
    value_current_positions,
    build_transaction_price_audit,
    build_historical_account_equity,
)
from portfolio_analytics.analytics.stress_test import run_stress_test
from portfolio_analytics.accounting.valuation_engine import (
    LIVE_MARKET,
    HISTORICAL_AS_OF,
    FROZEN_SNAPSHOT,
    frozen_snapshot_as_of,
    get_valuation_prices,
)


# Keep every Plotly figure on a clean white canvas to match the site theme.
pio.templates.default = "plotly_white"




#______________________________________________________________________________
# SIGNED LONG/SHORT ANALYTICS HELPERS
#______________________________________________________________________________

def _signed_portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Static current-book returns using signed weights without normalising away shorts."""
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


def _series_metrics_local(daily_returns: pd.Series, risk_free_rate: float = 0.0, var_level: float = 0.95) -> dict[str, float]:
    """Mirror the project's standard return/risk metrics for a supplied daily-return series."""
    r = pd.to_numeric(daily_returns, errors="coerce").dropna()
    if r.empty:
        return {
            "Annual Return": np.nan, "Annual Volatility": np.nan, "Sharpe": np.nan,
            "VaR": np.nan, "Expected Shortfall": np.nan, "Max Drawdown": np.nan,
        }
    trading_days = 252
    mean_daily = float(r.mean())
    annual_return = mean_daily * trading_days
    annual_volatility = float(r.std(ddof=1)) * np.sqrt(trading_days) if len(r) > 1 else np.nan
    sharpe = (annual_return - risk_free_rate) / annual_volatility if np.isfinite(annual_volatility) and annual_volatility > 0 else np.nan
    alpha = 1.0 - float(var_level)
    q = float(r.quantile(alpha))
    es_slice = r[r <= q]
    es = float(es_slice.mean()) if not es_slice.empty else q
    growth = (1.0 + r).cumprod()
    drawdown = growth / growth.cummax() - 1.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else np.nan
    return {
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "var": q,
        "expected_shortfall": es,
        "max_drawdown": max_drawdown,
    }


def _signed_portfolio_metrics(returns: pd.DataFrame, weights: pd.Series, risk_free_rate: float = 0.0, var_level: float = 0.95) -> dict[str, float]:
    return _series_metrics_local(_signed_portfolio_returns(returns, weights), risk_free_rate, var_level)


def _signed_risk_contribution(returns: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    """Component volatility contribution using signed long/short weights."""
    empty = pd.DataFrame(columns=["Ticker", "Weight", "Risk Contribution", "Risk Contribution %"])
    if returns.empty:
        return empty
    cols = [str(c) for c in returns.columns]
    w = pd.Series(weights, dtype=float).reindex(cols).fillna(0.0)
    active = w[w != 0.0]
    if active.empty:
        return empty
    selected = returns.loc[:, active.index].dropna(how="any")
    if len(selected) < 2:
        return empty
    cov = selected.cov().to_numpy(dtype=float) * 252
    wv = active.loc[selected.columns].to_numpy(dtype=float)
    variance = float(wv @ cov @ wv)
    if not np.isfinite(variance) or variance <= 0:
        return empty
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


#______________________________________________________________________________
# CACHED MARKET / SIMULATION HELPERS
#______________________________________________________________________________

@st.cache_data(ttl=3600, show_spinner=False)
def cached_market_data(tickers: tuple[str, ...]):
    """Cache Yahoo history for one hour so Streamlit reruns do not redownload it."""
    return get_market_data(list(tickers))

@st.cache_data(ttl=300, show_spinner=False)
def cached_today_snapshot(tickers: tuple[str, ...]):
    """Return latest and previous trading-day closes directly from yfinance.

    This snapshot is deliberately independent of the portfolio valuation date.
    It always represents the latest two daily closes available from Yahoo Finance.
    A batched download is attempted first for reliability and speed, with a
    per-ticker history fallback if Yahoo returns an incomplete batch.
    """
    ordered = list(dict.fromkeys(
        str(t).strip().upper() for t in tickers if str(t).strip()
    ))
    out = pd.DataFrame(
        index=ordered,
        columns=["Previous Close", "Current Close", "Day Change %"],
        dtype=float,
    )
    if not ordered:
        return out

    aliases = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
    yahoo_symbols = [aliases.get(t, t) for t in ordered]
    reverse = {aliases.get(t, t): t for t in ordered}

    def _store(original_ticker: str, closes: pd.Series) -> None:
        clean = pd.to_numeric(closes, errors="coerce").dropna()
        if len(clean) < 2:
            return
        previous = float(clean.iloc[-2])
        current = float(clean.iloc[-1])
        out.loc[original_ticker, "Previous Close"] = previous
        out.loc[original_ticker, "Current Close"] = current
        if previous != 0:
            out.loc[original_ticker, "Day Change %"] = (current / previous - 1.0) * 100.0

    # First try one batched Yahoo request for every ticker in the holdings list.
    try:
        batch = yf.download(
            tickers=yahoo_symbols,
            period="5d",
            interval="1d",
            auto_adjust=False,
            actions=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
        if batch is not None and not batch.empty:
            if len(yahoo_symbols) == 1:
                symbol = yahoo_symbols[0]
                if "Close" in batch.columns:
                    _store(reverse[symbol], batch["Close"])
            elif isinstance(batch.columns, pd.MultiIndex):
                level0 = set(map(str, batch.columns.get_level_values(0)))
                level1 = set(map(str, batch.columns.get_level_values(1)))
                for symbol in yahoo_symbols:
                    original = reverse[symbol]
                    try:
                        if symbol in level0 and "Close" in level1:
                            _store(original, batch[(symbol, "Close")])
                        elif "Close" in level0 and symbol in level1:
                            _store(original, batch[("Close", symbol)])
                    except Exception:
                        pass
    except Exception:
        pass

    # Fill any gaps individually. This avoids showing a dash merely because one
    # ticker caused Yahoo's multi-ticker response to be incomplete.
    for original_ticker in ordered:
        if pd.notna(out.loc[original_ticker, "Day Change %"]):
            continue
        yahoo_ticker = aliases.get(original_ticker, original_ticker)
        try:
            hist = yf.Ticker(yahoo_ticker).history(
                period="5d", interval="1d", auto_adjust=False, actions=False
            )
        except Exception:
            continue
        if hist is not None and not hist.empty and "Close" in hist.columns:
            _store(original_ticker, hist["Close"])

    return out


@st.cache_data(ttl=3600, show_spinner=False)
def cached_market_data_range(
    tickers: tuple[str, ...],
    start: str,
    end: str | None = None,
):
    """Cache ledger-specific Yahoo history for one hour."""
    return get_market_data_range(
        list(tickers),
        start=start,
        end=end,
    )


@st.cache_data(show_spinner=False)
def cached_monte_carlo(
    returns: pd.DataFrame,
    risk_free_rate: float,
    simulations: int = 10_000,
    seed: int = 42,
):
    """Cache deterministic Monte Carlo results for unchanged returns/settings."""
    return monte_carlo_portfolios(
        returns,
        risk_free_rate=risk_free_rate,
        simulations=simulations,
        seed=seed,
    )





#______________________________________________________________________________
# THEMED DASHBOARD TABLES
#______________________________________________________________________________

def _sentiment_cell_style(value):
    """Green for gains/positive values, red for losses/negative values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""

    if pd.isna(number) or abs(number) < 1e-12:
        return ""
    if number > 0:
        return (
            "color: #087A55 !important; "
            "background-color: #ECF8F2 !important; "
            "font-weight: 700 !important;"
        )
    return (
        "color: #C23B35 !important; "
        "background-color: #FDF0EF !important; "
        "font-weight: 700 !important;"
    )


def _theme_dataframe(data, sentiment_columns=None):
    """Apply the soft navy / gold / ivory website theme to display-only tables."""
    try:
        from pandas.io.formats.style import Styler
    except Exception:
        Styler = ()

    if Styler and isinstance(data, Styler):
        styler = data
    elif isinstance(data, pd.DataFrame):
        styler = data.style
    else:
        return data

    # The reference theme is intentionally light: navy is reserved for text and
    # strong actions, while tables use warm-white surfaces and a soft grey header.
    styler = styler.set_table_styles([
        {
            "selector": "thead th",
            "props": [
                ("background-color", "#F3F2EE"),
                ("color", "#102B46"),
                ("font-weight", "700"),
                ("border-bottom", "1px solid #DED9CF"),
                ("text-align", "left"),
            ],
        },
        {
            "selector": "tbody td",
            "props": [
                ("background-color", "#FFFFFF"),
                ("color", "#172A40"),
                ("border-bottom", "1px solid #ECE8E0"),
            ],
        },
    ], overwrite=False)

    styler = styler.set_properties(**{
        "font-size": "0.94rem",
        "padding": "0.58rem 0.72rem",
    })

    if sentiment_columns:
        existing = [
            column for column in sentiment_columns
            if column in getattr(styler, "data", pd.DataFrame()).columns
        ]
        if existing:
            try:
                styler = styler.map(_sentiment_cell_style, subset=existing)
            except AttributeError:
                styler = styler.applymap(_sentiment_cell_style, subset=existing)

    return styler


def themed_dataframe(data, *args, sentiment_columns=None, **kwargs):
    """Render a softly themed read-only dashboard table.

    Editable grids remain native Streamlit widgets. Positive/negative colouring is
    opt-in so only decision-useful financial tables receive red/green emphasis.
    """
    styler = _theme_dataframe(data, sentiment_columns=sentiment_columns)

    if hasattr(styler, "to_html"):
        table_html = styler.to_html()
    else:
        frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        table_html = _theme_dataframe(
            frame, sentiment_columns=sentiment_columns
        ).to_html()

    requested_height = kwargs.get("height")
    max_height_css = (
        f"max-height:{int(requested_height)}px;"
        if isinstance(requested_height, (int, float)) and requested_height > 0
        else ""
    )

    html = (
        f'<div class="pa-table-shell" style="{max_height_css}">'
        f'{table_html}'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


#______________________________________________________________________________
# RISK / PERFORMANCE VISUAL HELPERS
#______________________________________________________________________________


def _plotly_config() -> dict[str, Any]:
    """Shared interactive chart controls with a Tableau-like feel."""
    return {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": True,
        "modeBarButtonsToRemove": ["lasso2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2},
    }


def _apply_time_range_buttons(fig: go.Figure) -> None:
    """Add compact range selectors for time-series exploration."""
    fig.update_xaxes(
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(count=3, label="3Y", step="year", stepmode="backward"),
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ],
            x=0,
            y=1.10,
        ),
        rangeslider=dict(visible=False),
    )


def plot_scaled_series(
    series: pd.Series,
    *,
    title: str,
    y_label: str,
    anchor_zero: bool = False,
) -> None:
    """Interactive dated series with axes locked to the actual observations."""
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return
    clean.index = pd.to_datetime(clean.index, errors="coerce")
    clean = clean.loc[clean.index.notna()].sort_index()
    if clean.empty:
        return

    y_min = float(clean.min())
    y_max = float(clean.max())
    if anchor_zero and y_min >= 0:
        lower = 0.0
        upper = y_max * 1.03 if y_max > 0 else 1.0
    else:
        span = y_max - y_min
        pad = span * 0.03 if span > 0 else max(abs(y_max) * 0.03, 1.0)
        lower = y_min - pad
        upper = y_max + pad

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=clean.index,
            y=clean.to_numpy(),
            mode="lines",
            name=title,
            hovertemplate="Date %{x|%d %b %Y}<br>Value %{y:,.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        height=430,
        margin=dict(l=35, r=20, t=70, b=35),
        hovermode="x unified",
        dragmode="zoom",
        xaxis_title="Date",
        yaxis_title=y_label,
    )
    fig.update_xaxes(range=[clean.index.min(), clean.index.max()])
    fig.update_yaxes(range=[lower, upper], fixedrange=False)
    _apply_time_range_buttons(fig)
    st.plotly_chart(fig, use_container_width=True, config=_plotly_config())


def plot_growth_and_drawdown(portfolio_returns: pd.Series) -> None:
    """Render interactive historical performance and drawdown charts."""
    clean = pd.to_numeric(portfolio_returns, errors="coerce").dropna()
    if clean.empty:
        return
    clean.index = pd.to_datetime(clean.index, errors="coerce")
    clean = clean.loc[clean.index.notna()].sort_index()
    if clean.empty:
        return

    wealth = (1.0 + clean).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0

    growth = go.Figure()
    growth.add_trace(
        go.Scatter(
            x=wealth.index,
            y=wealth.to_numpy(),
            mode="lines",
            name="Growth of £1",
            hovertemplate="%{x|%d %b %Y}<br>£%{y:.3f}<extra></extra>",
        )
    )
    growth.update_layout(
        title="Growth of £1",
        height=410,
        margin=dict(l=35, r=20, t=70, b=35),
        hovermode="x unified",
        dragmode="zoom",
        xaxis_title="Date",
        yaxis_title="Portfolio value (£)",
    )
    growth.update_xaxes(range=[wealth.index.min(), wealth.index.max()])
    growth.update_yaxes(range=[0.0, max(1.0, float(wealth.max()) * 1.03)])
    _apply_time_range_buttons(growth)

    dd_pct = drawdown * 100.0
    lower = float(dd_pct.min())
    lower = min(-1.0, np.floor(lower / 5.0) * 5.0)
    dd = go.Figure()
    dd.add_trace(
        go.Scatter(
            x=dd_pct.index,
            y=dd_pct.to_numpy(),
            mode="lines",
            fill="tozeroy",
            name="Drawdown",
            hovertemplate="%{x|%d %b %Y}<br>%{y:.2f}%<extra></extra>",
        )
    )
    dd.update_layout(
        title="Portfolio drawdown",
        height=380,
        margin=dict(l=35, r=20, t=70, b=35),
        hovermode="x unified",
        dragmode="zoom",
        xaxis_title="Date",
        yaxis_title="Drawdown (%)",
    )
    dd.update_xaxes(range=[dd_pct.index.min(), dd_pct.index.max()])
    dd.update_yaxes(range=[lower, 0.0], ticksuffix="%")
    _apply_time_range_buttons(dd)

    growth_col, drawdown_col = st.columns(2, gap="medium")
    with growth_col:
        st.plotly_chart(growth, use_container_width=True, config=_plotly_config())
    with drawdown_col:
        st.plotly_chart(dd, use_container_width=True, config=_plotly_config())


def plot_historical_account_performance(curve: pd.DataFrame) -> None:
    """Render one historical performance chart beside account drawdown."""
    if curve is None or curve.empty:
        return

    display = curve.copy()
    display.index = pd.to_datetime(display.index, errors="coerce")
    display = display.loc[display.index.notna()].sort_index()
    if display.empty:
        return

    equity = pd.to_numeric(display.get("Equity"), errors="coerce").dropna()
    cumulative = pd.to_numeric(
        display.get("Cumulative Return"), errors="coerce"
    ).dropna()
    drawdown = pd.to_numeric(display.get("Drawdown"), errors="coerce").dropna()

    # Equity is the preferred account-performance view for a dated ledger.
    # Cumulative return is only a fallback when an equity series is unavailable,
    # so the dashboard never shows two visually duplicative performance charts.
    performance_fig = None
    if not equity.empty:
        performance_fig = go.Figure()
        performance_fig.add_trace(
            go.Scatter(
                x=equity.index,
                y=equity.to_numpy(),
                mode="lines",
                name="Account equity",
                hovertemplate="%{x|%d %b %Y}<br>$%{y:,.2f}<extra></extra>",
            )
        )
        performance_fig.update_layout(
            title="Historical account equity",
            height=390,
            margin=dict(l=35, r=20, t=70, b=35),
            hovermode="x unified",
            dragmode="zoom",
            xaxis_title="Date",
            yaxis_title="Equity",
        )
        performance_fig.update_xaxes(range=[equity.index.min(), equity.index.max()])
        y_min = float(equity.min())
        y_max = float(equity.max())
        span = y_max - y_min
        pad = span * 0.03 if span > 0 else max(abs(y_max) * 0.03, 1.0)
        performance_fig.update_yaxes(range=[y_min - pad, y_max + pad])
        _apply_time_range_buttons(performance_fig)

    elif not cumulative.empty:
        cumulative_pct = cumulative * 100.0
        performance_fig = go.Figure()
        performance_fig.add_trace(
            go.Scatter(
                x=cumulative_pct.index,
                y=cumulative_pct.to_numpy(),
                mode="lines",
                name="Cumulative return",
                hovertemplate="%{x|%d %b %Y}<br>%{y:.2f}%<extra></extra>",
            )
        )
        performance_fig.update_layout(
            title="Historical cumulative return",
            height=390,
            margin=dict(l=35, r=20, t=70, b=35),
            hovermode="x unified",
            dragmode="zoom",
            xaxis_title="Date",
            yaxis_title="Cumulative return (%)",
        )
        performance_fig.update_xaxes(
            range=[cumulative_pct.index.min(), cumulative_pct.index.max()]
        )
        performance_fig.update_yaxes(ticksuffix="%")
        _apply_time_range_buttons(performance_fig)

    drawdown_fig = None
    if not drawdown.empty:
        drawdown_pct = drawdown * 100.0
        lower = float(drawdown_pct.min())
        lower = min(-1.0, np.floor(lower / 5.0) * 5.0)
        drawdown_fig = go.Figure()
        drawdown_fig.add_trace(
            go.Scatter(
                x=drawdown_pct.index,
                y=drawdown_pct.to_numpy(),
                mode="lines",
                fill="tozeroy",
                name="Drawdown",
                hovertemplate="%{x|%d %b %Y}<br>%{y:.2f}%<extra></extra>",
            )
        )
        drawdown_fig.update_layout(
            title="Historical account drawdown",
            height=390,
            margin=dict(l=35, r=20, t=70, b=35),
            hovermode="x unified",
            dragmode="zoom",
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
        )
        drawdown_fig.update_xaxes(
            range=[drawdown_pct.index.min(), drawdown_pct.index.max()]
        )
        drawdown_fig.update_yaxes(range=[lower, 0.0], ticksuffix="%")
        _apply_time_range_buttons(drawdown_fig)

    left, right = st.columns(2, gap="large")
    if performance_fig is not None:
        with left:
            st.plotly_chart(
                performance_fig, use_container_width=True, config=_plotly_config()
            )
    if drawdown_fig is not None:
        with right:
            st.plotly_chart(
                drawdown_fig, use_container_width=True, config=_plotly_config()
            )


def plot_correlation_heatmap(returns: pd.DataFrame) -> None:
    """Render an interactive correlation heatmap with values in each cell."""
    if returns is None or returns.empty or returns.shape[1] < 2:
        st.info("At least two securities with overlapping return history are needed for correlation.")
        return

    corr = returns.corr().round(2)
    labels = corr.columns.astype(str).tolist()

    fig = go.Figure(
        data=go.Heatmap(
            z=corr.to_numpy(),
            x=labels,
            y=labels,
            zmin=-1,
            zmax=1,
            zmid=0,
            colorscale="RdBu_r",
            text=corr.to_numpy(),
            texttemplate="%{text:.2f}",
            textfont={"size": 12},
            hovertemplate=(
                "%{y} vs %{x}<br>Correlation: %{z:.3f}<extra></extra>"
            ),
            colorbar={
                "title": "Correlation",
                "tickvals": [-1, -0.5, 0, 0.5, 1],
            },
        )
    )

    fig.update_layout(
        title={"text": "Correlation Heatmap", "x": 0.0},
        xaxis={"title": "", "side": "bottom"},
        yaxis={"title": "", "autorange": "reversed"},
        margin={"l": 50, "r": 30, "t": 60, "b": 50},
        height=max(430, 55 * len(labels)),
    )

    st.caption(
        "Values range from -1 to +1. Red indicates positive co-movement, "
        "blue indicates negative co-movement, and values near 0 indicate weak linear relationship."
    )
    st.plotly_chart(fig, use_container_width=True, config=_plotly_config())

def plot_risk_contribution(contribution: pd.DataFrame) -> None:
    """Interactive risk contribution chart with its asset table aligned on the right."""
    if contribution is None or contribution.empty:
        return
    view = contribution.copy()
    values = pd.to_numeric(view["Risk Contribution %"], errors="coerce")
    labels = view["Ticker"].astype(str)
    valid = values.notna()
    values = values.loc[valid]
    labels = labels.loc[valid]
    if values.empty:
        return

    risk_table = pd.DataFrame({"Ticker": labels.tolist(), "Risk Contribution %": values.to_numpy()})
    if float(pd.to_numeric(risk_table["Risk Contribution %"], errors="coerce").abs().max()) <= 1.5:
        risk_table["Risk Contribution %"] *= 100.0
    risk_table = risk_table.sort_values("Risk Contribution %", ascending=False)

    chart_col, table_col = st.columns([0.9, 1.1], gap="medium")
    with chart_col:
        st.markdown("#### Risk contribution")
        # Always use a pie/donut chart for risk, including long/short books.
        # Pie slices cannot be negative, so signed component contributions are
        # converted to absolute magnitudes for slice sizing while the signed
        # value remains visible in the hover text and table.
        signed_pct = values * 100.0 if float(values.abs().max()) <= 1.5 else values
        pie_values = signed_pct.abs()
        if float(pie_values.sum()) > 0:
            hover_signed = [f"{v:+.2f}%" for v in signed_pct.to_numpy()]
            fig = go.Figure(data=[go.Pie(
                labels=labels.tolist(),
                values=pie_values.to_numpy(),
                hole=0.58,
                textinfo="percent",
                textposition="inside",
                customdata=hover_signed,
                hovertemplate=(
                    "%{label}<br>Share of absolute portfolio risk: %{percent}"
                    "<br>Signed risk contribution: %{customdata}<extra></extra>"
                ),
                sort=False,
            )])
            fig.update_layout(
                height=330,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
                annotations=[dict(text="Total<br>risk", x=0.5, y=0.5, showarrow=False, font_size=13)],
            )
            st.plotly_chart(fig, use_container_width=True, config=_plotly_config())
            if (signed_pct < 0).any():
                st.caption(
                    "Pie slices show each asset's absolute share of portfolio risk. "
                    "Negative signed contributions in the table/hover indicate hedging effects."
                )

    with table_col:
        st.markdown("#### Asset risk contribution")
        themed_dataframe(
            risk_table.style.format({"Risk Contribution %": "{:.2f}%"}),
            use_container_width=True, hide_index=True,
            height=min(330, 38 + 35 * max(1, len(risk_table))),
        )

def plot_holdings_donut(valued_positions: pd.DataFrame) -> None:
    """Compact interactive current-holdings mix for any valued portfolio route."""
    if valued_positions is None or valued_positions.empty:
        return

    view = valued_positions.copy()
    view["Ticker"] = view["Ticker"].astype(str)
    view["Signed Market Value"] = pd.to_numeric(view["Signed Market Value"], errors="coerce")
    view = view.dropna(subset=["Signed Market Value"])
    if view.empty:
        return

    grouped = view.groupby("Ticker", as_index=False)["Signed Market Value"].sum()
    grouped["Exposure"] = grouped["Signed Market Value"].abs()
    grouped = grouped[grouped["Exposure"] > 0].sort_values("Exposure", ascending=False)
    if grouped.empty or float(grouped["Exposure"].sum()) <= 0:
        return

    grouped["Weight %"] = grouped["Exposure"] / grouped["Exposure"].sum() * 100.0
    grouped["Direction"] = np.where(grouped["Signed Market Value"] >= 0, "Long", "Short")

    chart_col, table_col = st.columns([0.78, 1.22], gap="medium")
    with chart_col:
        st.markdown("#### Today's holdings")
        fig = go.Figure(data=[go.Pie(
            labels=grouped["Ticker"],
            values=grouped["Exposure"],
            hole=0.58,
            textinfo="percent",
            textposition="inside",
            hovertemplate=(
                "%{label}<br>Exposure: $%{value:,.2f}<br>Weight: %{percent}<extra></extra>"
            ),
            sort=False,
        )])
        fig.update_layout(
            height=330,
            margin=dict(l=5, r=5, t=10, b=5),
            showlegend=False,
            annotations=[dict(
                text=f"${grouped['Exposure'].sum():,.0f}<br><span style='font-size:10px'>Gross</span>",
                x=0.5, y=0.5, showarrow=False, font_size=14,
            )],
        )
        st.plotly_chart(fig, use_container_width=True, config=_plotly_config())

    with table_col:
        st.markdown("#### Holdings by market exposure")
        table = grouped[["Ticker", "Direction", "Signed Market Value", "Weight %"]].copy()
        table = table.rename(columns={"Signed Market Value": "Market Value"})

        # Market-monitoring fields always represent the latest Yahoo trading day,
        # regardless of any historical as-of date selected for portfolio valuation.
        today_snapshot = cached_today_snapshot(tuple(grouped["Ticker"].astype(str)))
        current_prices = pd.to_numeric(
            today_snapshot.get("Current Close", pd.Series(dtype=float)), errors="coerce"
        )
        day_change_pct = pd.to_numeric(
            today_snapshot.get("Day Change %", pd.Series(dtype=float)), errors="coerce"
        )
        table["Current Price"] = table["Ticker"].map(current_prices)
        table["Day Change %"] = table["Ticker"].map(day_change_pct)
        table = table[[
            "Ticker", "Direction", "Market Value", "Weight %",
            "Current Price", "Day Change %",
        ]]

        styled = table.style.format({
            "Market Value": "${:,.2f}",
            "Weight %": "{:.1f}%",
            "Current Price": lambda value: "—" if pd.isna(value) else f"${value:,.2f}",
            "Day Change %": lambda value: "—" if pd.isna(value) else f"{value:+.2f}%",
        })
        themed_dataframe(
            styled,
            sentiment_columns=["Day Change %"],
            use_container_width=True,
            hide_index=True,
            height=min(330, 38 + 35 * max(1, len(table))),
        )
        if (grouped["Signed Market Value"] < 0).any():
            st.caption("Short slices are sized by absolute market exposure; direction is shown in the table.")


def returns_signature(returns: pd.DataFrame) -> str:
    """Stable lightweight signature for on-demand simulation state."""
    if returns is None or returns.empty:
        return "empty"
    hashed = pd.util.hash_pandas_object(returns, index=True).to_numpy().tobytes()
    columns = "|".join(map(str, returns.columns)).encode("utf-8")
    return hashlib.sha256(hashed + columns).hexdigest()[:16]


def render_on_demand_monte_carlo(
    returns: pd.DataFrame,
    *,
    risk_free_rate: float,
    tickers: list[str],
    key_prefix: str,
) -> None:
    """Keep Monte Carlo completely idle until the user explicitly runs it."""
    st.markdown("### Monte Carlo optimisation")
    control_col, note_col = st.columns([1, 2], gap="large")
    with control_col:
        simulations_count = st.selectbox(
            "Number of simulated portfolios",
            [1_000, 5_000, 10_000, 25_000],
            index=2,
            key=f"{key_prefix}_mc_count",
        )
        run_clicked = st.button(
            "▶ Run Monte Carlo Simulation",
            key=f"{key_prefix}_mc_run",
            type="primary",
            use_container_width=True,
        )
    with note_col:
        st.info(
            "Monte Carlo runs only when requested. Normal dashboard interaction, zooming and filtering do not trigger a new simulation."
        )

    signature = returns_signature(returns)
    state_key = f"{key_prefix}_mc_ready_{signature}_{int(simulations_count)}_{float(risk_free_rate):.8f}"
    if run_clicked:
        st.session_state[state_key] = True

    if not st.session_state.get(state_key, False):
        st.markdown(
            "<div class='mc-placeholder'><b>Monte Carlo chart will appear here</b><br>"
            "Click <i>Run Monte Carlo Simulation</i> when you want to generate the opportunity set.</div>",
            unsafe_allow_html=True,
        )
        return

    with st.spinner(f"Simulating {int(simulations_count):,} portfolios…"):
        simulations, candidates = cached_monte_carlo(
            returns,
            risk_free_rate=risk_free_rate,
            simulations=int(simulations_count),
            seed=42,
        )

    if candidates.empty:
        st.info("Not enough common return history for multi-asset Monte Carlo analysis.")
        return

    plot_monte_carlo(simulations, candidates)
    candidates_display = candidates.copy()
    candidates_display["Annual Return"] *= 100.0
    candidates_display["Annual Volatility"] *= 100.0
    for ticker in tickers:
        if ticker in candidates_display.columns:
            candidates_display[ticker] *= 100.0

    st.markdown("#### Optimised portfolio combinations")
    st.caption(
        "These are the asset combinations found in the simulation for maximum risk-adjusted return, "
        "minimum volatility and maximum return."
    )
    candidate_specs = [
        ("Max Sharpe", "Maximum Sharpe", "Best simulated risk-adjusted return"),
        ("Min Volatility", "Minimum Risk", "Lowest simulated annual volatility"),
        ("Max Return", "Maximum Return", "Highest simulated annual return"),
    ]
    candidate_cols = st.columns(3, gap="medium")
    for col, (portfolio_name, heading, description) in zip(candidate_cols, candidate_specs):
        with col:
            selected = candidates_display.loc[
                candidates_display["Portfolio"].eq(portfolio_name)
            ]
            if selected.empty:
                st.info(f"{heading} result unavailable.")
                continue
            row = selected.iloc[0]
            st.markdown(f"**{heading}**")
            st.caption(description)
            m1, m2 = st.columns(2)
            m1.metric("Return", f"{float(row['Annual Return']):.2f}%")
            m2.metric("Volatility", f"{float(row['Annual Volatility']):.2f}%")
            st.metric("Sharpe", f"{float(row['Sharpe']):.2f}")
            allocation = pd.DataFrame({
                "Asset": [ticker for ticker in tickers if ticker in selected.columns],
                "Allocation %": [float(row[ticker]) for ticker in tickers if ticker in selected.columns],
            })
            allocation = allocation.loc[allocation["Allocation %"].abs() > 0.005]
            allocation = allocation.sort_values("Allocation %", ascending=False).reset_index(drop=True)
            themed_dataframe(
                allocation.round({"Allocation %": 2}),
                use_container_width=True,
                hide_index=True,
                height=min(360, 38 + 35 * max(len(allocation), 1)),
            )
    st.caption(
        f"{int(simulations_count):,} reproducible long-only, fully-invested random portfolios. "
        "Max Sharpe, minimum volatility and maximum return are search results, not forecasts."
    )


def plot_monte_carlo(simulations: pd.DataFrame, candidates: pd.DataFrame) -> None:
    """Interactive volatility-return opportunity set with zoom and hover."""
    if simulations is None or simulations.empty:
        return
    x = pd.to_numeric(simulations["Annual Volatility"], errors="coerce") * 100.0
    y = pd.to_numeric(simulations["Annual Return"], errors="coerce") * 100.0
    sharpe = pd.to_numeric(simulations["Sharpe"], errors="coerce")
    valid = x.notna() & y.notna() & sharpe.notna()
    if not valid.any():
        return

    plot_df = pd.DataFrame({
        "Annual volatility (%)": x.loc[valid],
        "Annual return (%)": y.loc[valid],
        "Sharpe": sharpe.loc[valid],
    })
    fig = px.scatter(
        plot_df,
        x="Annual volatility (%)",
        y="Annual return (%)",
        color="Sharpe",
        color_continuous_scale="Viridis",
        hover_data={
            "Annual volatility (%)": ":.2f",
            "Annual return (%)": ":.2f",
            "Sharpe": ":.3f",
        },
        title="Monte Carlo portfolio simulation",
    )
    fig.update_traces(marker=dict(size=5, opacity=0.55))

    if candidates is not None and not candidates.empty:
        for _, row in candidates.iterrows():
            cx = float(row["Annual Volatility"]) * 100.0
            cy = float(row["Annual Return"]) * 100.0
            fig.add_trace(go.Scatter(
                x=[cx], y=[cy], mode="markers+text",
                text=[str(row["Portfolio"])], textposition="top center",
                marker=dict(size=12, symbol="diamond", line=dict(width=1)),
                name=str(row["Portfolio"]),
                hovertemplate=(
                    f"{str(row['Portfolio'])}<br>Volatility: {cx:.2f}%<br>Return: {cy:.2f}%<extra></extra>"
                ),
            ))

    x_valid = x.loc[valid]
    y_valid = y.loc[valid]
    x_span = max(float(x_valid.max() - x_valid.min()), 0.5)
    y_span = max(float(y_valid.max() - y_valid.min()), 0.5)
    fig.update_layout(
        height=520,
        margin=dict(l=35, r=25, t=60, b=35),
        dragmode="zoom",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(range=[max(0.0, float(x_valid.min()) - x_span * 0.03), float(x_valid.max()) + x_span * 0.03])
    fig.update_yaxes(range=[float(y_valid.min()) - y_span * 0.04, float(y_valid.max()) + y_span * 0.04])
    st.plotly_chart(fig, use_container_width=True, config=_plotly_config())

#______________________________________________________________________________
# INTERACTIVE STRESS TEST
#______________________________________________________________________________

def render_stress_test(valued_positions: pd.DataFrame, portfolio_equity: float) -> None:
    """Compact, user-driven price-shock stress test for today's final positions."""
    if valued_positions is None or valued_positions.empty:
        return

    required = {"Ticker", "Signed Market Value"}
    if not required.issubset(set(valued_positions.columns)):
        return

    with st.expander("Stress Test", expanded=False):
        st.caption(
            "Apply hypothetical price moves to today's resolved positions. Nothing is run "
            "until you change a scenario. Shorts use signed market value, so their P&L "
            "responds in the correct direction."
        )

        scenario_frame = (
            valued_positions[["Ticker", "Signed Market Value"]]
            .copy()
            .groupby("Ticker", as_index=False)["Signed Market Value"]
            .sum()
        )
        scenario_frame["Shock %"] = 0.0

        edited = st.data_editor(
            scenario_frame[["Ticker", "Shock %"]],
            hide_index=True,
            use_container_width=True,
            disabled=["Ticker"],
            column_config={
                "Shock %": st.column_config.NumberColumn(
                    "Price shock %", min_value=-100.0, max_value=500.0, step=1.0, format="%.1f%%"
                )
            },
            key="interactive_stress_scenario",
        )

        shocks = dict(
            zip(
                edited["Ticker"].astype(str),
                pd.to_numeric(edited["Shock %"], errors="coerce").fillna(0.0) / 100.0,
            )
        )

        stress_input = scenario_frame[["Ticker", "Signed Market Value"]].copy()
        stress_results, stressed_value, impact, impact_pct = run_stress_test(
            stress_input, shocks, value_column="Signed Market Value"
        )

        stressed_equity = float(portfolio_equity) + float(impact)
        base_position_value = float(stress_input["Signed Market Value"].sum())

        # Two-by-two metric grid keeps labels and values readable at normal
        # dashboard widths instead of squeezing four cards into one narrow row.
        top_left, top_right = st.columns(2, gap="medium")
        top_left.metric("Current equity", f"${portfolio_equity:,.2f}")
        top_right.metric(
            "Stressed equity",
            f"${stressed_equity:,.2f}",
            delta=f"${impact:,.2f}",
        )

        bottom_left, bottom_right = st.columns(2, gap="medium")
        bottom_left.metric("Position P&L impact", f"${impact:,.2f}")
        bottom_right.metric(
            "Position change",
            "N/A" if abs(base_position_value) < 1e-12 else f"{impact_pct:.2%}",
        )

        display = stress_results.rename(
            columns={
                "Signed Market Value": "Current Signed Value",
                "Scenario Change": "Shock",
                "Stressed Value": "Stressed Signed Value",
                "Impact": "P&L Impact",
            }
        )
        display["Shock"] = display["Shock"] * 100.0
        themed_dataframe(
            display.style.format({
                "Current Signed Value": "${:,.2f}",
                "Shock": "{:.1f}%",
                "Stressed Signed Value": "${:,.2f}",
                "P&L Impact": "${:,.2f}",
            }),
            use_container_width=True,
            hide_index=True,
        )


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def load_validated_cleaning_policy() -> dict[str, Any]:
    """Load method-level evidence produced by mass_validation_lab.py.

    The policy contains reliability statistics only. It deliberately does not
    import the training portfolio's ticker, currency or fee values into a new
    user's portfolio.
    """
    candidates = [
        Path("validation_outputs/validated_cleaning_policy.json"),
        Path(__file__).resolve().parent / "validation_outputs" / "validated_cleaning_policy.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def add_validation_evidence(suggestions: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    if suggestions is None or suggestions.empty:
        return suggestions
    view = suggestions.copy()
    methods = (policy or {}).get("methods", {})
    validated_n = []
    validated_accuracy = []
    policy_auto = []
    for _, row in view.iterrows():
        key = f"{row.get('Field')}|{row.get('Repair Type')}"
        evidence = methods.get(key, {})
        validated_n.append(int(evidence.get("validated_decisions", 0) or 0))
        acc = evidence.get("validated_accuracy")
        validated_accuracy.append(float(acc) * 100 if acc is not None else np.nan)
        policy_auto.append(bool(evidence.get("auto_accept_recommended", False)))
    view["Validated Cases"] = validated_n
    view["Validation Accuracy %"] = validated_accuracy
    view["Policy Auto-Accept"] = policy_auto
    return view

def value_exists(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, float) and math.isnan(value):
        return False

    return str(value).strip().lower() not in {
        "",
        "nan",
        "none",
        "<na>",
    }


def dataframe_for_display(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return an Arrow-safe copy for Streamlit display only.

    The cleaning audit deliberately stores rich Python objects such as tuple
    fingerprints. PyArrow requires one consistent scalar type per column, so
    those audit objects are converted to readable strings only at the UI
    boundary. The underlying cleaning/accounting data is never modified.
    """
    if frame is None:
        return pd.DataFrame()

    result = frame.copy()

    for column in result.columns:
        if pd.api.types.is_object_dtype(result[column]):
            result[column] = result[column].map(
                lambda value: "" if value is None or (isinstance(value, float) and math.isnan(value))
                else repr(value) if isinstance(value, (tuple, list, dict, set))
                else str(value)
            )

    return result


def reset_review_state() -> None:
    st.session_state.accepted_review_holdings = {}
    st.session_state.pending_review_holdings = {}
    st.session_state.review_matches = {}
    st.session_state.review_confirmed_matches = {}


def clear_portfolio_state() -> None:
    for key in [
        "parsed_input",
        "working_holdings",
        "original_holdings_raw",
        "holdings_sheet_edit_mode",
        "holdings_sheet_editor_version",
        "valid_holdings",
        "review_holdings",
        "accepted_review_holdings",
        "pending_review_holdings",
        "review_matches",
        "review_confirmed_matches",
        "ledger_valid_map",
        "ledger_review",
        "ledger_corrections",
        "ledger_issues",
        "data_quality_report",
        "duplicate_rows",
        "accepted_transactions",
        "active_transactions",
        "ledger_editor_source",
        "original_transactions_raw",
        "original_cashflows_raw",
        "accepted_cashflows",
        "ledger_revision",
        "auto_clean_report",
        "auto_clean_applied",
        "auto_clean_candidate_report",
        "auto_clean_candidate_transactions",
        "auto_clean_candidate_cashflows",
        "auto_clean_candidate_ready",
        "cleaning_diagnosis_report",
        "cleaning_questions_ready",
        "cleaning_preferences",
        "cleaning_repair_suggestions",
        "cleaning_repair_diagnostics",
        "date_format_preference",
    ]:
        st.session_state.pop(key, None)

    # Streamlit data_editor widget state can otherwise survive a new upload.
    for key in list(st.session_state.keys()):
        if (
            str(key).startswith("ledger_repair_editor_")
            or str(key).startswith("guided_repair_editor")
        ):
            st.session_state.pop(key, None)


def apply_ledger_transactions(
    original_transactions: pd.DataFrame,
    cashflows: pd.DataFrame | None = None,
) -> None:
    """
    Run the existing, unmodified ledger pipeline (duplicate flagging ->
    clean_transaction_ledger -> data quality report -> ticker validation)
    against whatever transaction table is passed in, and store the
    results in session state exactly as the upload flow always has.
    The active cash-event table is stored alongside the accepted trades so
    downstream accounting always uses the same revision of the cleaned input.

    Used both by the normal upload path and by the Auto-Clean button, so
    the accounting/validation logic itself is defined in exactly one
    place and cannot drift between the two.
    """
    flagged_transactions = (
        flag_potential_duplicates(
            original_transactions
        )
    )

    clean_transactions, ledger_issues = (
        clean_transaction_ledger(
            flagged_transactions
        )
    )

    quality_report = (
        build_data_quality_report(
            original_transactions,
            clean_transactions,
            ledger_issues,
        )
    )

    duplicate_rows = (
        flagged_transactions[
            flagged_transactions[
                "Potential Duplicate"
            ].eq(True)
        ].copy()
    )

    editor_source = (
        flagged_transactions.copy()
    )

    editor_source[
        "Include in Analysis"
    ] = editor_source.index.isin(
        clean_transactions.index
    )

    st.session_state.accepted_transactions = (
        clean_transactions.copy()
    )

    # Single accounting source of truth: every upload, Auto-Clean run, and
    # accepted ledger revision replaces the active transaction table.
    st.session_state.active_transactions = (
        clean_transactions.copy().reset_index(drop=True)
    )

    st.session_state.accepted_cashflows = (
        cashflows.copy()
        if cashflows is not None
        else None
    )

    st.session_state.parsed_input[
        "cashflows"
    ] = (
        cashflows.copy()
        if cashflows is not None
        else None
    )

    # Force Streamlit to create a fresh data_editor after the accepted
    # dataset changes. A fixed widget key can preserve stale rows across
    # reruns even when ledger_editor_source has been replaced.
    st.session_state.ledger_revision = (
        int(st.session_state.get("ledger_revision", 0))
        + 1
    )

    st.session_state.ledger_editor_source = (
        editor_source.reset_index(
            drop=True
        )
    )

    st.session_state.parsed_input[
        "transactions"
    ] = clean_transactions.copy()

    st.session_state.ledger_issues = ledger_issues

    st.session_state.data_quality_report = (
        quality_report
    )

    st.session_state.duplicate_rows = (
        duplicate_rows
    )

    valid_map, ledger_review = (
        validate_ledger_tickers(
            clean_transactions
        )
    )

    st.session_state.ledger_valid_map = valid_map
    st.session_state.ledger_review = ledger_review
    st.session_state.ledger_corrections = {}

def set_active_transactions(transactions):
    """
    Make this dataframe the single active transaction ledger
    used by the accounting engine.
    """

    active = transactions.copy().reset_index(drop=True)

    st.session_state["active_transactions"] = active
    st.session_state["accepted_transactions"] = active.copy()

    st.session_state.parsed_input[
        "transactions"
    ] = active.copy()

    # Force any transaction editor to rebuild
    st.session_state["ledger_revision"] = (
        st.session_state.get("ledger_revision", 0)
        + 1
    )

#______________________________________________________________________________
# DASHBOARD SETUP
#______________________________________________________________________________

st.set_page_config(
    page_title="Portfolio Analytics",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
        --pa-navy: #082B4C;
        --pa-navy-2: #0E3A62;
        --pa-gold: #C99A35;
        --pa-gold-soft: #E7D2A0;
        --pa-bg: #F7F6F2;
        --pa-card: #FFFFFF;
        --pa-soft: #FBFAF7;
        --pa-header-soft: #F3F2EE;
        --pa-line: #E5E1D8;
        --pa-text: #14283F;
        --pa-muted: #6D7785;
        --pa-success: #087A55;
        --pa-danger: #C23B35;
        --pa-warning: #9A6A13;
    }

    /* THEME ONLY: existing Streamlit layout and section order are untouched. */
    html, body, [class*="css"] {
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .stApp {
        background: var(--pa-bg);
        color: var(--pa-text);
    }
    .block-container { max-width: 1500px; padding-top: 1.35rem; padding-bottom: 3rem; }

    /* HEADER: the strongest use of navy, matching the reference image. */
    .pa-hero {
        background: linear-gradient(105deg, #062B4D 0%, #0A355B 100%);
        border: 1px solid rgba(201,154,53,.38);
        border-radius: 16px;
        padding: 22px 28px;
        margin-bottom: 20px;
        box-shadow: 0 8px 22px rgba(8,43,76,.09);
    }
    .pa-hero h1 { color: #E0AF45 !important; margin: 0; font-size: 2rem; letter-spacing: -.025em; }
    .pa-hero p { color: #F0C96B !important; margin: 7px 0 0; font-size: .98rem; font-weight: 520; }

    /* TYPOGRAPHY: restrained navy rather than heavy colour blocks. */
    h1, h2, h3, h4 { color: #1C2C40 !important; letter-spacing: -.018em; }
    p, label, .stMarkdown { color: var(--pa-text); }
    [data-testid="stCaptionContainer"] { color: var(--pa-muted); }
    hr { border-color: var(--pa-line) !important; opacity: .85; }

    /* METRICS: white cards with gentle borders, like the reference. */
    [data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid var(--pa-line);
        border-radius: 12px;
        padding: 14px 16px;
        box-shadow: 0 3px 10px rgba(8,43,76,.035);
        min-height: 104px;
    }
    [data-testid="stMetricLabel"] { color: #647184; font-weight: 600; }
    [data-testid="stMetricValue"] { color: var(--pa-navy); font-weight: 740; }

    /* BUTTONS: important actions use gold; supporting actions stay white/navy. */
    div.stButton > button, div.stDownloadButton > button {
        border-radius: 9px !important;
        font-weight: 650 !important;
        transition: all .16s ease;
        box-shadow: none !important;
    }
    div.stButton > button[kind="primary"] {
        background: #C99A35 !important;
        border: 1px solid #C99A35 !important;
        color: #082B4C !important;
    }
    div.stButton > button[kind="primary"]:hover {
        background: #D8AD51 !important;
        border-color: #B98927 !important;
        color: #062B4D !important;
    }
    div.stButton > button[kind="secondary"] {
        background: #FFFFFF !important;
        border: 1px solid #C99A35 !important;
        color: #082B4C !important;
    }
    div.stButton > button[kind="secondary"]:hover {
        background: #FFF9EC !important;
        border-color: #A97B24 !important;
    }
    div.stDownloadButton > button {
        background: #082B4C !important;
        border: 1px solid #082B4C !important;
        color: #FFFFFF !important;
    }
    div.stDownloadButton > button:hover {
        background: #0E3A62 !important;
        border-color: #C99A35 !important;
    }

    /* INPUTS / SELECTS */
    [data-baseweb="input"] > div,
    [data-baseweb="select"] > div,
    [data-testid="stNumberInputContainer"],
    [data-testid="stTextInputRootElement"] {
        background: #FFFFFF !important;
        border-color: #DDD8CF !important;
        border-radius: 9px !important;
    }
    [data-baseweb="input"] > div:focus-within,
    [data-baseweb="select"] > div:focus-within,
    [data-testid="stTextInputRootElement"]:focus-within {
        border-color: #C99A35 !important;
        box-shadow: 0 0 0 1px rgba(201,154,53,.35) !important;
    }

    /* FILE UPLOADER */
    [data-testid="stFileUploaderDropzone"] {
        background: #FFFFFF !important;
        border: 1px dashed #D2CCC0 !important;
        border-radius: 12px !important;
    }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #C99A35 !important; }

    /* EXPANDERS / TABS */
    details[data-testid="stExpander"] {
        background: #FFFFFF;
        border: 1px solid var(--pa-line) !important;
        border-radius: 11px !important;
        overflow: hidden;
        box-shadow: 0 2px 7px rgba(8,43,76,.025);
    }
    details[data-testid="stExpander"] > summary { color: var(--pa-navy) !important; font-weight: 620; }
    details[data-testid="stExpander"][open] > summary {
        border-bottom: 1px solid var(--pa-line);
        background: #FAF9F6;
    }
    [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 1px solid var(--pa-line); }
    [data-baseweb="tab"] { color: #697585 !important; font-weight: 620; }
    [aria-selected="true"][data-baseweb="tab"] { color: var(--pa-navy) !important; }

    /* ALERTS: keep Streamlit's semantic colours but soften the box treatment. */
    [data-testid="stAlert"] {
        border-radius: 10px !important;
        border-width: 1px !important;
        box-shadow: none !important;
    }

    /* CHART SURFACES */
    [data-testid="stPlotlyChart"], [data-testid="stVegaLiteChart"] {
        background: #FFFFFF !important;
        border: 1px solid var(--pa-line);
        border-radius: 13px;
        padding: .3rem;
        box-shadow: 0 3px 10px rgba(8,43,76,.03);
    }
    [data-testid="stPlotlyChart"] > div,
    [data-testid="stPlotlyChart"] .js-plotly-plot,
    [data-testid="stPlotlyChart"] .plot-container,
    [data-testid="stPlotlyChart"] .svg-container {
        background: #FFFFFF !important;
    }

    /* READ-ONLY TABLES: reference-image styling — light header, white rows. */
    .pa-table-shell {
        width: 100%;
        overflow: auto;
        border: 1px solid #E3DED5;
        border-top: 2px solid rgba(201,154,53,.58);
        border-radius: 11px;
        background: #FFFFFF;
        box-shadow: 0 3px 10px rgba(8,43,76,.03);
        margin: .35rem 0 .8rem;
    }
    .pa-table-shell table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: .94rem;
        color: #172A40;
        margin: 0;
    }
    .pa-table-shell thead th {
        position: sticky; top: 0; z-index: 2;
        background: #F3F2EE !important;
        color: #132A43 !important;
        font-weight: 700 !important;
        text-align: left !important;
        border: 0 !important;
        border-bottom: 1px solid #DED9CF !important;
        padding: .65rem .76rem !important;
        white-space: nowrap;
    }
    .pa-table-shell tbody td, .pa-table-shell tbody th {
        background: #FFFFFF !important;
        color: #172A40;
        border: 0 !important;
        border-bottom: 1px solid #ECE8E0 !important;
        padding: .59rem .76rem !important;
    }
    .pa-table-shell tbody tr:last-child td,
    .pa-table-shell tbody tr:last-child th { border-bottom: 0 !important; }

    /* EDITABLE GRIDS: retain native Streamlit editing behaviour. */
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
        border: 1px solid var(--pa-line) !important;
        border-radius: 11px !important;
        overflow: hidden;
        background: #FFFFFF;
        box-shadow: 0 2px 8px rgba(8,43,76,.025);
    }

    .mc-placeholder {
        min-height: 185px;
        border: 1px dashed #D2CCC0;
        border-radius: 12px;
        background: #FFFFFF;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        color: #6B7787;
        margin-top: .5rem;
        padding: 2rem;
    }

    button:focus-visible, input:focus-visible, textarea:focus-visible {
        outline: 2px solid rgba(201,154,53,.35) !important;
        outline-offset: 2px;
    }
    </style>
    <div class="pa-hero">
      <h1>Portfolio Analytics</h1>
      <p>From uploaded financial data to validated positions, today's valuation, risk and portfolio analytics.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


#______________________________________________________________________________
# SESSION STATE
#______________________________________________________________________________

if "accepted_review_holdings" not in st.session_state:
    st.session_state.accepted_review_holdings = {}

if "pending_review_holdings" not in st.session_state:
    st.session_state.pending_review_holdings = {}

if "review_matches" not in st.session_state:
    st.session_state.review_matches = {}

if "review_confirmed_matches" not in st.session_state:
    st.session_state.review_confirmed_matches = {}

if "manual_optional_columns" not in st.session_state:
    st.session_state.manual_optional_columns = []

if "manual_portfolio_draft" not in st.session_state:
    st.session_state.manual_portfolio_draft = pd.DataFrame([
        {
            "Ticker": "",
            "Quantity": "",
            "Price": "",
        }
    ])


#______________________________________________________________________________
# CHOOSE INPUT METHOD
#______________________________________________________________________________

input_method_col, valuation_method_col = st.columns(2, gap="large")

with input_method_col:
    st.subheader("Build Your Portfolio")
    input_method = st.radio(
        "Choose how you want to enter your portfolio",
        [
            "Upload Portfolio",
            "Enter Manually",
        ],
        horizontal=True,
    )

with valuation_method_col:
    valuation_method_placeholder = st.empty()

current_cash = st.number_input(
    "Current / starting cash balance",
    value=0.0,
    step=1000.0,
    help=(
        "For a holdings snapshot, enter today's account cash balance if known. "
        "For a ledger, enter only starting cash not already recorded as a deposit."
    ),
)


#______________________________________________________________________________
# OPTION 1 — UPLOAD PORTFOLIO
#______________________________________________________________________________

if input_method == "Upload Portfolio":

    uploaded = st.file_uploader(
        "Upload portfolio",
        type=["csv", "xlsx"],
        help=(
            "Simple holdings: Ticker + Quantity. "
            "Ledger: Date + Type + Ticker + Quantity + Price/Gross Value."
        ),
    )

    if uploaded is None:
        st.stop()

    file_signature = hashlib.sha256(
        uploaded.getvalue()
    ).hexdigest()

    source_signature = f"upload:{file_signature}"

    if (
        st.session_state.get("portfolio_signature")
        != source_signature
    ):
        clear_portfolio_state()

        try:
            parsed = read_upload(
                uploaded.getvalue(),
                uploaded.name,
            )

        except Exception as exc:
            st.error(str(exc))
            st.stop()

        st.session_state.portfolio_signature = (
            source_signature
        )

        st.session_state.parsed_input = parsed

        if parsed["mode"] == "holdings":
            # Keep the complete uploaded sheet intact for review/edit.  The
            # parser works from a separate prepared copy, so no source column
            # disappears from the user's view.
            st.session_state.original_holdings_raw = parsed["holdings"].copy()
            st.session_state.holdings_sheet_edit_mode = False
            st.session_state.holdings_sheet_editor_version = 0

            # Security matching happens immediately on upload. Exact Yahoo
            # tickers enter the valid set; fuzzy/company-name matches and bad
            # quantities are quarantined from analysis until explicitly accepted.
            working = prepare_holdings_for_review(
                parsed["holdings"]
            )

            valid, review = validate_holdings(
                working
            )

            st.session_state.working_holdings = working
            st.session_state.valid_holdings = valid
            st.session_state.review_holdings = review
            reset_review_state()

        elif parsed["mode"] == "market_data":
            # Market-price history is a valid financial dataset, but it must not
            # be pushed through transaction accounting.  Keep it parsed and
            # available for inspection / future market-data workflows.
            st.session_state.parsed_market_data = parsed["market_data"].copy()

        else:
            original_transactions = (
                parsed["transactions"].copy()
            )

            # Kept untouched so Auto-Clean (below) always re-cleans from
            # the true original upload, not from a previously-cleaned copy.
            st.session_state.original_transactions_raw = (
                original_transactions.copy()
            )

            original_cashflows = parsed.get(
                "cashflows"
            )

            st.session_state.original_cashflows_raw = (
                original_cashflows.copy()
                if original_cashflows is not None
                else None
            )

            canonical_cashflows = (
                extract_cash_events(
                    original_cashflows
                )
                if original_cashflows is not None
                else None
            )

            apply_ledger_transactions(
                original_transactions,
                canonical_cashflows,
            )

        st.rerun()


    #__________________________________________________________________
    # INTERACTIVE AUTO-CLEAN REVIEW
    #__________________________________________________________________
    # Parsing has already identified the file schema. The cleaner now works on
    # the raw accepted ledger, but does NOT alter accounting until the user
    # explicitly accepts the cleaning preview.

    if (
        st.session_state.get("parsed_input", {}).get("mode")
        == "ledger"
    ):
        with st.expander(
            "Data Quality Check & Cleaning",
            expanded=False,
        ):
            st.caption(
                "Check the uploaded data for missing, inconsistent or unusual values "
                "before it is used in portfolio accounting and analytics."
            )

            raw_preview = st.session_state.get(
                "original_transactions_raw",
                pd.DataFrame(),
            )
            if not raw_preview.empty:
                with st.expander(
                    f"Raw upload preview ({len(raw_preview)} rows, "
                    f"{len(raw_preview.columns)} columns)"
                ):
                    themed_dataframe(
                        dataframe_for_display(raw_preview.head(50)),
                        use_container_width=True,
                        hide_index=True,
                    )

            if st.button(
                "Run Data Quality Check",
                key="start_cleaning_button",
                type="primary",
            ):
                raw_for_cleaning = st.session_state.original_transactions_raw

                # First pass is diagnostic only. It identifies uncertainty and
                # prepares questions; it does NOT alter the accounting ledger.
                _, diagnosis_report = auto_clean_ledger(
                    raw_for_cleaning,
                    preferences={
                        "date_preference": "auto",
                        "duplicate_policy": "quarantine_all",
                        "incomplete_policy": "reject",
                        "price_outlier_policy": "keep_warning",
                        "action_overrides": {},
                    },
                )

                repair_suggestions, repair_diagnostics = diagnose_repairs(
                    raw_for_cleaning
                )
                st.session_state.cleaning_repair_suggestions = repair_suggestions
                st.session_state.cleaning_repair_diagnostics = repair_diagnostics

                st.session_state.cleaning_diagnosis_report = diagnosis_report
                st.session_state.cleaning_questions_ready = True
                st.session_state.auto_clean_candidate_ready = False
                st.session_state.pop("auto_clean_candidate_report", None)
                st.session_state.pop("auto_clean_candidate_transactions", None)
                st.session_state.pop("auto_clean_candidate_cashflows", None)
                st.rerun()

            questions_ready = bool(
                st.session_state.get("cleaning_questions_ready", False)
            )

            if questions_ready and not st.session_state.get("auto_clean_candidate_ready", False):
                diagnosis = st.session_state.get("cleaning_diagnosis_report", {})
                diagnosis_audit = diagnosis.get("audit_table", pd.DataFrame())

                st.markdown("#### Cleaning Questions")
                st.caption(
                    "These questions come from uncertainty detected in this file. "
                    "Your answers become explicit cleaning rules for this run."
                )

                ambiguous_dates = 0
                hard_duplicates = int(diagnosis.get("hard_duplicate_rows", 0))
                price_outliers = 0
                incomplete_rows = 0
                unknown_actions: list[str] = []

                if not diagnosis_audit.empty:
                    warnings_series = diagnosis_audit.get(
                        "Warnings",
                        pd.Series("", index=diagnosis_audit.index),
                    ).astype(str)
                    ambiguous_dates = int(
                        warnings_series.str.contains(
                            "ambiguous numeric date",
                            case=False,
                            na=False,
                        ).sum()
                    )

                    if "Price Outlier" in diagnosis_audit.columns:
                        price_outliers = int(
                            diagnosis_audit["Price Outlier"].fillna(False).sum()
                        )

                    reason_series = diagnosis_audit.get(
                        "Reason",
                        pd.Series("", index=diagnosis_audit.index),
                    ).astype(str)
                    incomplete_rows = int(
                        reason_series.str.contains(
                            "missing|no usable",
                            case=False,
                            regex=True,
                            na=False,
                        ).sum()
                    )

                    unknown_mask = reason_series.str.contains(
                        "unrecognised action",
                        case=False,
                        na=False,
                    )
                    if unknown_mask.any() and "Original Action" in diagnosis_audit.columns:
                        unknown_actions = sorted({
                            str(value).strip()
                            for value in diagnosis_audit.loc[
                                unknown_mask,
                                "Original Action",
                            ].dropna()
                            if str(value).strip()
                        })

                q1, q2, q3, q4 = st.columns(4)
                q1.metric("Ambiguous dates", ambiguous_dates)
                q2.metric("Duplicate-ID rows", hard_duplicates)
                q3.metric("Unusual prices", price_outliers)
                q4.metric("Incomplete rows", incomplete_rows)

                repair_suggestions = st.session_state.get(
                    "cleaning_repair_suggestions",
                    pd.DataFrame(),
                )
                repair_diagnostics = st.session_state.get(
                    "cleaning_repair_diagnostics",
                    {},
                )

                learned_profile = repair_diagnostics.get("pattern_profile", {})
                if learned_profile:
                    with st.expander("What this portfolio taught the cleaner", expanded=False):
                        st.caption(
                            "The cleaner learns auditable relationships from the populated rows "
                            "before suggesting repairs. These are rules and statistical patterns, "
                            "not hidden LLM guesses."
                        )
                        themed_dataframe(
                            dataframe_for_display(profile_summary_table(learned_profile)),
                            use_container_width=True,
                            hide_index=True,
                        )

                edited_repairs = pd.DataFrame()
                if repair_suggestions is not None and not repair_suggestions.empty:
                    st.markdown("**Repair suggestions**")
                    st.caption(
                        "The repair engine now checks both missing values and wrong-but-present contradictions. "
                        "It only proposes a correction when the evidence identifies a plausible replacement. "
                        "Nothing is applied until you accept it."
                    )

                    validated_policy = load_validated_cleaning_policy()
                    if validated_policy:
                        st.caption(
                            f"Validation policy loaded: {int(validated_policy.get('scenarios', 0)):,} synthetic scenarios. "
                            "The policy controls trust in repair METHODS while this uploaded portfolio supplies its own values and patterns."
                        )
                    repair_view = add_validation_evidence(repair_suggestions, validated_policy)
                    repair_view.insert(
                        0,
                        "Accept Repair",
                        [
                            (
                                bool(policy_ok)
                                if int(validated_cases) > 0
                                else (rtype == "RECONSTRUCTED")
                            )
                            or (
                                rtype == "INFERRED"
                                and float(conf) >= 0.98
                                and (
                                    int(validated_cases) == 0
                                    or (pd.notna(validation_accuracy) and float(validation_accuracy) >= 99.9)
                                )
                            )
                            for rtype, conf, validated_cases, validation_accuracy, policy_ok in zip(
                                repair_view["Repair Type"],
                                repair_view["Confidence"],
                                repair_view["Validated Cases"],
                                repair_view["Validation Accuracy %"],
                                repair_view["Policy Auto-Accept"],
                            )
                        ],
                    )
                    repair_view["Confidence"] = (
                        pd.to_numeric(repair_view["Confidence"], errors="coerce") * 100
                    ).round(1)
                    repair_view["Proposed Value"] = repair_view["Proposed Value"].astype(str)

                    edited_repairs = st.data_editor(
                        dataframe_for_display(repair_view),
                        use_container_width=True,
                        hide_index=False,
                        key="guided_repair_editor",
                        column_config={
                            "Accept Repair": st.column_config.CheckboxColumn(
                                "Accept Repair",
                                help="Only checked repairs will be applied before cleaning.",
                            ),
                            "Proposed Value": st.column_config.TextColumn(
                                "Proposed Value",
                                help="You can edit the proposed value before applying it.",
                            ),
                            "Confidence": st.column_config.NumberColumn(
                                "Confidence %",
                                format="%.1f",
                            ),
                        },
                        disabled=[
                            "Source Row",
                            "Field",
                            "Original Value",
                            "Repair Type",
                            "Confidence",
                            "Basis",
                            "Source Column",
                            "Status",
                            "Validated Cases",
                            "Validation Accuracy %",
                            "Policy Auto-Accept",
                        ],
                    )

                    rm1, rm2, rm3 = st.columns(3)
                    rm1.metric("Reconstructed", int(repair_diagnostics.get("reconstructed", 0)))
                    rm2.metric("Inferred", int(repair_diagnostics.get("inferred", 0)))
                    rm3.metric("Estimated", int(repair_diagnostics.get("estimated", 0)))

                    manual_repair_questions = repair_diagnostics.get("manual_questions", pd.DataFrame())
                    if isinstance(manual_repair_questions, pd.DataFrame) and not manual_repair_questions.empty:
                        with st.expander("Rows the cleaner refuses to guess", expanded=True):
                            st.caption(
                                "These rows contain missing or contradictory information that the cleaner cannot resolve safely. "
                                "They should be reviewed before accounting is treated as reconciled."
                            )
                            themed_dataframe(
                                dataframe_for_display(manual_repair_questions),
                                use_container_width=True,
                                hide_index=True,
                            )

                manual_repair_questions = repair_diagnostics.get(
                    "manual_questions",
                    pd.DataFrame(),
                )
                if isinstance(manual_repair_questions, pd.DataFrame) and not manual_repair_questions.empty:
                    st.markdown("**Missing fields that should not be predicted**")
                    st.caption(
                        "Dates, BUY/SELL direction and original transaction IDs are not "
                        "fabricated. These rows remain visible for your decision."
                    )
                    themed_dataframe(
                        dataframe_for_display(manual_repair_questions),
                        use_container_width=True,
                        hide_index=True,
                    )

                with st.form("guided_cleaning_questions"):
                    if ambiguous_dates:
                        st.markdown("**1. Ambiguous dates**")
                        date_choice = st.radio(
                            "Some dates could be interpreted as either DD/MM/YYYY or MM/DD/YYYY. Which convention should win when the date itself cannot decide?",
                            [
                                "Automatic detection (recommended)",
                                "DD/MM/YYYY",
                                "MM/DD/YYYY",
                            ],
                            key="cleaning_date_choice",
                        )
                    else:
                        date_choice = "Automatic detection (recommended)"

                    if hard_duplicates:
                        st.markdown("**2. Repeated transaction IDs**")
                        duplicate_choice = st.radio(
                            "How should repeated transaction IDs be handled?",
                            [
                                "Quarantine every occurrence (recommended)",
                                "Keep the first occurrence and quarantine later repeats",
                            ],
                            key="cleaning_duplicate_choice",
                        )
                    else:
                        duplicate_choice = "Quarantine every occurrence (recommended)"

                    if unknown_actions:
                        st.markdown("**3. Unrecognised transaction actions**")
                        st.caption(
                            "Map only actions you understand. Leaving one as Review prevents the cleaner from guessing its financial meaning."
                        )

                        action_choices = {}
                        action_options = [
                            "Keep for Review",
                            "BUY",
                            "SELL",
                            "DIVIDEND",
                            "DEPOSIT",
                            "WITHDRAWAL",
                            "TRANSFER IN",
                            "TRANSFER OUT",
                            "IGNORE",
                        ]

                        for index, raw_action in enumerate(unknown_actions):
                            action_choices[raw_action] = st.selectbox(
                                f"What does '{raw_action}' mean?",
                                action_options,
                                key=f"action_override_{index}",
                            )
                    else:
                        action_choices = {}

                    if price_outliers:
                        st.markdown("**4. Unusual price movements**")
                        price_choice = st.radio(
                            "The cleaner found unusually large price moves. What should happen to those rows?",
                            [
                                "Keep them but show a warning (recommended)",
                                "Move them to Review before inclusion",
                            ],
                            key="cleaning_price_choice",
                        )
                    else:
                        price_choice = "Keep them but show a warning (recommended)"

                    if incomplete_rows:
                        st.markdown("**5. Incomplete financial rows**")
                        incomplete_choice = st.radio(
                            "Rows missing required accounting fields cannot safely enter position calculations. Where should they appear?",
                            [
                                "Reject from analysis but keep in the audit (recommended)",
                                "Move them to Review instead of Reject",
                            ],
                            key="cleaning_incomplete_choice",
                        )
                    else:
                        incomplete_choice = "Reject from analysis but keep in the audit (recommended)"

                    build_preview = st.form_submit_button(
                        "Apply Answers & Build Cleaning Preview",
                        type="primary",
                        use_container_width=True,
                    )

                if build_preview:
                    date_preference = {
                        "Automatic detection (recommended)": "auto",
                        "DD/MM/YYYY": "dayfirst",
                        "MM/DD/YYYY": "monthfirst",
                    }[date_choice]

                    duplicate_policy = (
                        "keep_first"
                        if duplicate_choice.startswith("Keep the first")
                        else "quarantine_all"
                    )

                    price_outlier_policy = (
                        "review"
                        if price_choice.startswith("Move them")
                        else "keep_warning"
                    )

                    incomplete_policy = (
                        "review"
                        if incomplete_choice.startswith("Move them")
                        else "reject"
                    )

                    action_overrides = {
                        str(raw).strip().upper(): value
                        for raw, value in action_choices.items()
                        if value != "Keep for Review"
                    }

                    preferences = {
                        "date_preference": date_preference,
                        "duplicate_policy": duplicate_policy,
                        "price_outlier_policy": price_outlier_policy,
                        "incomplete_policy": incomplete_policy,
                        "action_overrides": action_overrides,
                    }

                    raw_for_cleaning = st.session_state.original_transactions_raw.copy()

                    repair_application_log = pd.DataFrame()
                    if (
                        isinstance(edited_repairs, pd.DataFrame)
                        and not edited_repairs.empty
                        and repair_suggestions is not None
                        and not repair_suggestions.empty
                    ):
                        accepted_indices = [
                            idx
                            for idx, value in edited_repairs["Accept Repair"].items()
                            if bool(value)
                        ]
                        edited_values = {
                            idx: edited_repairs.at[idx, "Proposed Value"]
                            for idx in accepted_indices
                        }
                        raw_for_cleaning, repair_application_log = apply_repairs(
                            raw_for_cleaning,
                            repair_suggestions,
                            accepted_indices=accepted_indices,
                            edited_values=edited_values,
                        )

                    cleaned_transactions, clean_report = auto_clean_ledger(
                        raw_for_cleaning,
                        preferences=preferences,
                    )
                    clean_report["repair_suggestions"] = (
                        repair_suggestions.copy()
                        if isinstance(repair_suggestions, pd.DataFrame)
                        else pd.DataFrame()
                    )
                    clean_report["applied_repairs"] = repair_application_log.copy()
                    clean_report["repaired_source"] = raw_for_cleaning.copy()

                    ledger_cash_events = extract_cash_events(raw_for_cleaning)
                    original_cashflows = st.session_state.get("original_cashflows_raw")
                    external_cash_events = (
                        extract_cash_events(original_cashflows)
                        if original_cashflows is not None
                        else None
                    )
                    candidate_cashflows = combine_cash_events(
                        external_cash_events,
                        ledger_cash_events,
                    )

                    clean_report["accounting_cash_events"] = ledger_cash_events.copy()
                    clean_report["user_preferences"] = preferences

                    st.session_state.cleaning_preferences = preferences
                    st.session_state.auto_clean_candidate_transactions = cleaned_transactions.copy()
                    st.session_state.auto_clean_candidate_cashflows = (
                        candidate_cashflows.copy()
                        if candidate_cashflows is not None
                        else None
                    )
                    st.session_state.auto_clean_candidate_report = clean_report
                    st.session_state.auto_clean_candidate_ready = True
                    st.session_state.cleaning_questions_ready = False
                    st.rerun()

            candidate_ready = bool(
                st.session_state.get("auto_clean_candidate_ready", False)
            )

            applied = bool(
                st.session_state.get("auto_clean_applied", False)
            )

            clean_report = None
            report_is_preview = False

            if candidate_ready:
                clean_report = st.session_state.get(
                    "auto_clean_candidate_report"
                )
                report_is_preview = True
            elif applied:
                clean_report = st.session_state.get(
                    "auto_clean_report"
                )

            if clean_report is not None:
                if report_is_preview:
                    st.info(
                        "Cleaning preview ready. Nothing in the portfolio analysis "
                        "has changed yet. Review the tables below before accepting."
                    )
                else:
                    st.success(
                        "Cleaned data accepted. The active accounting ledger below "
                        "now uses this accepted cleaning revision."
                    )

                # --------------------------------------------------------------
                # Cleaning summary — modelled on the standalone cleaner UI, but
                # driven by our audit-first financial cleaning engine.
                # --------------------------------------------------------------
                st.markdown("#### Cleaning Summary")

                summary_cols = st.columns(6)
                summary_cols[0].metric(
                    "Rows read",
                    int(clean_report.get("rows_read", 0)),
                )
                summary_cols[1].metric(
                    "Auto accepted",
                    int(clean_report.get("accepted_rows", 0)),
                )
                summary_cols[2].metric(
                    "Accepted + warning",
                    int(clean_report.get("warning_rows", 0)),
                )
                summary_cols[3].metric(
                    "Review",
                    int(clean_report.get("review_rows", 0)),
                )
                summary_cols[4].metric(
                    "Rejected",
                    int(clean_report.get("rejected_rows", 0)),
                )
                summary_cols[5].metric(
                    "Silently dropped",
                    int(clean_report.get("silently_dropped_rows", 0)),
                )

                detail_cols = st.columns(5)
                detail_cols[0].metric(
                    "Tickers normalised",
                    int(clean_report.get("tickers_normalised", 0)),
                )
                detail_cols[1].metric(
                    "Actions normalised",
                    int(clean_report.get("actions_normalised", 0)),
                )
                detail_cols[2].metric(
                    "Dates reparsed",
                    int(clean_report.get("dates_reparsed", 0)),
                )
                detail_cols[3].metric(
                    "Numbers coerced",
                    int(clean_report.get("numbers_coerced", 0)),
                )
                detail_cols[4].metric(
                    "Hard duplicate rows",
                    int(clean_report.get("hard_duplicate_rows", 0)),
                )

                applied_repairs = clean_report.get("applied_repairs", pd.DataFrame())
                repair_suggestions_report = clean_report.get("repair_suggestions", pd.DataFrame())

                if isinstance(applied_repairs, pd.DataFrame) and not applied_repairs.empty:
                    st.markdown("##### Repairs applied before cleaning")
                    r1, r2, r3 = st.columns(3)
                    r1.metric(
                        "Reconstructed",
                        int((applied_repairs["Repair Type"] == "RECONSTRUCTED").sum()),
                    )
                    r2.metric(
                        "Inferred",
                        int((applied_repairs["Repair Type"] == "INFERRED").sum()),
                    )
                    r3.metric(
                        "Estimated",
                        int((applied_repairs["Repair Type"] == "ESTIMATED").sum()),
                    )
                    with st.expander("See applied repair audit"):
                        themed_dataframe(
                            dataframe_for_display(applied_repairs),
                            use_container_width=True,
                            hide_index=True,
                        )
                elif isinstance(repair_suggestions_report, pd.DataFrame) and not repair_suggestions_report.empty:
                    st.caption(
                        "Repair suggestions were detected, but none were accepted for this preview."
                    )

                change_summary = pd.DataFrame([
                    {
                        "Area": "Ticker",
                        "What changed": "case / whitespace / broker suffix normalisation",
                        "Rows affected": int(clean_report.get("tickers_normalised", 0)),
                    },
                    {
                        "Area": "Action",
                        "What changed": "financial action aliases normalised",
                        "Rows affected": int(clean_report.get("actions_normalised", 0)),
                    },
                    {
                        "Area": "Date",
                        "What changed": "mixed date representations parsed",
                        "Rows affected": int(clean_report.get("dates_reparsed", 0)),
                    },
                    {
                        "Area": "Numbers",
                        "What changed": "shares / prices / fees coerced from text",
                        "Rows affected": int(clean_report.get("numbers_coerced", 0)),
                    },
                    {
                        "Area": "Transaction ID",
                        "What changed": "repeated IDs quarantined for audit",
                        "Rows affected": int(clean_report.get("hard_duplicate_rows", 0)),
                    },
                ])

                with st.expander("What was fixed or flagged"):
                    themed_dataframe(
                        change_summary,
                        use_container_width=True,
                        hide_index=True,
                    )

                    duplicate_ids = clean_report.get(
                        "duplicate_transaction_ids",
                        [],
                    )
                    if duplicate_ids:
                        st.caption(
                            "Duplicate transaction IDs quarantined: "
                            + ", ".join(map(str, duplicate_ids))
                        )

                detected_columns = clean_report.get(
                    "detected_columns",
                    {},
                )
                if detected_columns:
                    detected_table = pd.DataFrame([
                        {
                            "Financial role": role,
                            "Detected source column": column,
                        }
                        for role, column in detected_columns.items()
                        if column is not None
                    ])
                    with st.expander("Columns used by the cleaning engine"):
                        themed_dataframe(
                            detected_table,
                            use_container_width=True,
                            hide_index=True,
                        )

                accepted_rows_table = clean_report.get(
                    "accepted_position_rows",
                    pd.DataFrame(),
                )
                review_rows_table = clean_report.get(
                    "review_rows_table",
                    pd.DataFrame(),
                )
                rejected_rows_table = clean_report.get(
                    "rejected_rows_table",
                    pd.DataFrame(),
                )
                audit_table = clean_report.get(
                    "audit_table",
                    pd.DataFrame(),
                )
                price_table = clean_report.get(
                    "price_table",
                    pd.DataFrame(),
                )

                tab_clean, tab_review, tab_rejected, tab_audit, tab_price = st.tabs([
                    f"✅ Cleaned Position Rows ({len(accepted_rows_table)})",
                    f"🚩 Review ({len(review_rows_table)})",
                    f"⛔ Rejected ({len(rejected_rows_table)})",
                    f"🧾 Full Audit ({len(audit_table)})",
                    f"📈 Derived Price Table ({len(price_table)})",
                ])

                with tab_clean:
                    st.caption(
                        "Rows the cleaner considers usable for position analysis. "
                        "Warnings remain visible in the audit fields."
                    )
                    themed_dataframe(
                        dataframe_for_display(accepted_rows_table),
                        use_container_width=True,
                        hide_index=True,
                    )

                with tab_review:
                    st.caption(
                        "Rows whose financial meaning is recognised but should not "
                        "be guessed automatically, such as ambiguous transfers or "
                        "corporate actions."
                    )
                    if review_rows_table.empty:
                        st.caption("Nothing requires semantic review.")
                    else:
                        themed_dataframe(
                            dataframe_for_display(review_rows_table),
                            use_container_width=True,
                            hide_index=True,
                        )

                with tab_rejected:
                    st.caption(
                        "Rows excluded from automatic position analysis with an "
                        "explicit reason. They remain fully auditable."
                    )
                    if rejected_rows_table.empty:
                        st.caption("Nothing was rejected.")
                    else:
                        themed_dataframe(
                            dataframe_for_display(rejected_rows_table),
                            use_container_width=True,
                            hide_index=True,
                        )

                with tab_audit:
                    st.caption(
                        "One audit record per source row. Original values, cleaned "
                        "values, decision, reason, warnings and financial checks are "
                        "kept together so no row can disappear silently."
                    )
                    themed_dataframe(
                        dataframe_for_display(audit_table),
                        use_container_width=True,
                        hide_index=True,
                    )

                with tab_price:
                    st.caption(
                        "Deterministic cleaned position table produced from accepted "
                        "position-affecting events. This is a cleaning diagnostic, "
                        "not a replacement for the valuation engine."
                    )
                    themed_dataframe(
                        price_table,
                        use_container_width=True,
                        hide_index=True,
                    )

                # --------------------------------------------------------------
                # Downloadable audit outputs. This mirrors the useful standalone
                # cleaner interaction without replacing the portfolio dashboard.
                # --------------------------------------------------------------
                with st.expander("Download Cleaning Evidence"):
                    audit_csv = audit_table.to_csv(index=False).encode("utf-8")
                    price_csv = price_table.to_csv(index=False).encode("utf-8")

                    d1, d2 = st.columns(2)
                    with d1:
                        st.download_button(
                            "Download full cleaning audit (.csv)",
                            data=audit_csv,
                            file_name="portfolio_cleaning_audit.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
                    with d2:
                        st.download_button(
                            "Download derived price table (.csv)",
                            data=price_csv,
                            file_name="cleaned_price_table.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

                # --------------------------------------------------------------
                # Explicit state transition: preview -> accepted accounting state.
                # --------------------------------------------------------------
                if report_is_preview:
                    c_accept, c_discard = st.columns(2)

                    with c_accept:
                        accept_cleaning = st.button(
                            "Accept Cleaned Data & Update Analysis",
                            key="accept_auto_clean_candidate",
                            type="primary",
                            use_container_width=True,
                        )

                    with c_discard:
                        discard_cleaning = st.button(
                            "Discard Cleaning Preview",
                            key="discard_auto_clean_candidate",
                            use_container_width=True,
                        )

                    if discard_cleaning:
                        st.session_state.pop(
                            "auto_clean_candidate_transactions",
                            None,
                        )
                        st.session_state.pop(
                            "auto_clean_candidate_cashflows",
                            None,
                        )
                        st.session_state.pop(
                            "auto_clean_candidate_report",
                            None,
                        )
                        st.session_state.auto_clean_candidate_ready = False
                        st.rerun()

                    if accept_cleaning:
                        candidate_transactions = st.session_state.get(
                            "auto_clean_candidate_transactions",
                            pd.DataFrame(),
                        )
                        candidate_cashflows = st.session_state.get(
                            "auto_clean_candidate_cashflows"
                        )
                        candidate_report = st.session_state.get(
                            "auto_clean_candidate_report",
                            {},
                        )

                        apply_ledger_transactions(
                            candidate_transactions,
                            candidate_cashflows,
                        )

                        st.session_state.auto_clean_report = candidate_report
                        st.session_state.auto_clean_applied = True
                        st.session_state.auto_clean_candidate_ready = False

                        st.session_state.pop(
                            "auto_clean_candidate_transactions",
                            None,
                        )
                        st.session_state.pop(
                            "auto_clean_candidate_cashflows",
                            None,
                        )
                        st.session_state.pop(
                            "auto_clean_candidate_report",
                            None,
                        )

                        st.rerun()

                else:
                    # Full revert means return to the upload-derived state, not an
                    # inverse transformation. That keeps the operation deterministic.
                    if st.button(
                        "Revert Cleaning to Original Upload",
                        key="revert_auto_clean",
                        use_container_width=True,
                    ):
                        original_transactions = (
                            st.session_state.original_transactions_raw.copy()
                        )

                        original_cashflows = st.session_state.get(
                            "original_cashflows_raw"
                        )
                        canonical_original_cashflows = (
                            extract_cash_events(original_cashflows)
                            if original_cashflows is not None
                            else None
                        )

                        apply_ledger_transactions(
                            original_transactions,
                            canonical_original_cashflows,
                        )

                        st.session_state.auto_clean_applied = False
                        st.session_state.pop("auto_clean_report", None)
                        st.rerun()


#______________________________________________________________________________
# OPTION 2 — MANUAL ENTRY
#______________________________________________________________________________

elif input_method == "Enter Manually":

    st.write(
        "Enter your current holdings directly below."
    )

    st.caption(
        "Ticker, Quantity and Price are available by default. Add optional "
        "financial columns when you have them; missing optional fields do not "
        "prevent the portfolio from being parsed."
    )

    optional_column_choices = [
        "Date",
        "Time",
        "Action",
        "Fees",
        "Gross Value",
        "Transaction ID",
        "Account",
        "Notes",
        "Market Value",
        "Cost Basis",
    ]

    selected_optional_columns = st.multiselect(
        "Add more columns",
        optional_column_choices,
        default=st.session_state.manual_optional_columns,
        key="manual_optional_columns_selector",
    )

    draft = st.session_state.manual_portfolio_draft.copy()
    required_manual_columns = ["Ticker", "Quantity", "Price"]

    for column in required_manual_columns + selected_optional_columns:
        if column not in draft.columns:
            draft[column] = ""

    keep_columns = required_manual_columns + selected_optional_columns
    draft = draft[[column for column in keep_columns if column in draft.columns]]
    st.session_state.manual_optional_columns = selected_optional_columns
    st.session_state.manual_portfolio_draft = draft

    manual_portfolio = st.data_editor(
        draft,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ticker": st.column_config.TextColumn(
                "Ticker / Security",
                help="Example: AAPL, Apple, BTC, NVDA",
            ),
            "Quantity": st.column_config.TextColumn(
                "Quantity",
                help="Positive = long, negative = short",
            ),
            "Price": st.column_config.TextColumn(
                "Price",
                help="Optional sheet price. Leave blank to use market data later.",
            ),
        },
        key="manual_portfolio_editor",
    )

    if st.button(
        "Validate Portfolio",
        type="primary",
    ):
        manual_portfolio = manual_portfolio[
            ~(
                manual_portfolio["Ticker"]
                .fillna("")
                .astype(str)
                .str.strip()
                .eq("")
                &
                manual_portfolio["Quantity"]
                .fillna("")
                .astype(str)
                .str.strip()
                .eq("")
            )
        ].reset_index(drop=True)

        if manual_portfolio.empty:
            st.error(
                "Enter at least one holding."
            )
            st.stop()

        valid, review = validate_holdings(
            manual_portfolio
        )

        st.session_state.manual_portfolio_draft = (
            manual_portfolio.copy()
        )

        st.session_state.portfolio_signature = (
            "manual:portfolio"
        )

        st.session_state.parsed_input = {
            "mode": "holdings",
            "holdings": manual_portfolio.copy(),
            "transactions": None,
            "cashflows": None,
            "frozen_prices": None,
            "parsing_report": None,
            "market_data_start": (
                pd.Timestamp.today().normalize() - pd.DateOffset(years=5)
            ),
        }

        st.session_state.working_holdings = (
            manual_portfolio.copy()
        )

        st.session_state.valid_holdings = valid
        st.session_state.review_holdings = review
        reset_review_state()

        st.rerun()


#______________________________________________________________________________
# STOP UNTIL INPUT HAS BEEN VALIDATED
#______________________________________________________________________________

if "parsed_input" not in st.session_state:
    st.stop()

parsed = st.session_state.parsed_input
mode = parsed["mode"]

detected_labels = {
    "ledger": "Detected transaction ledger.",
    "holdings": "Detected current holdings snapshot.",
    "market_data": "Detected historical market-price data.",
    "cashflows": "Detected cashflow ledger.",
}
st.success(detected_labels.get(mode, "Financial dataset detected."))

parsing_report = parsed.get("parsing_report")

if parsing_report:
    with st.expander("Parsing Summary", expanded=False):
        classification = parsing_report.get("classification", {})
        st.caption(
            "The parser identifies what each column most likely represents before "
            "cleaning or accounting begins. Match confidence is retained for audit."
        )

        schema_table = parsing_report.get("schema_table")
        if schema_table is not None and not schema_table.empty:
            themed_dataframe(schema_table, use_container_width=True, hide_index=True)

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric(
                "Transaction score",
                int(classification.get("transaction_score", 0)),
            )
        with col_b:
            st.metric(
                "Holdings score",
                int(classification.get("holdings_score", 0)),
            )
        with col_c:
            st.metric(
                "Market-data score",
                int(classification.get("market_data_score", 0)),
            )

        date_profile = parsing_report.get("date_profile", {})
        if date_profile.get("present"):
            st.write(
                f"Date field: **{date_profile.get('column')}** | "
                f"Detected pattern: **{date_profile.get('suggested_format') or 'standard / mixed text'}**"
            )

            if date_profile.get("requires_user_choice"):
                choice = st.radio(
                    "Some dates are ambiguous. Which format represents most of this file?",
                    ["DD/MM/YYYY", "MM/DD/YYYY"],
                    horizontal=True,
                    key="date_format_preference",
                )
                st.caption(
                    f"Stored parsing preference: {choice}. The cleaning stage will use "
                    "this preference only for genuinely ambiguous numeric dates."
                )

        if mode == "holdings" and parsed.get("market_data_start") is not None:
            st.write(
                "Historical market-data lookback starts at "
                f"**{pd.Timestamp(parsed['market_data_start']).date()}**. "
                "This is a price-history start date, not an assumed purchase date."
            )

        for warning in parsing_report.get("warnings", []):
            st.warning(warning)


#______________________________________________________________________________
# REVIEW / EDIT COMPLETE UPLOADED HOLDINGS SHEET
#______________________________________________________________________________

if mode == "holdings" and st.session_state.get("portfolio_signature", "").startswith("upload:"):
    st.markdown("### Uploaded Portfolio")
    st.caption(
        "Your complete uploaded CSV/Excel sheet is preserved here. Review or edit "
        "the whole sheet; changes do not affect analysis until you click Accept Changes."
    )

    if not st.session_state.get("holdings_sheet_edit_mode", False):
        if st.button(
            "Review / Edit Uploaded Sheet",
            key="open_holdings_sheet_editor",
            type="secondary",
        ):
            st.session_state.holdings_sheet_edit_mode = True
            st.rerun()
    else:
        full_sheet = st.session_state.get(
            "original_holdings_raw",
            parsed.get("holdings", pd.DataFrame()),
        ).copy()

        editor_version = int(st.session_state.get("holdings_sheet_editor_version", 0))
        edited_full_sheet = st.data_editor(
            full_sheet,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"full_holdings_sheet_editor_{editor_version}",
        )

        accept_col, cancel_col = st.columns(2)
        with accept_col:
            accept_sheet_changes = st.button(
                "Accept Changes & Update Analysis",
                key="accept_full_holdings_sheet_changes",
                type="primary",
                use_container_width=True,
            )
        with cancel_col:
            cancel_sheet_changes = st.button(
                "Cancel",
                key="cancel_full_holdings_sheet_changes",
                use_container_width=True,
            )

        if cancel_sheet_changes:
            st.session_state.holdings_sheet_edit_mode = False
            st.session_state.holdings_sheet_editor_version = editor_version + 1
            st.rerun()

        if accept_sheet_changes:
            try:
                refreshed_working = prepare_holdings_for_review(edited_full_sheet)
                refreshed_valid, refreshed_review = validate_holdings(refreshed_working)
            except Exception as exc:
                st.error(f"The edited sheet could not be parsed: {exc}")
            else:
                # Explicit acceptance is the only point at which the edited
                # source sheet replaces the active holdings input.
                st.session_state.original_holdings_raw = edited_full_sheet.copy()
                st.session_state.parsed_input["holdings"] = edited_full_sheet.copy()
                st.session_state.working_holdings = refreshed_working
                st.session_state.valid_holdings = refreshed_valid
                st.session_state.review_holdings = refreshed_review
                reset_review_state()
                st.session_state.holdings_sheet_edit_mode = False
                st.session_state.holdings_sheet_editor_version = editor_version + 1
                st.rerun()


#______________________________________________________________________________
# HISTORICAL MARKET-DATA INPUT + QUANT ANALYTICS
#______________________________________________________________________________

if mode == "market_data":
    market_frame = parsed.get("market_data")
    schema = (parsing_report or {}).get("schema", {})

    st.subheader("Historical Market Data")
    st.caption(
        "This file contains market-price history rather than transactions. "
        "It uses a separate analytics path and never enters cash/position accounting."
    )

    if parsed.get("inferred_ticker"):
        st.info(
            f"No Ticker column was present. Security inferred from the source name: "
            f"**{parsed['inferred_ticker']}**."
        )

    try:
        market = standardise_market_data(
            market_frame,
            schema,
            inferred_ticker=parsed.get("inferred_ticker"),
        )
    except Exception as exc:
        st.error(f"The market-data schema was recognised, but it could not be prepared for analytics: {exc}")
        st.stop()

    metric_a, metric_b, metric_c, metric_d = st.columns(4)
    with metric_a:
        st.metric("Valid observations", f"{len(market.observations):,}")
    with metric_b:
        st.metric("Securities", f"{market.prices.shape[1]:,}")
    with metric_c:
        if not market.prices.empty:
            st.metric("Date range", f"{market.prices.index.min().date()} → {market.prices.index.max().date()}")
        else:
            st.metric("Date range", "Unavailable")
    with metric_d:
        st.metric("Price field", market.price_field.replace("_", " ").title())

    if not market.issues.empty:
        with st.expander(f"Data-quality review ({len(market.issues):,} flagged observations)"):
            themed_dataframe(dataframe_for_display(market.issues), use_container_width=True, hide_index=True)

    if market.prices.empty or market.returns.empty:
        st.error("There are not enough valid price observations to calculate returns.")
        st.stop()

    st.markdown("### Analysis settings")
    set_a, set_b = st.columns(2)
    with set_a:
        risk_free_pct = st.number_input(
            "Annual risk-free rate (%)", min_value=-20.0, max_value=50.0,
            value=0.0, step=0.25, key="market_risk_free_rate"
        )
    with set_b:
        var_confidence_pct = st.selectbox(
            "Historical VaR confidence", [90, 95, 99], index=1, key="market_var_confidence"
        )
    risk_free_rate = float(risk_free_pct) / 100.0
    var_level = float(var_confidence_pct) / 100.0

    st.markdown("### Asset analysis")
    asset_summary = asset_metrics(market.returns, risk_free_rate=risk_free_rate, var_level=var_level)
    if not asset_summary.empty:
        display_assets = asset_summary.copy()
        for col in ["annual_return", "annual_volatility", "var", "expected_shortfall", "max_drawdown"]:
            display_assets[col] = display_assets[col] * 100.0
        display_assets = display_assets.rename(columns={
            "observations": "Return Obs.",
            "annual_return": "Annual Return %",
            "annual_volatility": "Annual Volatility %",
            "sharpe": "Sharpe",
            "var": f"Daily VaR {var_confidence_pct}%",
            "expected_shortfall": "Daily Expected Shortfall %",
            "max_drawdown": "Max Drawdown %",
        })
        themed_dataframe(display_assets.round(3), use_container_width=True, hide_index=True)

    # Single-security price history is analysed directly as an asset. Multi-asset
    # history additionally exposes a portfolio layer with user-controlled weights.
    if market.prices.shape[1] == 1:
        ticker = str(market.prices.columns[0])
        metrics = asset_summary.iloc[0].to_dict()
        st.markdown(f"### {ticker} risk summary")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Annual return", f"{metrics['annual_return']:.2%}")
        c2.metric("Annual volatility", f"{metrics['annual_volatility']:.2%}")
        c3.metric("Sharpe", f"{metrics['sharpe']:.2f}" if np.isfinite(metrics['sharpe']) else "N/A")
        c4.metric(f"Daily VaR {var_confidence_pct}%", f"{metrics['var']:.2%}")
        c5.metric("Expected shortfall", f"{metrics['expected_shortfall']:.2%}")

        price_series = market.prices[ticker].rename("Price")
        plot_scaled_series(
            price_series,
            title=f"{ticker} price history",
            y_label="Adjusted price",
            anchor_zero=True,
        )

        returns_series = market.returns[ticker].dropna()
        wealth = (1.0 + returns_series).cumprod().rename("Growth of £1")
        plot_scaled_series(
            wealth,
            title=f"{ticker} growth of £1",
            y_label="Value of £1",
            anchor_zero=True,
        )

        st.info(
            "This is a single market series, so VaR, expected shortfall, volatility, "
            "Sharpe and drawdown are asset-level measures. Upload multiple tickers to "
            "activate portfolio weights, correlation, risk contribution and optimisation."
        )
        st.stop()

    st.markdown("### Build a portfolio from this market dataset")
    tickers = list(market.prices.columns)
    default_weight = 1.0 / len(tickers)
    weights_source = pd.DataFrame({"Ticker": tickers, "Weight %": [default_weight * 100.0] * len(tickers)})
    weights_editor = st.data_editor(
        weights_source,
        hide_index=True,
        use_container_width=True,
        disabled=["Ticker"],
        column_config={
            "Weight %": st.column_config.NumberColumn("Weight %", min_value=0.0, max_value=100.0, step=0.5),
        },
        key="market_portfolio_weights",
    )
    raw_weights = pd.Series(
        pd.to_numeric(weights_editor["Weight %"], errors="coerce").fillna(0.0).to_numpy() / 100.0,
        index=weights_editor["Ticker"].astype(str).tolist(),
    )
    if raw_weights.sum() <= 0:
        st.error("Portfolio weights must sum to more than 0%.")
        st.stop()
    weights = raw_weights / raw_weights.sum()
    st.caption(f"Entered weights sum to {raw_weights.sum():.2%}. Analytics uses normalised weights summing to 100%.")

    p_metrics = market_portfolio_metrics(
        market.returns, weights, risk_free_rate=risk_free_rate, var_level=var_level
    )
    p_returns = market_portfolio_returns(market.returns, weights)

    st.markdown("### Portfolio risk summary")
    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric("Annual return", f"{p_metrics['annual_return']:.2%}")
    p2.metric("Annual volatility", f"{p_metrics['annual_volatility']:.2%}")
    p3.metric("Sharpe", f"{p_metrics['sharpe']:.2f}" if np.isfinite(p_metrics['sharpe']) else "N/A")
    p4.metric(f"Daily VaR {var_confidence_pct}%", f"{p_metrics['var']:.2%}")
    p5.metric("Expected shortfall", f"{p_metrics['expected_shortfall']:.2%}")
    p6.metric("Max drawdown", f"{p_metrics['max_drawdown']:.2%}")

    if not p_returns.empty:
        plot_growth_and_drawdown(p_returns)

    complete_returns = market.returns.loc[:, weights[weights > 0].index]
    plot_correlation_heatmap(complete_returns)

    st.markdown("### Risk contribution")
    risk_table = market_risk_contribution(market.returns, weights)
    if not risk_table.empty:
        plot_risk_contribution(risk_table)
    else:
        st.info("Not enough overlapping return history to calculate risk contribution.")

    render_on_demand_monte_carlo(
        market.returns,
        risk_free_rate=risk_free_rate,
        tickers=tickers,
        key_prefix="market_data",
    )

    st.stop()


#______________________________________________________________________________
# SECURITY & INPUT REVIEW — HOLDINGS
#______________________________________________________________________________

if mode == "holdings":

    review = st.session_state.get(
        "review_holdings",
        pd.DataFrame(),
    )

    if not review.empty:

        unresolved = [
            str(index)
            for index in review.index
            if str(index)
            not in st.session_state.accepted_review_holdings
        ]

        review_label = (
            f"Security & Input Review — {len(unresolved)} item(s) require attention"
            if unresolved
            else "Security & Input Review — all items resolved"
        )

        # Keep this workflow compact when the file is clean, but make genuine
        # input problems impossible to miss.  A bad ticker/company name or an
        # invalid quantity therefore opens the review automatically.
        with st.expander(
            review_label,
            expanded=bool(unresolved),
        ):
            st.caption(
                "Every security is checked before it reaches accounting. "
                "Company names, ticker typos, ambiguous matches and invalid "
                "quantities stay here until you accept a correction or leave "
                "the row excluded."
            )

            if st.session_state.accepted_review_holdings:
                if st.button(
                    "Revert accepted holding corrections",
                    key="revert_holding_review_corrections",
                ):
                    st.session_state.accepted_review_holdings = {}
                    st.session_state.pending_review_holdings = {}
                    st.session_state.review_matches = {}
                    st.session_state.review_confirmed_matches = {}
                    st.rerun()

            for index, row in review.iterrows():

                review_key = str(index)

                if review_key in st.session_state.accepted_review_holdings:
                    continue

                st.divider()

                original_security = str(
                    row.get("Input", "")
                ).strip()

                original_quantity = str(
                    row.get("Original Quantity", "")
                ).strip()

                status_text = str(row.get("Status", "")).strip()
                st.markdown(
                    f"**Original entry:** `{original_security or '(blank)'}` "
                    f"· Quantity: `{original_quantity or '(blank)'}`"
                )
                if status_text:
                    st.warning(status_text)

                source_record = row.get("Source Record")
                if isinstance(source_record, dict) and source_record:
                    st.caption("Extracted source row")
                    themed_dataframe(
                        dataframe_for_display(pd.DataFrame([source_record])),
                        use_container_width=True,
                        hide_index=True,
                    )

                col1, col2 = st.columns([2, 1])

                with col1:
                    corrected_security = st.text_input(
                        "Security / ticker",
                        value=original_security,
                        key=f"review_security_{index}",
                        help=(
                            "Enter an exact ticker such as AAPL, or at least one complete "
                            "word from the company/security name, such as Vanguard. "
                            "Short fragments such as 'van' are not matched."
                        ),
                    ).strip()

                with col2:
                    corrected_quantity = st.text_input(
                        "Quantity",
                        value=original_quantity,
                        key=f"review_quantity_{index}",
                    ).strip()

                if st.button(
                    "Check Match",
                    key=f"check_match_{index}",
                ):
                    candidates = resolve_security_candidates(
                        corrected_security
                    )

                    st.session_state.review_matches[review_key] = {
                        "Query": corrected_security,
                        "Candidates": candidates,
                    }
                    st.session_state.review_confirmed_matches.pop(review_key, None)

                    st.rerun()

                search_result = st.session_state.review_matches.get(
                    review_key
                )

                candidates = []
                if isinstance(search_result, dict):
                    candidates = search_result.get("Candidates", []) or []

                # Preserve the initial parser suggestion before the user runs
                # an explicit search.
                if not candidates:
                    initial_ticker = row.get("Ticker")
                    if value_exists(initial_ticker):
                        candidates = [{
                            "Ticker": initial_ticker,
                            "Asset Name": row.get("Asset Name"),
                            "Asset Type": row.get("Asset Type"),
                            "Exchange": row.get("Exchange"),
                            "Match Status": row.get("Match Status"),
                            "Exact Ticker Match": (
                                str(row.get("Match Status", "")) == "Valid ticker"
                            ),
                        }]

                current_match = st.session_state.review_confirmed_matches.get(
                    review_key
                )
                selected_candidate = None

                if candidates:
                    st.caption(
                        "Yahoo Finance found the following possible match. "
                        "Confirm it only if it is the security you intended."
                    )
                    options = list(range(len(candidates)))

                    def _candidate_label(i):
                        candidate = candidates[i]
                        ticker = candidate.get("Ticker") or "?"
                        name = candidate.get("Asset Name") or ticker
                        exchange = candidate.get("Exchange")
                        asset_type = candidate.get("Asset Type")
                        details = " · ".join(
                            str(value)
                            for value in (exchange, asset_type)
                            if value_exists(value)
                        )
                        return (
                            f"{ticker} — {name}"
                            + (f" ({details})" if details else "")
                        )

                    selected_index = st.selectbox(
                        "Approximate match",
                        options=options,
                        format_func=_candidate_label,
                        key=f"candidate_select_{index}",
                    )
                    selected_candidate = candidates[selected_index]

                    yes_col, no_col = st.columns([1, 1])
                    with yes_col:
                        if st.button(
                            "Yes — this is my security",
                            key=f"confirm_match_{index}",
                            type="primary",
                            use_container_width=True,
                        ):
                            st.session_state.review_confirmed_matches[review_key] = (
                                selected_candidate
                            )
                            st.rerun()
                    with no_col:
                        if st.button(
                            "No — search another",
                            key=f"reject_match_{index}",
                            use_container_width=True,
                        ):
                            st.session_state.review_confirmed_matches.pop(review_key, None)
                            st.session_state.review_matches.pop(review_key, None)
                            st.rerun()

                if (
                    isinstance(current_match, dict)
                    and value_exists(current_match.get("Ticker"))
                ):
                    ticker = str(current_match["Ticker"])
                    asset_name = current_match.get("Asset Name") or ticker
                    st.success(f"Confirmed: {ticker} — {asset_name}")
                elif candidates:
                    st.info(
                        "Confirm the Yahoo Finance match above, or choose 'No — search another'."
                    )
                else:
                    st.error(
                        "No security match has been confirmed. Edit the security "
                        "and click 'Check Match'."
                    )

                try:
                    quantity_number = float(corrected_quantity)
                    quantity_valid = (
                        math.isfinite(quantity_number)
                        and quantity_number != 0
                    )
                except Exception:
                    quantity_number = None
                    quantity_valid = False

                if not quantity_valid:
                    st.error(
                        f"Quantity '{corrected_quantity}' is not a valid non-zero number."
                    )

                can_accept = (
                    isinstance(current_match, dict)
                    and value_exists(current_match.get("Ticker"))
                    and quantity_valid
                )

                action_col, note_col = st.columns([1, 3])
                with action_col:
                    stage_clicked = st.button(
                        "Use This Correction",
                        key=f"stage_holding_{index}",
                        type="primary",
                        disabled=not can_accept,
                        use_container_width=True,
                    )
                with note_col:
                    staged = review_key in st.session_state.pending_review_holdings
                    if staged:
                        st.success("Correction staged. Click 'Accept Changes & Update Analysis' below to recalculate.")
                    else:
                        st.caption(
                            "Until you accept the changes below, this row remains excluded from analysis."
                        )

                if stage_clicked:
                    ticker = str(current_match["Ticker"])
                    st.session_state.pending_review_holdings[review_key] = {
                        "Ticker": ticker,
                        "Asset Name": current_match.get("Asset Name") or ticker,
                        "Quantity": quantity_number,
                        "Provided Price": row.get("Provided Price", float("nan")),
                        "Provided Entry Price": row.get(
                            "Provided Entry Price",
                            float("nan"),
                        ),
                    }
                    st.rerun()

            # One explicit commit point: unresolved rows remain excluded from the
            # current calculation. Only this button adds staged corrections and
            # triggers a fresh portfolio calculation.
            pending = st.session_state.get("pending_review_holdings", {})
            if pending:
                st.divider()
                st.info(
                    f"{len(pending)} correction(s) are ready. Current results above/below still exclude "
                    "those rows until you update the analysis."
                )
                if st.button(
                    "Accept Changes & Update Analysis",
                    key="commit_holding_review_changes",
                    type="primary",
                    use_container_width=True,
                ):
                    st.session_state.accepted_review_holdings.update(pending)
                    st.session_state.pending_review_holdings = {}
                    st.rerun()

#______________________________________________________________________________
# DATA QUALITY REPORT
#______________________________________________________________________________

if mode == "ledger":

    quality_report = st.session_state.get(
        "data_quality_report",
        {},
    )

    if quality_report:

        with st.expander("Data Quality Report", expanded=False):
            q1, q2, q3, q4 = st.columns(4)

            q1.metric(
                "Rows Read",
                quality_report.get(
                    "Rows Read",
                    0,
                ),
            )

            q2.metric(
                "Valid Transactions",
                quality_report.get(
                    "Valid Transactions",
                    0,
                ),
            )

            q3.metric(
                "Incomplete",
                quality_report.get(
                    "Incomplete Transactions",
                    0,
                ),
            )

            q4.metric(
                "Potential Duplicates",
                quality_report.get(
                    "Potential Duplicate Rows",
                    0,
                ),
            )

            q5, q6, q7, q8 = st.columns(4)

            q5.metric(
                "Invalid Dates",
                quality_report.get(
                    "Invalid Dates",
                    0,
                ),
            )

            q6.metric(
                "Invalid Quantities",
                quality_report.get(
                    "Invalid Quantities",
                    0,
                ),
            )

            q7.metric(
                "Invalid Prices",
                quality_report.get(
                    "Invalid Prices",
                    0,
                ),
            )

            q8.metric(
                "Unrecognised Actions",
                quality_report.get(
                    "Unrecognised Actions",
                    0,
                ),
            )

            st.caption(
                "Duplicate rows are flagged for review, not automatically deleted."
            )

            duplicate_rows = st.session_state.get(
                "duplicate_rows",
                pd.DataFrame(),
            )

            if not duplicate_rows.empty:

                with st.expander(
                    "Potential Duplicate Transactions"
                ):
                    themed_dataframe(
                        duplicate_rows,
                        use_container_width=True,
                        hide_index=True,
                    )

#______________________________________________________________________________
# EDIT / REPAIR / ADD TRANSACTIONS
#______________________________________________________________________________

if mode == "ledger":

    with st.expander(
        "Review, Repair or Add Transactions",
        expanded=False,
    ):

        st.write(
            "The accounting below continues to use the last accepted clean ledger. "
            "You can edit any row, add new transactions, or untick rows you want "
            "excluded. Nothing changes until you accept the draft."
        )

        editor_source = st.session_state.get(
            "ledger_editor_source",
            pd.DataFrame(),
        )

        if not editor_source.empty:

            disabled_columns = []

            if (
                "Potential Duplicate"
                in editor_source.columns
            ):
                disabled_columns.append(
                    "Potential Duplicate"
                )

            edited_ledger = st.data_editor(
                editor_source,
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                disabled=disabled_columns,
                column_config={
                    "Include in Analysis": st.column_config.CheckboxColumn(
                        "Include",
                        help=(
                            "Tick to include this transaction in the portfolio "
                            "when changes are accepted."
                        ),
                    ),
                    "Potential Duplicate": st.column_config.CheckboxColumn(
                        "Potential Duplicate",
                        disabled=True,
                    ),
                },
                key=(
                    "ledger_repair_editor_"
                    + str(
                        st.session_state.get(
                            "ledger_revision",
                            0,
                        )
                    )
                ),
            )

            c_accept, c_reset = st.columns(
                [1, 1]
            )

            with c_accept:

                accept_changes = st.button(
                    "Accept Changes & Update Analysis",
                    type="primary",
                    use_container_width=True,
                )

            with c_reset:

                reset_draft = st.button(
                    "Reset Draft to Last Accepted",
                    use_container_width=True,
                )

            if reset_draft:

                accepted = st.session_state.get(
                    "accepted_transactions",
                    pd.DataFrame(),
                ).copy()

                accepted = (
                    flag_potential_duplicates(
                        accepted
                    )
                )

                accepted[
                    "Include in Analysis"
                ] = True

                st.session_state.ledger_editor_source = (
                    accepted.reset_index(
                        drop=True
                    )
                )

                st.session_state.ledger_revision = (
                    int(
                        st.session_state.get(
                            "ledger_revision",
                            0,
                        )
                    )
                    + 1
                )

                st.rerun()

            if accept_changes:

                draft = edited_ledger.copy()

                include_column = (
                    "Include in Analysis"
                )

                if include_column not in draft.columns:
                    draft[
                        include_column
                    ] = True

                included_rows = draft[
                    draft[
                        include_column
                    ].fillna(False)
                ].copy()

                control_columns = [
                    "Include in Analysis",
                    "Potential Duplicate",
                ]

                included_for_cleaning = (
                    included_rows.drop(
                        columns=[
                            column
                            for column in control_columns
                            if column
                            in included_rows.columns
                        ],
                        errors="ignore",
                    )
                )

                all_for_reporting = (
                    draft.drop(
                        columns=[
                            column
                            for column in control_columns
                            if column
                            in draft.columns
                        ],
                        errors="ignore",
                    )
                )

                repaired_transactions, remaining_issues = (
                    clean_transaction_ledger(
                        included_for_cleaning
                    )
                )

                edited_ledger_cash_events = (
                    extract_cash_events(
                        included_for_cleaning
                    )
                )

                original_cashflows = (
                    st.session_state.get(
                        "original_cashflows_raw"
                    )
                )

                external_cash_events = (
                    extract_cash_events(
                        original_cashflows
                    )
                    if original_cashflows is not None
                    else None
                )

                refreshed_cashflows = (
                    combine_cash_events(
                        external_cash_events,
                        edited_ledger_cash_events,
                    )
                )

                refreshed_report = (
                    build_data_quality_report(
                        all_for_reporting,
                        repaired_transactions,
                        remaining_issues,
                    )
                )

                refreshed_flagged = (
                    flag_potential_duplicates(
                        all_for_reporting
                    )
                )

                refreshed_flagged[
                    "Include in Analysis"
                ] = draft[
                    include_column
                ].fillna(False).values

                refreshed_duplicates = (
                    refreshed_flagged[
                        refreshed_flagged[
                            "Potential Duplicate"
                        ].eq(True)
                    ].copy()
                )

                st.session_state.accepted_transactions = (
                    repaired_transactions.copy()
                )

                st.session_state.active_transactions = (
                    repaired_transactions.copy()
                )

                st.session_state.parsed_input[
                    "transactions"
                ] = repaired_transactions.copy()

                st.session_state.accepted_cashflows = (
                    refreshed_cashflows.copy()
                    if refreshed_cashflows is not None
                    else None
                )

                st.session_state.parsed_input[
                    "transactions"
                ] = repaired_transactions.copy()

                st.session_state.parsed_input[
                    "cashflows"
                ] = (
                    refreshed_cashflows.copy()
                    if refreshed_cashflows is not None
                    else None
                )

                st.session_state.ledger_revision = (
                    int(
                        st.session_state.get(
                            "ledger_revision",
                            0,
                        )
                    )
                    + 1
                )

                st.session_state.ledger_editor_source = (
                    refreshed_flagged.reset_index(
                        drop=True
                    )
                )

                st.session_state.ledger_issues = (
                    remaining_issues
                )

                st.session_state.data_quality_report = (
                    refreshed_report
                )

                st.session_state.duplicate_rows = (
                    refreshed_duplicates
                )

                valid_map, ledger_review = (
                    validate_ledger_tickers(
                        repaired_transactions
                    )
                )

                st.session_state.ledger_valid_map = (
                    valid_map
                )

                st.session_state.ledger_review = (
                    ledger_review
                )

                # Keep corrections only for securities still present.
                existing_corrections = (
                    st.session_state.get(
                        "ledger_corrections",
                        {},
                    )
                )

                current_inputs = {
                    item["Input"]
                    for item in ledger_review
                }

                st.session_state.ledger_corrections = {
                    key: value
                    for key, value
                    in existing_corrections.items()
                    if key in current_inputs
                }

                st.success(
                    "Changes accepted. The accounting has been rebuilt "
                    "using the updated clean ledger."
                )

                st.rerun()

            current_issues = st.session_state.get(
                "ledger_issues",
                pd.DataFrame(),
            )

            if not current_issues.empty:
                st.info(
                    f"{len(current_issues)} included row(s) are still incomplete "
                    "and therefore are not part of the accepted accounting ledger."
                )


#______________________________________________________________________________
# SECURITY & INPUT REVIEW — LEDGER
#______________________________________________________________________________

if mode == "ledger":

    ledger_review = st.session_state.get(
        "ledger_review",
        [],
    )

    unresolved_ledger = [
        item
        for item in ledger_review
        if str(item.get("Input", "")).strip()
        not in st.session_state.ledger_corrections
    ]

    if ledger_review:
        review_label = (
            f"Security & Input Review — {len(unresolved_ledger)} security item(s) require attention"
            if unresolved_ledger
            else "Security & Input Review — all security items resolved"
        )

        with st.expander(
            review_label,
            expanded=bool(unresolved_ledger),
        ):
            st.caption(
                "Each unique security is checked once. Exact tickers pass automatically; "
                "company names, typos and ambiguous matches require your confirmation. "
                "Accepted corrections are applied to every matching transaction."
            )

            if st.session_state.ledger_corrections:
                if st.button(
                    "Revert accepted ticker corrections",
                    key="revert_ledger_ticker_corrections",
                ):
                    st.session_state.ledger_corrections = {}
                    st.rerun()

            for index, item in enumerate(ledger_review):

                original = str(item.get("Input", "")).strip()

                if original in st.session_state.ledger_corrections:
                    continue

                st.divider()

                suggested = item.get("Ticker")
                if suggested:
                    st.caption(
                        f"Original: {original} | Initial suggestion: {suggested}"
                    )
                else:
                    st.caption(f"Original: {original}")

                corrected = st.text_input(
                    "Correct security / ticker",
                    value=(str(suggested) if suggested else original),
                    key=f"ledger_security_{index}",
                    help=(
                        "Enter an exact ticker or a complete company/security-name word, then check the "
                        "possible matches before accepting."
                    ),
                ).strip()

                search_key = f"ledger_candidate_search_{index}"
                select_key = f"ledger_candidate_select_{index}"

                if st.button(
                    "Check Match",
                    key=f"ledger_check_match_{index}",
                ):
                    st.session_state[search_key] = resolve_security_candidates(
                        corrected
                    )
                    st.rerun()

                candidates = st.session_state.get(search_key, []) or []

                if not candidates and suggested:
                    candidates = [{
                        "Ticker": suggested,
                        "Asset Name": item.get("Asset Name"),
                        "Asset Type": item.get("Asset Type"),
                        "Exchange": item.get("Exchange"),
                        "Exact Ticker Match": False,
                    }]

                chosen = None
                if candidates:
                    options = list(range(len(candidates)))

                    def _ledger_candidate_label(i):
                        candidate = candidates[i]
                        ticker = candidate.get("Ticker") or "?"
                        name = candidate.get("Asset Name") or ticker
                        exchange = candidate.get("Exchange")
                        asset_type = candidate.get("Asset Type")
                        details = " · ".join(
                            str(value)
                            for value in (exchange, asset_type)
                            if value_exists(value)
                        )
                        return (
                            f"{ticker} — {name}"
                            + (f" ({details})" if details else "")
                        )

                    selected = st.selectbox(
                        "Select match",
                        options=options,
                        format_func=_ledger_candidate_label,
                        key=select_key,
                    )
                    chosen = candidates[selected]
                    ticker = chosen.get("Ticker")
                    if value_exists(ticker):
                        st.success(
                            f"Selected: {ticker} — "
                            f"{chosen.get('Asset Name') or ticker}"
                        )
                else:
                    st.error(
                        "No valid security match found. Edit the entry and click 'Check Match'."
                    )

                accept_disabled = not (
                    isinstance(chosen, dict)
                    and value_exists(chosen.get("Ticker"))
                )

                accept_col, note_col = st.columns([1, 3])
                with accept_col:
                    accept_ticker = st.button(
                        "Accept Correction",
                        key=f"ledger_accept_{index}",
                        disabled=accept_disabled,
                        use_container_width=True,
                    )
                with note_col:
                    st.caption(
                        "Leave unresolved to keep those transactions out of accounting for now."
                    )

                if accept_ticker:
                    st.session_state.ledger_corrections[original] = str(
                        chosen["Ticker"]
                    )
                    st.rerun()

#______________________________________________________________________________
# CHOOSE VALUATION METHOD
#______________________________________________________________________________

frozen_prices = parsed.get(
    "frozen_prices"
)

has_embedded_frozen_prices = (
    frozen_prices is not None
    and not frozen_prices.empty
)

has_holdings_prices = False

if mode == "holdings":
    working_prices = st.session_state.get(
        "working_holdings",
        pd.DataFrame(),
    )

    has_holdings_prices = (
        "Provided Price" in working_prices.columns
        and working_prices["Provided Price"]
        .notna()
        .any()
    )

valuation_options = [
    LIVE_MARKET,
]

if mode == "ledger":
    valuation_options.append(
        HISTORICAL_AS_OF
    )

if (
    has_embedded_frozen_prices
    or has_holdings_prices
):
    valuation_options.append(
        FROZEN_SNAPSHOT
    )

with valuation_method_placeholder.container():
    st.subheader("Valuation Method")

    valuation_mode = st.radio(
        "Choose how open positions should be priced",
        valuation_options,
        horizontal=True,
        help=(
            "Live Market uses current Yahoo Finance prices. "
            "Historical As-Of reconstructs a ledger and prices it on a chosen date. "
            "Frozen Test Snapshot uses only prices stored in the uploaded file and "
            "never falls back to live data."
        ),
    )

    valuation_as_of_date = None

    if valuation_mode == HISTORICAL_AS_OF:
        selected_as_of = st.date_input(
            "Historical as-of date",
            value=pd.Timestamp.today().date(),
            help=(
                "Only ledger events on or before this date are included, and prices "
                "use the latest available market close on or before this date."
            ),
        )

        valuation_as_of_date = pd.Timestamp(
            selected_as_of
        ) + pd.Timedelta(
            hours=23,
            minutes=59,
            seconds=59,
        )

    elif valuation_mode == FROZEN_SNAPSHOT:
        valuation_as_of_date = frozen_snapshot_as_of(
            frozen_prices
        )

        if valuation_as_of_date is not None:
            st.caption(
                "Frozen snapshot date detected: "
                f"{valuation_as_of_date:%Y-%m-%d}. "
                "Ledger reconstruction will stop at this date."
            )
        else:
            st.caption(
                "Frozen prices were detected without a snapshot date. "
                "All accepted ledger transactions will be included."
            )


#______________________________________________________________________________
# BUILD FINAL VALIDATED PORTFOLIO
#______________________________________________________________________________

ledger = None
cash_summary = None

if mode == "holdings":

    portfolio = st.session_state.get(
        "valid_holdings",
        pd.DataFrame(),
    ).copy()

    accepted = st.session_state.get(
        "accepted_review_holdings",
        {},
    )

    if accepted:
        portfolio = pd.concat(
            [
                portfolio,
                pd.DataFrame(
                    list(accepted.values())
                ),
            ],
            ignore_index=True,
        )

    review = st.session_state.get(
        "review_holdings",
        pd.DataFrame(),
    )

    unresolved_count = 0

    if not review.empty:
        unresolved_count = sum(
            str(index)
            not in accepted
            for index in review.index
        )

    if unresolved_count > 0:
        st.info(
            f"{unresolved_count} unresolved holding(s) are excluded from the current calculation. "
            "Correct them in Security & Input Review, then click 'Accept Changes & Update Analysis' to recalculate."
        )

    if portfolio.empty:
        st.error(
            "No validated holdings remain."
        )
        st.stop()

    portfolio = (
        portfolio.groupby(
            "Ticker",
            as_index=False,
        )
        .agg({
            "Asset Name": "last",
            "Quantity": "sum",
            "Provided Price": "last",
            "Provided Entry Price": "last",
        })
    )

    portfolio = portfolio[
        portfolio["Quantity"].ne(0)
    ].copy()

    positions = finalise_holdings(
        portfolio
    )

    cash_balance = float(
        current_cash
    )

else:

    outstanding = [
        item["Input"]
        for item in st.session_state.get(
            "ledger_review",
            [],
        )
        if item["Input"]
        not in st.session_state.get(
            "ledger_corrections",
            {},
        )
    ]

    ticker_map = dict(
        st.session_state.get(
            "ledger_valid_map",
            {},
        )
    )

    ticker_map.update(
        st.session_state.get(
            "ledger_corrections",
            {},
        )
    )

    accepted_transactions = (
        st.session_state.get(
            "active_transactions",
            st.session_state.get(
                "accepted_transactions",
                parsed["transactions"],
            ),
        ).copy()
    )

    analysis_transactions = (
        exclude_unresolved_securities(
            accepted_transactions,
            outstanding,
        )
    )

    if outstanding:
        excluded_count = (
            len(accepted_transactions)
            - len(analysis_transactions)
        )

        st.info(
            f"Analysis is continuing with the validated transactions. "
            f"{excluded_count} transaction row(s) are temporarily excluded "
            "because their security still needs review."
        )

    clean_transactions = apply_ledger_ticker_map(
        analysis_transactions,
        ticker_map,
    )

    clean_transactions = (
        standardise_ledger_for_accounting(
            clean_transactions
        )
    )

    try:
        positions, ledger, cash_summary = (
            build_current_account(
                clean_transactions,
                st.session_state.get(
                    "accepted_cashflows",
                    parsed.get("cashflows"),
                ),
                starting_free_cash=current_cash,
                as_of_date=valuation_as_of_date,
            )
        )

    except Exception as exc:
        st.exception(exc)
        st.stop()

    cash_balance = float(
        cash_summary["Cash Balance"]
    )

    audit_positions, audit_exposure = (
        build_transaction_price_audit(
            clean_transactions,
            positions,
            cash_balance,
            as_of_date=valuation_as_of_date,
        )
    )


#______________________________________________________________________________
# HISTORICAL TRANSACTION-LEDGER PERFORMANCE
#______________________________________________________________________________

if mode == "ledger":
    historical_tx = clean_transactions.copy()

    if not historical_tx.empty and "Date" in historical_tx.columns:
        historical_dates = pd.to_datetime(
            historical_tx["Date"],
            errors="coerce",
        ).dropna()

        if not historical_dates.empty:
            history_tickers = (
                historical_tx["Ticker"]
                .dropna()
                .astype(str)
                .str.strip()
            )
            history_tickers = [
                ticker for ticker in dict.fromkeys(history_tickers) if ticker
            ]

            if history_tickers:
                history_start = (
                    historical_dates.min().normalize()
                    - pd.Timedelta(days=7)
                )

                if valuation_as_of_date is not None:
                    history_end_date = pd.Timestamp(
                        valuation_as_of_date
                    ).normalize() + pd.Timedelta(days=1)
                else:
                    history_end_date = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)

                try:
                    (
                        ledger_history_prices,
                        _,
                        ledger_history_valid,
                        ledger_history_invalid,
                    ) = cached_market_data_range(
                        tuple(history_tickers),
                        history_start.strftime("%Y-%m-%d"),
                        history_end_date.strftime("%Y-%m-%d"),
                    )

                    historical_account_curve, curve_missing_tickers = (
                        build_historical_account_equity(
                            historical_tx,
                            st.session_state.get(
                                "accepted_cashflows",
                                parsed.get("cashflows"),
                            ),
                            ledger_history_prices,
                            starting_free_cash=current_cash,
                        )
                    )

                except Exception as exc:
                    historical_account_curve = pd.DataFrame()
                    curve_missing_tickers = history_tickers
                    st.warning(
                        "Historical transaction performance could not be reconstructed "
                        f"right now. Today's accounting is unchanged. Details: {exc}"
                    )

                if not historical_account_curve.empty:
                    st.subheader("Historical Transaction Performance")
                    st.caption(
                        "Because this upload contains dated transactions, this chart reconstructs "
                        "the actual long/short holdings through time, values them with historical "
                        "adjusted market prices, adds account cash, and removes deposits/withdrawals "
                        "from return calculations. Invalid or unresolved transaction rows remain excluded."
                    )

                    usable_daily_returns = pd.to_numeric(
                        historical_account_curve["Daily Return"],
                        errors="coerce",
                    ).dropna()

                    total_return = pd.to_numeric(
                        historical_account_curve["Cumulative Return"],
                        errors="coerce",
                    ).dropna()
                    drawdown_series = pd.to_numeric(
                        historical_account_curve["Drawdown"],
                        errors="coerce",
                    ).dropna()

                    h1, h2, h3 = st.columns(3)
                    h1.metric(
                        "Reconstructed observations",
                        f"{len(historical_account_curve):,}",
                    )
                    h2.metric(
                        "Cumulative return",
                        f"{float(total_return.iloc[-1]):.2%}"
                        if not total_return.empty
                        else "N/A",
                    )
                    h3.metric(
                        "Maximum drawdown",
                        f"{float(drawdown_series.min()):.2%}"
                        if not drawdown_series.empty
                        else "N/A",
                    )

                    plot_historical_account_performance(
                        historical_account_curve
                    )

                    missing_for_curve = sorted(
                        set(ledger_history_invalid)
                        | set(curve_missing_tickers)
                    )
                    if missing_for_curve:
                        st.info(
                            "Historical account performance could not value: "
                            + ", ".join(missing_for_curve)
                            + ". Those securities remain excluded from this historical curve."
                        )

                    if usable_daily_returns.empty:
                        st.info(
                            "The equity curve is available, but a meaningful cash-flow-adjusted "
                            "return series could not be calculated because the account did not have "
                            "a usable positive/negative prior equity base on enough dates. Enter the "
                            "starting cash balance or include deposit history when available."
                        )



#______________________________________________________________________________
# STOP IF THERE ARE NO OPEN POSITIONS
#______________________________________________________________________________

if positions.empty:
    st.warning(
        "No open positions were found."
    )
    st.stop()


#______________________________________________________________________________
# GET VALUATION PRICES
#______________________________________________________________________________

provided_prices = None

if (
    mode == "holdings"
    and "Provided Price" in positions.columns
):
    usable = (
        positions
        .set_index("Ticker")["Provided Price"]
        .dropna()
    )

    if not usable.empty:
        provided_prices = usable

tickers = positions["Ticker"].tolist()

try:
    (
        latest_prices,
        invalid_tickers,
        valuation_metadata,
    ) = get_valuation_prices(
        tickers,
        valuation_mode,
        frozen_prices=frozen_prices,
        provided_prices=provided_prices,
        as_of_date=valuation_as_of_date,
    )

except Exception as exc:
    st.exception(exc)
    st.stop()

if invalid_tickers:
    missing_text = ", ".join(
        invalid_tickers
    )

    if valuation_metadata.get(
        "Strict",
        False,
    ):
        st.error(
            "Frozen valuation is incomplete. "
            "No frozen price was found for: "
            + missing_text
            + ". Live prices were deliberately NOT used as a fallback."
        )
        st.stop()

    st.warning(
        "No usable valuation price was found for: "
        + missing_text
    )

positions = positions[
    positions["Ticker"].isin(
        latest_prices.index
    )
].copy()

if positions.empty:
    st.error(
        "No open position has a usable valuation price."
    )
    st.stop()


#______________________________________________________________________________
# VALUE OPEN POSITIONS
#______________________________________________________________________________

valued_positions, exposure = (
    value_current_positions(
        positions,
        latest_prices,
    )
)

signed_positions = (
    valued_positions[
        "Signed Market Value"
    ].sum()
)

portfolio_equity = (
    cash_balance
    + signed_positions
)


#______________________________________________________________________________
# PORTFOLIO OVERVIEW
#______________________________________________________________________________

st.subheader("Portfolio Overview")
st.markdown(
    "<div style='max-width:980px;color:#6b778c;margin-bottom:0.7rem;'>"
    "Today's holdings visual is generated from the final valued positions, so it works "
    "the same way whether the source began as a holdings file, transaction ledger or hybrid export."
    "</div>",
    unsafe_allow_html=True,
)
plot_holdings_donut(valued_positions)




#______________________________________________________________________________
# DISPLAY VALUED PORTFOLIO
#______________________________________________________________________________

if valuation_mode == LIVE_MARKET:
    portfolio_heading = "Today's Portfolio"
elif valuation_mode == HISTORICAL_AS_OF:
    portfolio_heading = (
        "Portfolio As of "
        f"{pd.Timestamp(valuation_as_of_date):%Y-%m-%d}"
    )
else:
    portfolio_heading = "Frozen Test Portfolio"

market_as_of = valuation_metadata.get(
    "Market As Of"
)

if market_as_of is not None and pd.notna(
    market_as_of
):
    as_of_text = pd.Timestamp(
        market_as_of
    ).strftime("%Y-%m-%d")
else:
    as_of_text = "not supplied"

st.subheader(portfolio_heading)
st.caption(
    f"Valuation: {valuation_mode} | "
    f"As of: {as_of_text} | "
    f"Price source: {valuation_metadata['Price Source']}."
)

if mode == "ledger":
    with st.expander(
        "Accounting Audit Benchmark",
        expanded=False,
    ):
        st.caption(
            "This benchmark reconstructs accepted BUY/SELL transactions only. "
            "BUY/SELL controls direction, signed quantities are normalised to "
            "absolute trade size, duplicate transaction IDs are excluded "
            "pending review, and DIVIDEND/TRANSFER rows do not change holdings. "
            "The audit price is each ticker's latest accepted transaction price "
            "within the selected accounting period, not the selected market price."
        )

        a1, a2 = st.columns(2)
        a1.metric("Audit Long", f"${audit_exposure['Long Exposure']:,.2f}")
        a2.metric("Audit Short", f"${audit_exposure['Short Exposure']:,.2f}")
        a3, a4 = st.columns(2)
        a3.metric("Audit Gross", f"${audit_exposure['Gross Exposure']:,.2f}")
        a4.metric("Audit Net", f"${audit_exposure['Net Exposure']:,.2f}")
        a5, a6 = st.columns(2)
        a5.metric("Audit Cash", f"${audit_exposure['Cash Balance']:,.2f}")
        a6.metric("Audit Equity", f"${audit_exposure['Portfolio Equity']:,.2f}")

        audit_display = audit_positions[[
            "Ticker", "Position", "Quantity", "Current Price", "Signed Market Value"
        ]].rename(columns={"Current Price": "Latest Accepted Trade Price"})
        with st.expander("Audit Position Table", expanded=False):
            themed_dataframe(audit_display, use_container_width=True, hide_index=True)

c1, c2 = st.columns(2)
c1.metric("Portfolio Equity", f"${portfolio_equity:,.2f}")
c2.metric("Cash Balance", f"${cash_balance:,.2f}")
c3, c4 = st.columns(2)
c3.metric("Long Exposure", f"${exposure['Long Exposure']:,.2f}")
c4.metric("Short Exposure", f"${exposure['Short Exposure']:,.2f}")
c5, c6 = st.columns(2)
c5.metric("Gross Exposure", f"${exposure['Gross Exposure']:,.2f}")
c6.metric("Net Exposure", f"${exposure['Net Exposure']:,.2f}")

if pd.notna(exposure["Unrealised P&L"]):
    st.metric("Open Unrealised P&L", f"${exposure['Unrealised P&L']:,.2f}")
else:
    st.metric("Open Unrealised P&L", "N/A")

if mode == "holdings" and valued_positions["Quantity"].lt(0).any():
    st.warning(
        "This snapshot contains short positions. Ticker + Quantity can value the "
        "short exposure today, but complete account equity requires the actual account cash balance."
    )

if mode == "holdings":
    st.info(
        "A current holdings snapshot does not contain transaction cost basis. "
        "Entry price and P&L remain unavailable unless ledger data is supplied."
    )


if mode == "ledger":
    with st.expander("Transaction & Cash Ledger", expanded=False):
        themed_dataframe(
            ledger.style.format({
                "Quantity": "{:,.4f}",
                "Price": "${:,.2f}",
                "Cash Before": "${:,.2f}",
                "Cash Change": "${:,.2f}",
                "Cash After": "${:,.2f}",
                "Realised P&L": "${:,.2f}",
            }),
            use_container_width=True,
            hide_index=True,
            sentiment_columns=["Cash Change", "Realised P&L"],
        )


#______________________________________________________________________________
# DISPLAY OPEN POSITIONS
#______________________________________________________________________________

positions_col, stress_col = st.columns(2, gap="large")

with positions_col:
    with st.expander("Open Positions", expanded=False):
        display_columns = [
            "Ticker",
            "Position",
            "Quantity",
            "Average Entry Price",
            "Current Price",
            "Exposure",
            "Signed Market Value",
            "Unrealised P&L",
            "Realised P&L",
        ]

        display_columns = [
            column
            for column in display_columns
            if column
            in valued_positions.columns
        ]

        display = valued_positions[
            display_columns
        ].copy()

        themed_dataframe(
            display.style.format({
                "Quantity": "{:,.4f}",
                "Average Entry Price": (
                    lambda x:
                    "N/A"
                    if pd.isna(x)
                    else f"${x:,.2f}"
                ),
                "Current Price": "${:,.2f}",
                "Exposure": "${:,.2f}",
                "Signed Market Value": "${:,.2f}",
                "Unrealised P&L": (
                    lambda x:
                    "N/A"
                    if pd.isna(x)
                    else f"${x:,.2f}"
                ),
                "Realised P&L": (
                    lambda x:
                    "N/A"
                    if pd.isna(x)
                    else f"${x:,.2f}"
                ),
            }),
            use_container_width=True,
            hide_index=True,
            sentiment_columns=["Unrealised P&L", "Realised P&L"],
        )

with stress_col:
    render_stress_test(valued_positions, portfolio_equity)

#______________________________________________________________________________
# HISTORICAL RISK + ALLOCATION ANALYTICS FOR VALIDATED OPEN POSITIONS
#______________________________________________________________________________

st.subheader("Historical Risk & Allocation Analytics")
st.caption(
    "This section uses Yahoo Finance daily adjusted market prices for the validated "
    "open securities above. It is deliberately separate from today's accounting: "
    "cash, realised P&L and transaction-ledger balances are not rewritten."
)

analytics_tickers = valued_positions["Ticker"].astype(str).dropna().unique().tolist()

try:
    historical_prices, historical_returns, valid_history_tickers, invalid_history_tickers = (
        cached_market_data(tuple(analytics_tickers))
    )
except Exception as exc:
    st.warning(
        "Historical analytics could not retrieve market history right now. "
        f"Today's accounting is unchanged. Details: {exc}"
    )
    historical_prices = pd.DataFrame()
    historical_returns = pd.DataFrame()
    valid_history_tickers = []
    invalid_history_tickers = analytics_tickers

if invalid_history_tickers:
    st.info(
        "Historical analytics are unavailable for: "
        + ", ".join(map(str, invalid_history_tickers))
        + ". These securities remain in today's accounting if they have a valid valuation price."
    )

if historical_prices.empty or historical_returns.empty or not valid_history_tickers:
    st.info(
        "Not enough historical market data is available to calculate risk, VaR, "
        "Sharpe ratio, drawdown, historical performance or optimisation."
    )
else:
    st.markdown("### Risk settings")
    risk_a, risk_b = st.columns(2)
    with risk_a:
        portfolio_risk_free_pct = st.number_input(
            "Annual risk-free rate (%)",
            min_value=-20.0,
            max_value=50.0,
            value=0.0,
            step=0.25,
            key="validated_portfolio_risk_free_rate",
        )
    with risk_b:
        portfolio_var_confidence_pct = st.selectbox(
            "Historical VaR confidence",
            [90, 95, 99],
            index=1,
            key="validated_portfolio_var_confidence",
        )

    portfolio_risk_free_rate = float(portfolio_risk_free_pct) / 100.0
    portfolio_var_level = float(portfolio_var_confidence_pct) / 100.0

    # Calculate the security-level table first, then render it in the requested
    # dashboard order below.
    security_risk = asset_metrics(
        historical_returns[valid_history_tickers],
        risk_free_rate=portfolio_risk_free_rate,
        var_level=portfolio_var_level,
    )
    security_display = pd.DataFrame()
    if not security_risk.empty:
        security_display = security_risk.copy()
        for col in [
            "annual_return",
            "annual_volatility",
            "var",
            "expected_shortfall",
            "max_drawdown",
        ]:
            security_display[col] = security_display[col] * 100.0
        security_display = security_display.rename(columns={
            "observations": "Return Obs.",
            "annual_return": "Annual Return %",
            "annual_volatility": "Annual Volatility %",
            "sharpe": "Sharpe",
            "var": f"Daily VaR {portfolio_var_confidence_pct}%",
            "expected_shortfall": "Daily Expected Shortfall %",
            "max_drawdown": "Max Drawdown %",
        })

    # Portfolio-level analytics are run on the validated OPEN positions, regardless
    # of whether they came from a holdings sheet or a transaction ledger.
    analytics_positions = valued_positions[
        valued_positions["Ticker"].isin(valid_history_tickers)
    ].copy()
    has_short_positions = bool((analytics_positions["Quantity"] < 0).any())

    signed_values = (
        analytics_positions
        .set_index("Ticker")["Signed Market Value"]
        .astype(float)
        .groupby(level=0)
        .sum()
    )
    signed_values = signed_values[signed_values != 0.0]

    if signed_values.empty:
        st.info("No non-zero open market exposure is available for portfolio-level historical analytics.")
        if not security_display.empty:
            with st.expander("Security Risk Table", expanded=False):
                themed_dataframe(
                    security_display.round(3),
                    use_container_width=True,
                    hide_index=True,
                )
    else:
        allocation_tickers = [
            ticker for ticker in valid_history_tickers if ticker in signed_values.index
        ]
        allocation_returns = historical_returns[allocation_tickers]

        if has_short_positions:
            # For a long/short book, preserve sign and current leverage.  When account
            # equity is usable, weights are signed market value / current equity; cash
            # is therefore the zero-return residual.  If equity is not a stable base,
            # fall back to gross-exposure weights and label the result accordingly.
            gross_exposure_base = float(signed_values.abs().sum())
            equity_base_usable = (
                np.isfinite(portfolio_equity)
                and abs(float(portfolio_equity)) > max(1e-9, 0.01 * gross_exposure_base)
            )

            if equity_base_usable:
                current_weights = signed_values.reindex(allocation_tickers).fillna(0.0) / float(portfolio_equity)
                allocation_label = "Current open-book risk"
                allocation_caption = (
                    "This uses the transaction ledger's current open positions and cash balance. "
                    "Signed market values are divided by current account equity, so short positions "
                    "retain their hedge/leverage effect and cash acts as the zero-return residual."
                )
            else:
                current_weights = signed_values.reindex(allocation_tickers).fillna(0.0) / gross_exposure_base
                allocation_label = "Current open-position sleeve risk"
                allocation_caption = (
                    "Current account equity is too small or unstable to use as a return denominator, "
                    "so this view is scaled by gross open exposure. Longs stay positive and shorts stay negative."
                )

            current_metrics = _signed_portfolio_metrics(
                allocation_returns,
                current_weights,
                risk_free_rate=portfolio_risk_free_rate,
                var_level=portfolio_var_level,
            )
            current_returns = _signed_portfolio_returns(
                allocation_returns,
                current_weights,
            )
            contribution = _signed_risk_contribution(
                allocation_returns,
                current_weights,
            ) if len(allocation_tickers) >= 2 else pd.DataFrame()
        else:
            long_values = signed_values.clip(lower=0.0)
            long_values = long_values[long_values > 0]
            if long_values.empty or float(long_values.sum()) <= 0:
                st.info("No positive long market value is available for portfolio-level historical analytics.")
                current_weights = pd.Series(dtype=float)
                current_metrics = None
                current_returns = pd.Series(dtype=float)
                contribution = pd.DataFrame()
                allocation_label = "Current allocation risk"
                allocation_caption = ""
            else:
                allocation_tickers = [
                    ticker for ticker in valid_history_tickers if ticker in long_values.index
                ]
                allocation_returns = historical_returns[allocation_tickers]
                current_weights = long_values.reindex(allocation_tickers).fillna(0.0)
                current_weights = current_weights / current_weights.sum()
                current_metrics = market_portfolio_metrics(
                    allocation_returns,
                    current_weights,
                    risk_free_rate=portfolio_risk_free_rate,
                    var_level=portfolio_var_level,
                )
                current_returns = market_portfolio_returns(
                    allocation_returns,
                    current_weights,
                )
                contribution = market_risk_contribution(
                    allocation_returns,
                    current_weights,
                ) if len(allocation_tickers) >= 2 else pd.DataFrame()
                allocation_label = "Current allocation risk"
                allocation_caption = (
                    "Weights are based on today's validated long market values and sum to 100% "
                    "across securities with usable historical data. Cash is excluded, so this is "
                    "asset-sleeve risk rather than a reconstruction of historical account equity."
                )

        if current_metrics is not None:
            # Same analytics route for a holdings upload and for a transaction ledger's
            # reconstructed open positions.
            if len(allocation_tickers) >= 2:
                st.markdown("### Risk contribution")
                if contribution.empty:
                    st.info("Not enough overlapping history to calculate risk contribution.")
                else:
                    plot_risk_contribution(contribution)

            allocation_col, security_col = st.columns(2, gap="large")
            with allocation_col:
                st.markdown(f"### {allocation_label}")
                st.caption(allocation_caption)

                m1, m2 = st.columns(2)
                m1.metric("Annual return", f"{current_metrics['annual_return']:.2%}")
                m2.metric("Annual volatility", f"{current_metrics['annual_volatility']:.2%}")
                m3, m4 = st.columns(2)
                m3.metric(
                    "Sharpe",
                    f"{current_metrics['sharpe']:.2f}"
                    if np.isfinite(current_metrics["sharpe"])
                    else "N/A",
                )
                m4.metric(
                    f"Daily VaR {portfolio_var_confidence_pct}%",
                    f"{current_metrics['var']:.2%}",
                )
                m5, m6 = st.columns(2)
                m5.metric("Expected shortfall", f"{current_metrics['expected_shortfall']:.2%}")
                m6.metric("Max drawdown", f"{current_metrics['max_drawdown']:.2%}")

            with security_col:
                st.markdown("### Security risk")
                if not security_display.empty:
                    with st.expander("Security Risk Table", expanded=False):
                        themed_dataframe(
                            security_display.round(3),
                            use_container_width=True,
                            hide_index=True,
                        )
                else:
                    st.caption("No security-level risk table is available for this portfolio.")

            performance_title = (
                "Historical performance of today's open book"
                if has_short_positions
                else "Historical performance of today's allocation"
            )
            st.markdown(f"### {performance_title}")
            if has_short_positions:
                st.caption(
                    "This is a static backtest of today's reconstructed open positions against historical "
                    "market returns. It is separate from the dated transaction-ledger equity curve above."
                )
            if current_returns.empty:
                st.info("Not enough overlapping market history to build a portfolio return series.")
            else:
                plot_growth_and_drawdown(current_returns)

            if len(allocation_tickers) >= 2:
                plot_correlation_heatmap(allocation_returns)

                render_on_demand_monte_carlo(
                    allocation_returns,
                    risk_free_rate=portfolio_risk_free_rate,
                    tickers=allocation_tickers,
                    key_prefix="validated_portfolio",
                )
                if has_short_positions:
                    st.caption(
                        "Monte Carlo remains a long-only allocation search across the same open-position universe; "
                        "it does not overwrite or reinterpret the current short positions."
                    )
            else:
                st.info(
                    "One security has usable history. Portfolio correlation, risk contribution "
                    "and optimisation require at least two securities."
                )

st.success(
    "Risk analytics are active for historical market-data files and validated open positions, "
    "including reconstructed transaction-ledger books. Today's accounting remains unchanged."
)
