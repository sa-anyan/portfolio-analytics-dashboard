"""Compact Discover workspace: render stored findings; never calculate portfolio metrics."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from portfolio_analytics.scenarios.engine import run_scenario
from portfolio_analytics.ui.trust import render_trust


def navigate_to_dashboard(section: str = "overview") -> None:
    st.session_state["workspace"] = "Portfolio dashboard"
    st.session_state["dashboard_focus"] = section


def queue_copilot_question(question: str) -> None:
    # Callback runs before the next script pass, before the text-area is created.
    # Preparing a question never sends it or consumes an AI request.
    st.session_state["copilot_question"] = question


def visible_findings(findings: list[dict], limit: int = 3) -> tuple[list[dict], list[dict]]:
    priority = {"concentration_unavailable": 0, "risk_unavailable": 0,
                "direct_concentration": 1, "risk_driver": 2, "capital_vs_risk": 3}
    ordered = sorted(findings, key=lambda f: (priority.get(f.get("id"), 4), f.get("id", "")))
    return ordered[:limit], ordered[limit:]


def _finding(finding: dict, deep: dict) -> None:
    identifier = finding.get("id", "finding")
    title = str(finding.get("title", "Portfolio finding"))
    st.markdown(f"**{title}**")
    st.markdown(str(finding.get("explanation", "Explanation unavailable.")))
    with st.expander(f"Evidence and calculations · {title}"):
        st.caption("Evidence from the accepted portfolio and deterministic analytics engine.")
        evidence = finding.get("evidence", {})
        st.dataframe(pd.DataFrame([{"Measure": key.replace("_", " "), "Value": str(value)}
                                  for key, value in evidence.items()]), hide_index=True, use_container_width=True)
        if identifier == "direct_concentration":
            c = deep.get("concentration", {})
            st.caption(c.get("method", ""))
            st.caption("Shares are fractions: 0.85 means 85%. HHI uses the 0–1 scale. Cash is excluded.")
            st.dataframe(pd.DataFrame(c.get("holdings", [])), hide_index=True, use_container_width=True)
        if identifier in {"risk_driver", "capital_vs_risk"}:
            ranked = deep.get("risk_interpretation", {}).get("ranked_contributors", [])
            frame = pd.DataFrame(ranked)
            if not frame.empty:
                columns = ["ticker", "signed_equity_weight", "gross_security_exposure_share",
                           "risk_contribution_pct", "absolute_risk_share", "effect"]
                st.dataframe(frame[[c for c in columns if c in frame]], hide_index=True, use_container_width=True)
            st.caption("Fractions use different denominators. Signed equity weights can exceed 1 or be negative. Negative signed volatility contributions identify historical hedge effects.")
    st.caption("Investigate next · " + str(finding.get("investigate", "Review portfolio methodology.")))
    first, second = st.columns(2)
    with first:
        target = "allocation" if identifier == "direct_concentration" else "risk"
        if finding.get("topic") == "data_quality":
            target = "data"
        st.button("Open supporting portfolio analysis", key=f"discover_analysis_{identifier}",
                  on_click=navigate_to_dashboard, args=(target,), use_container_width=True)
    with second:
        st.button("Prepare a Copilot question", key=f"discover_copilot_{identifier}",
                  on_click=queue_copilot_question,
                  args=(f"Explain this deterministic finding and its limitations: {title}. Use the accepted portfolio's deep_findings evidence.",),
                  use_container_width=True)


def render_discover(state: dict | None, analytics: dict | None) -> None:
    st.subheader("Deep Analytics")
    view = st.radio("Deep Analytics view", ["Discover", "Exposure"], horizontal=True, key="deep_analytics_view")
    if view == "Exposure":
        from portfolio_analytics.ui.constituents import render_exposure
        render_exposure(state)
        return
    st.caption("Discover · What matters, why it matters, and where to investigate next.")
    if analytics and analytics.get("trust_diagnostics"):
        render_trust(analytics["trust_diagnostics"])
        if analytics["trust_diagnostics"].get("valuation_status") == "blocked":
            st.info("Refresh the affected inputs on the portfolio dashboard before interpreting findings or modelling scenarios.")
            st.button("Return to portfolio dashboard", on_click=navigate_to_dashboard)
            return
    if state is None or analytics is None:
        st.info("Analyse a portfolio on the portfolio dashboard first. Discover will use that same portfolio; no second upload is needed.")
        st.button("Build your portfolio", on_click=navigate_to_dashboard, use_container_width=True)
        return
    deep = analytics.get("deep_findings")
    if not deep:
        st.info("Deterministic findings are unavailable for this session. Analyse the portfolio again to generate them.")
        st.button("Return to portfolio dashboard", on_click=navigate_to_dashboard)
        return
    st.caption(f"Accepted portfolio · Reporting currency: {state.get('meta', {}).get('base_currency', 'USD')} · {len(state.get('positions', []))} open security positions")
    if not state.get("positions"):
        st.info("This portfolio has no open security positions. Security concentration is undefined; any retained cash remains in the accepted account.")
    st.caption("These findings describe direct securities only. Open Exposure for known direct-plus-indirect securities, eligibility and coverage; unavailable indirect exposure is unknown, not zero.")
    findings = deep.get("findings", [])
    if analytics.get("trust_diagnostics", {}).get("risk_status") == "unavailable":
        findings = [f for f in findings if f.get("id") not in {"risk_driver", "capital_vs_risk"}]
    shown, remaining = visible_findings(findings)
    if not shown:
        st.info("No supported findings are available. Review portfolio coverage and methodology.")
    for finding in shown:
        _finding(finding, deep)
        st.divider()
    if remaining:
        with st.expander(f"More findings ({len(remaining)})"):
            for finding in remaining:
                _finding(finding, deep)
    with st.expander("Methodology and coverage"):
        for limitation in deep.get("limitations", []):
            st.write(limitation)
        interpretation = deep.get("risk_interpretation", {})
        st.write(f"Risk interpretation: {interpretation.get('observations', 0)} common observations; minimum {interpretation.get('minimum_observations', 'unavailable')}.")
        if interpretation.get("reason"):
            st.info(interpretation["reason"])
        st.caption(analytics.get("meta", {}).get("risk_method", "Risk methodology unavailable."))
    with st.expander("Explore an existing price-shock scenario"):
        st.caption("Hypothetical current-mark shock. It uses the existing scenario engine and never changes accepted holdings. Funding and trading costs follow that engine's documented assumptions.")
        st.caption("Scenario risk retains the source portfolio's historical sample and data-quality limitations.")
        tickers = [row["ticker"] for row in state.get("positions", []) if row.get("current_price") is not None]
        risk_rows = deep.get("risk_interpretation", {}).get("ranked_contributors", [])
        preferred = risk_rows[0]["ticker"] if risk_rows else None
        if tickers:
            if preferred in tickers:
                tickers = [preferred] + [ticker for ticker in tickers if ticker != preferred]
            ticker = st.selectbox("Position to investigate", tickers, key="discover_shock_ticker")
            shock = st.number_input("Price change (%)", min_value=-99.0, max_value=1000.0, value=-15.0, step=1.0, key="discover_shock_percent")
            if st.button("Run hypothetical shock", key="discover_run_shock"):
                try:
                    st.session_state["latest_scenario"] = run_scenario(state, analytics, [{"action": "price_shock", "ticker": ticker, "value": shock}])
                except (ValueError, TypeError) as exc:
                    st.error(f"Scenario unavailable: {exc}")
            scenario = st.session_state.get("latest_scenario")
            if scenario:
                comparison = scenario.get("comparison", {})
                change = comparison.get("equity_change")
                st.write(f"Latest hypothetical scenario equity change: {change:,.2f} {state.get('meta', {}).get('base_currency', 'USD')}" if change is not None else "Scenario equity change unavailable.")
                coverage = scenario.get("scenario_analytics", {}).get("coverage", {})
                if not coverage.get("available", False):
                    st.info("Scenario risk unavailable: " + str(coverage.get("reason", "Incomplete data.")))
                st.button("Open existing scenario results", on_click=navigate_to_dashboard, args=("scenario",))
        else:
            st.info("No priced open position is available for a security shock.")
