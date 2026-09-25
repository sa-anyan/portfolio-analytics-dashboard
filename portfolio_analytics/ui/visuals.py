"""Plotly figure builders. No calculations that belong to the finance engine live here."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


#______________________________________________________________________________
# SHARED LAYOUT
#______________________________________________________________________________

def _layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title={"text": title, "x": 0.02, "xanchor": "left"},
        margin=dict(l=35, r=25, t=58, b=35),
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend_title_text="",
        height=340,
        hoverlabel=dict(font_size=12),
    )
    return fig


#______________________________________________________________________________
# HOLDINGS MIX
#______________________________________________________________________________

def holdings_donut(analytics: dict[str, Any]) -> go.Figure:
    data = pd.DataFrame(analytics.get("holdings_mix", []))
    if data.empty:
        return _layout(go.Figure(), "Holdings / Exposure Mix")
    data["Exposure %"] = pd.to_numeric(data["exposure_share"], errors="coerce").fillna(0.0) * 100.0
    fig = px.pie(data, names="ticker", values="Exposure %", hole=0.55)
    fig.update_traces(textposition="inside", textinfo="label+percent", hovertemplate="%{label}<br>Exposure share: %{value:.2f}%<extra></extra>")
    return _layout(fig, "Holdings / Gross Exposure Mix")


#______________________________________________________________________________
# RISK CONTRIBUTION
#______________________________________________________________________________

def risk_contribution_donut(analytics: dict[str, Any]) -> go.Figure:
    data = pd.DataFrame(analytics.get("risk_contribution", []))
    if data.empty:
        return _layout(go.Figure(), "Risk Contribution")
    data["Absolute Risk Share %"] = pd.to_numeric(data["absolute_risk_share"], errors="coerce").fillna(0.0) * 100.0
    data["Raw Contribution %"] = pd.to_numeric(data["risk_contribution_pct"], errors="coerce").fillna(0.0) * 100.0
    fig = px.pie(data, names="ticker", values="Absolute Risk Share %", hole=0.55, custom_data=["Raw Contribution %"])
    fig.update_traces(
        textposition="inside",
        textinfo="label+percent",
        hovertemplate="%{label}<br>Absolute risk share: %{value:.2f}%<br>Signed contribution: %{customdata[0]:.2f}%<extra></extra>",
    )
    return _layout(fig, "Risk Contribution")


#______________________________________________________________________________
# CORRELATION GRID
#______________________________________________________________________________

def correlation_heatmap(analytics: dict[str, Any]) -> go.Figure:
    corr = analytics.get("correlation", {})
    if not corr:
        return _layout(go.Figure(), "Correlation")
    frame = pd.DataFrame(corr).T
    frame = frame.apply(pd.to_numeric, errors="coerce")
    fig = go.Figure(data=go.Heatmap(
        z=frame.values,
        x=frame.columns,
        y=frame.index,
        zmin=-1,
        zmax=1,
        colorscale="RdBu",
        reversescale=True,
        text=frame.round(2).astype(str).values,
        texttemplate="%{text}",
        hovertemplate="%{y} vs %{x}<br>Correlation: %{z:.3f}<extra></extra>",
        colorbar={"title": "Correlation"},
    ))
    return _layout(fig, "Correlation Grid")


#______________________________________________________________________________
# PERFORMANCE + DRAWDOWN
#______________________________________________________________________________

def performance_figure(analytics: dict[str, Any]) -> go.Figure:
    rows = analytics.get("series", {}).get("portfolio_path", [])
    frame = pd.DataFrame(rows)
    if frame.empty or "date" not in frame.columns or "cumulative_return" not in frame.columns:
        return _layout(go.Figure(), "Portfolio Growth")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["Growth of 1"] = 1.0 + pd.to_numeric(frame["cumulative_return"], errors="coerce")
    fig = px.line(frame, x="date", y="Growth of 1")
    fig.update_traces(hovertemplate="%{x|%d %b %Y}<br>Growth of 1: %{y:.3f}<extra></extra>")
    fig.update_yaxes(title="Growth of 1")
    fig.update_xaxes(title="")
    return _layout(fig, "Portfolio Growth")


def reconstructed_value_figure(actual: dict[str, Any]) -> go.Figure:
    rows = actual.get("portfolio_path", [])
    frame = pd.DataFrame(rows)
    if frame.empty or "date" not in frame.columns or "equity" not in frame.columns:
        return _layout(go.Figure(), "Reconstructed Portfolio Value")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["Portfolio Value"] = pd.to_numeric(frame["equity"], errors="coerce")
    base = str(actual.get("base_currency") or "USD").upper()
    fig = px.line(frame, x="date", y="Portfolio Value")
    fig.update_traces(hovertemplate=f"%{{x|%d %b %Y}}<br>Portfolio value: %{{y:,.2f}} {base}<extra></extra>")
    fig.update_yaxes(title=f"Portfolio value ({base})")
    fig.update_xaxes(title="")
    return _layout(fig, "Reconstructed Portfolio Value")


def drawdown_figure(analytics: dict[str, Any]) -> go.Figure:
    rows = analytics.get("series", {}).get("portfolio_path", [])
    frame = pd.DataFrame(rows)
    if frame.empty or "date" not in frame.columns or "drawdown" not in frame.columns:
        return _layout(go.Figure(), "Drawdown")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["Drawdown %"] = pd.to_numeric(frame["drawdown"], errors="coerce") * 100.0
    fig = px.area(frame, x="date", y="Drawdown %")
    fig.update_traces(hovertemplate="%{x|%d %b %Y}<br>Drawdown: %{y:.2f}%<extra></extra>")
    fig.update_xaxes(title="")
    return _layout(fig, "Drawdown")


#______________________________________________________________________________
# EXPOSURE
#______________________________________________________________________________

def exposure_bar(analytics: dict[str, Any]) -> go.Figure:
    exposure = analytics.get("exposure", {})
    data = pd.DataFrame({
        "Measure": ["Long", "Short", "Gross", "Net"],
        "Value": [
            exposure.get("long"),
            exposure.get("short"),
            exposure.get("gross"),
            exposure.get("net"),
        ],
    })
    data["Value"] = pd.to_numeric(data["Value"], errors="coerce").fillna(0.0)
    fig = px.bar(data, x="Measure", y="Value")
    fig.update_traces(hovertemplate="%{x}: %{y:,.2f}<extra></extra>")
    fig.update_yaxes(title="Value")
    return _layout(fig, "Portfolio Exposure")


#______________________________________________________________________________
# SCENARIO COMPARISON
#______________________________________________________________________________

def scenario_comparison_bar(scenario: dict[str, Any]) -> go.Figure:
    """Compare accepted and scenario portfolio values without recalculating them."""
    comparison = scenario.get("comparison", {})
    portfolio = comparison.get("portfolio", {})
    before = portfolio.get("before", {})
    after = portfolio.get("after", {})

    measures = ["Equity", "Cash", "Gross Exposure", "Net Exposure"]
    keys = ["equity", "cash", "gross_exposure", "net_exposure"]
    rows = []
    for label, key in zip(measures, keys):
        rows.append({"Measure": label, "Portfolio": "Accepted", "Value": before.get(key)})
        rows.append({"Measure": label, "Portfolio": "Scenario", "Value": after.get(key)})

    data = pd.DataFrame(rows)
    data["Value"] = pd.to_numeric(data["Value"], errors="coerce")
    data = data.dropna(subset=["Value"])
    if data.empty:
        return _layout(go.Figure(), "Accepted vs Scenario")

    fig = px.bar(data, x="Measure", y="Value", color="Portfolio", barmode="group")
    fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:,.2f}<extra></extra>")
    fig.update_yaxes(title="Value")
    return _layout(fig, "Accepted vs Scenario")


def scenario_position_change_bar(scenario: dict[str, Any]) -> go.Figure:
    """Visualise signed market-value changes already produced by the scenario engine."""
    positions = scenario.get("comparison", {}).get("positions", [])
    data = pd.DataFrame(positions)
    required = {"ticker", "market_value_before", "market_value_after"}
    if data.empty or not required.issubset(data.columns):
        return _layout(go.Figure(), "Position Value Changes")

    data["Accepted"] = pd.to_numeric(data["market_value_before"], errors="coerce")
    data["Scenario"] = pd.to_numeric(data["market_value_after"], errors="coerce")
    tidy = data[["ticker", "Accepted", "Scenario"]].melt(
        id_vars="ticker", var_name="Portfolio", value_name="Value"
    ).dropna(subset=["Value"])
    fig = px.bar(tidy, x="ticker", y="Value", color="Portfolio", barmode="group")
    fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:,.2f}<extra></extra>")
    fig.update_xaxes(title="")
    fig.update_yaxes(title="Signed market value")
    return _layout(fig, "Position Value Changes")


#______________________________________________________________________________
# HISTORICAL COMBINATION RISK
#______________________________________________________________________________

def combination_risk_heatmap(combination_risk: dict[str, Any], metric: str = "annual_volatility") -> go.Figure:
    """Pairwise risk map from deterministic combination-risk results."""
    rows = combination_risk.get("results", []) if isinstance(combination_risk, dict) else []
    data = pd.DataFrame(rows)
    if data.empty or int(combination_risk.get("combination_size", 0) or 0) != 2:
        return _layout(go.Figure(), "Historical Combination Risk")

    tickers = list(combination_risk.get("selected_tickers", []))
    matrix = pd.DataFrame(float("nan"), index=tickers, columns=tickers)
    for row in rows:
        group = row.get("combination", [])
        value = row.get(metric)
        if len(group) == 2 and value is not None:
            a, b = group
            matrix.loc[a, b] = float(value)
            matrix.loc[b, a] = float(value)

    title_map = {
        "annual_volatility": "Pair Risk Map · Annual Volatility",
        "max_drawdown": "Pair Risk Map · Maximum Drawdown",
        "var_pct": "Pair Risk Map · VaR 95%",
        "expected_shortfall_pct": "Pair Risk Map · Expected Shortfall 95%",
    }
    fig = px.imshow(
        matrix * 100.0,
        labels={"color": "%"},
        aspect="auto",
        color_continuous_scale="RdYlGn_r" if metric != "max_drawdown" else "RdYlGn",
    )
    fig.update_traces(hovertemplate="%{x} + %{y}<br>%{z:.2f}%<extra></extra>")
    fig.update_xaxes(title="")
    fig.update_yaxes(title="")
    return _layout(fig, title_map.get(metric, "Historical Combination Risk"))


def combination_risk_ranked(combination_risk: dict[str, Any], metric: str = "annual_volatility", top_n: int = 20) -> go.Figure:
    """Rank larger combinations by one deterministic historical risk measure."""
    data = pd.DataFrame(combination_risk.get("results", []))
    if data.empty or metric not in data.columns:
        return _layout(go.Figure(), "Historical Combination Risk")
    data[metric] = pd.to_numeric(data[metric], errors="coerce")
    data = data.dropna(subset=[metric]).copy()
    if metric == "max_drawdown":
        data["risk_display"] = -data[metric] * 100.0
    else:
        data["risk_display"] = data[metric] * 100.0
    data = data.sort_values("risk_display", ascending=True).head(int(top_n))
    fig = px.bar(data, x="risk_display", y="label", orientation="h")
    fig.update_yaxes(title="", categoryorder="total descending")
    fig.update_xaxes(title="Historical risk (%)")
    fig.update_traces(hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>")
    return _layout(fig, "Lowest Historical Risk Combinations")
