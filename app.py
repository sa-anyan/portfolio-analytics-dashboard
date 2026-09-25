"""Portfolio Analytics v4 Streamlit interface.

The app gathers input and renders outputs. Financial calculations live exclusively
inside the package engines.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from portfolio_analytics.ai.copilot import ask_copilot, generate_dashboard_insights
from portfolio_analytics.analytics.engine import run_analytics, run_account_performance, returns_from_analytics
from portfolio_analytics.analytics.combination_risk import calculate_combination_risk
from portfolio_analytics.core.market_data import fetch_latest_prices, fetch_price_history
from portfolio_analytics.core.fx import fetch_fx_history
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.manual import parse_manual_holdings
from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.ui.insights import fallback_insights
from portfolio_analytics.ui.styles import APP_CSS, HERO_HTML
from portfolio_analytics.ui.visuals import (
    correlation_heatmap,
    combination_risk_heatmap,
    combination_risk_ranked,
    drawdown_figure,
    exposure_bar,
    holdings_donut,
    performance_figure,
    reconstructed_value_figure,
    risk_contribution_donut,
    scenario_comparison_bar,
    scenario_position_change_bar,
)


#______________________________________________________________________________
# PAGE SETUP
#______________________________________________________________________________

st.set_page_config(page_title="Portfolio Analytics v4", layout="wide")
st.markdown(APP_CSS, unsafe_allow_html=True)
st.markdown(HERO_HTML, unsafe_allow_html=True)


#______________________________________________________________________________
# SESSION STATE
#______________________________________________________________________________

for key, default in {
    "parsed": None,
    "portfolio_state": None,
    "analytics": None,
    "market_metadata": None,
    "copilot_insights": None,
    "copilot_messages": [],
    "latest_scenario": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

if "manual_rows" not in st.session_state:
    st.session_state.manual_rows = pd.DataFrame([
        {
            "Ticker": "",
            "Quantity": None,
            "Average Entry Price": None,
            "Current Price": None,
            "Asset Class": "",
            "Duration": None,
            "Rate Sensitivity": None,
        }
    ])


#______________________________________________________________________________
# PRESENTATION HELPERS
#______________________________________________________________________________

def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def number(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def insight_box(text: str) -> None:
    st.markdown(f'<div class="pa-insight">{text}</div>', unsafe_allow_html=True)


def get_api_key() -> str | None:
    try:
        return st.secrets.get("OPENAI_API_KEY")
    except Exception:
        return None


#______________________________________________________________________________
# CACHED MARKET DATA
#______________________________________________________________________________

@st.cache_data(ttl=900, show_spinner=False)
def cached_latest_prices(tickers: tuple[str, ...]):
    return fetch_latest_prices(list(tickers))


@st.cache_data(ttl=3600, show_spinner=False)
def cached_price_history(tickers: tuple[str, ...], period: str):
    return fetch_price_history(list(tickers), period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_dated_price_history(tickers: tuple[str, ...], start_date: str):
    return fetch_price_history(list(tickers), start=start_date)

@st.cache_data(ttl=3600, show_spinner=False)
def cached_fx_history(currencies: tuple[str, ...], start_date: str):
    return fetch_fx_history(list(currencies), start=start_date, base="USD")


#______________________________________________________________________________
# MAIN + COPILOT LAYOUT
#______________________________________________________________________________

main_col, copilot_col = st.columns([3.25, 1.15], gap="large")
api_key = get_api_key()

with copilot_col:
    st.markdown('<div class="pa-copilot">', unsafe_allow_html=True)
    st.subheader("AI Copilot")
    st.caption("Connected to parser, Portfolio State, analytics and scenario functions. Python calculates; Copilot interprets and explains.")
    if api_key:
        st.success("Copilot connected", icon="✅")
    else:
        st.info("Add `OPENAI_API_KEY` to Streamlit secrets to enable Copilot. The deterministic dashboard still works without it.")

    auto_ai_insights = st.toggle(
        "Automatic Copilot captions",
        value=bool(api_key),
        disabled=not bool(api_key),
        help="Generates short AI explanations after analysis. Disable this to minimise API calls.",
    )

    if st.session_state.portfolio_state is not None:
        for message in st.session_state.copilot_messages[-8:]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        question = st.text_area(
            "Ask anything about the portfolio",
            placeholder=(
                "Examples:\n"
                "How much BTC do I have?\n"
                "Why is my VaR high?\n"
                "What caused my drawdown in 2020?\n"
                "Which ticker contributed most to that drawdown?\n"
                "What if BTC falls 25%?\n"
                "What if rates rise 100 bps and I halve BTC?\n"
                "Target 10% annual volatility."
            ),
            height=135,
            key="copilot_question",
        )

        if st.button("Ask Copilot", type="primary", use_container_width=True):
            if not api_key:
                st.error("Copilot needs an OpenAI API key.")
            elif question.strip():
                st.session_state.copilot_messages.append({"role": "user", "content": question})
                try:
                    with st.spinner("Copilot is reading the portfolio engines..."):
                        response = ask_copilot(
                            question,
                            parsed=st.session_state.parsed,
                            state=st.session_state.portfolio_state,
                            analytics=st.session_state.analytics,
                            previous_scenario=st.session_state.latest_scenario,
                            conversation_history=st.session_state.copilot_messages[:-1],
                            api_key=api_key,
                        )
                    st.session_state.copilot_messages.append({"role": "assistant", "content": response["answer"]})
                    if response.get("scenario") is not None:
                        st.session_state.latest_scenario = response["scenario"]
                    st.rerun()
                except Exception as exc:
                    st.error(f"Copilot could not complete that request: {exc}")

        control_col1, control_col2 = st.columns(2)
        with control_col1:
            if st.session_state.latest_scenario is not None:
                if st.button("Reset scenario", use_container_width=True):
                    st.session_state.latest_scenario = None
                    st.rerun()
        with control_col2:
            if st.session_state.copilot_messages:
                if st.button("Clear chat", use_container_width=True):
                    st.session_state.copilot_messages = []
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


#______________________________________________________________________________
# INPUT ENGINE
#______________________________________________________________________________

with main_col:
    st.subheader("1. Build Your Portfolio")
    source_type = st.radio(
        "Input method",
        ["Upload file", "Manual entry"],
        horizontal=True,
    )

    settings_col1, settings_col2, settings_col3 = st.columns(3)
    with settings_col1:
        starting_cash = st.number_input("Starting / current cash", value=0.0, step=1000.0)
    with settings_col2:
        history_period = st.selectbox("Historical window", ["1y", "3y", "5y", "10y"], index=1)
    with settings_col3:
        use_live_prices = st.toggle("Use live market prices", value=True)

    uploaded = None
    manual_frame = None

    if source_type == "Upload file":
        uploaded = st.file_uploader(
            "Upload CSV or Excel portfolio",
            type=["csv", "xlsx"],
            help="The parser classifies holdings vs ledger and normalises it before anything reaches Portfolio State.",
        )
    else:
        st.caption("Enter holdings directly. Positive quantity = long; negative quantity = short. Entry price and current price are optional when live valuation is enabled.")
        manual_frame = st.data_editor(
            st.session_state.manual_rows,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "Ticker": st.column_config.TextColumn(required=True),
                "Quantity": st.column_config.NumberColumn(format="%.6f"),
                "Average Entry Price": st.column_config.NumberColumn(format="%.4f"),
                "Current Price": st.column_config.NumberColumn(format="%.4f"),
                "Asset Class": st.column_config.TextColumn(),
                "Duration": st.column_config.NumberColumn(help="Optional modified duration for rate scenarios."),
                "Rate Sensitivity": st.column_config.NumberColumn(help="Optional % price change for a +100 bp rate move."),
            },
            key="manual_editor",
        )
        st.session_state.manual_rows = manual_frame

    analyse = st.button("Parse & Analyse Portfolio", type="primary")

    if analyse:
        try:
            with st.spinner("Parsing and normalising portfolio..."):
                if source_type == "Upload file":
                    if uploaded is None:
                        raise ValueError("Upload a portfolio file first.")
                    parsed = parse_upload(uploaded.getvalue(), uploaded.name, starting_cash=starting_cash)
                else:
                    parsed = parse_manual_holdings(manual_frame, starting_cash=starting_cash)

            tickers = tuple(parsed.get("variables", {}).get("tickers", []))
            # CASH is a reserved non-market asset. Never send it to a market-data
            # provider where the symbol may resolve to an unrelated security.
            market_tickers = tuple(t for t in tickers if str(t).strip().upper() != "CASH")
            supplied_prices = parsed.get("variables", {}).get("provided_prices", {})

            latest_prices: dict[str, float] = dict(supplied_prices)
            latest_meta: dict[str, Any] = {"source": "user supplied", "as_of": None, "missing": []}

            if use_live_prices and market_tickers:
                with st.spinner("Fetching current market prices..."):
                    market_prices, latest_meta = cached_latest_prices(market_tickers)
                latest_prices.update(market_prices)

            state = build_portfolio_state(parsed, latest_prices=latest_prices, market_metadata=latest_meta)

            with st.spinner("Fetching historical prices and running analytics..."):
                history, history_meta = cached_price_history(market_tickers, history_period)
                analytics = run_analytics(state, history)

                accounting_history = state.get("accounting_history", {})
                if accounting_history.get("available"):
                    dated_events = accounting_history.get("trades", []) + accounting_history.get("cashflows", [])
                    dated_values = [pd.to_datetime(row.get("Date"), errors="coerce") for row in dated_events]
                    dated_values = [d for d in dated_values if pd.notna(d)]
                    if dated_values:
                        start_date = min(dated_values).date().isoformat()
                        actual_history, actual_history_meta = cached_dated_price_history(market_tickers, start_date)
                        currencies = tuple(sorted({
                            str(row.get("Currency") or "USD").upper()
                            for row in accounting_history.get("trades", [])
                        }))
                        fx_history, fx_meta = cached_fx_history(currencies, start_date)
                        analytics["actual_performance"] = run_account_performance(
                            state, actual_history, fx_history=fx_history, base_currency="USD"
                        )
                        history_meta = {**history_meta, "actual_performance": actual_history_meta, "fx": fx_meta}

            st.session_state.parsed = parsed
            st.session_state.portfolio_state = state
            st.session_state.analytics = analytics
            st.session_state.market_metadata = {"latest": latest_meta, "history": history_meta}
            st.session_state.latest_scenario = None
            st.session_state.copilot_messages = []

            insights = fallback_insights(state, analytics)
            if auto_ai_insights and api_key:
                try:
                    with st.spinner("Copilot is preparing chart captions..."):
                        insights = generate_dashboard_insights(state, analytics, api_key=api_key)
                except Exception as exc:
                    st.warning(f"AI captions were unavailable, so textbook fallback captions are shown. {exc}")
            st.session_state.copilot_insights = insights

        except Exception as exc:
            st.error(str(exc))


#______________________________________________________________________________
# PARSER + PORTFOLIO STATE REVIEW
#______________________________________________________________________________

with main_col:
    if st.session_state.parsed is not None:
        parsed = st.session_state.parsed
        state = st.session_state.portfolio_state
        analytics = st.session_state.analytics
        insights = st.session_state.copilot_insights or fallback_insights(state, analytics)

        st.divider()
        st.subheader("2. Parser & Portfolio State")
        a, b, c, d = st.columns(4)
        a.metric("Detected path", str(parsed.get("classification", "")).title())
        b.metric("Parser confidence", percent(parsed.get("confidence")))
        c.metric("Open positions", number(state.get("totals", {}).get("position_count"), 0))
        d.metric("Missing prices", number(len(state.get("meta", {}).get("missing_prices", [])), 0))

        if parsed.get("issues"):
            with st.expander(f"Parser review items ({len(parsed['issues'])})"):
                st.dataframe(pd.DataFrame(parsed["issues"]), use_container_width=True, hide_index=True)

        with st.expander("Normalised input used by the engine"):
            normalised = parsed.get("normalised_dataset", {})
            if parsed.get("classification") == "holdings":
                st.dataframe(pd.DataFrame(normalised.get("holdings", [])), use_container_width=True, hide_index=True)
            else:
                st.markdown("**Trades**")
                st.dataframe(pd.DataFrame(normalised.get("ledger", [])), use_container_width=True, hide_index=True)
                if normalised.get("cashflows"):
                    st.markdown("**Cash flows**")
                    st.dataframe(pd.DataFrame(normalised.get("cashflows", [])), use_container_width=True, hide_index=True)

        positions = pd.DataFrame(state.get("positions", []))
        if not positions.empty:
            st.markdown("**Current Portfolio State**")
            display_cols = [
                "ticker", "side", "quantity", "average_entry_price", "current_price",
                "signed_market_value", "exposure", "realised_pnl", "unrealised_pnl",
            ]
            st.dataframe(
                positions[[col for col in display_cols if col in positions.columns]],
                use_container_width=True,
                hide_index=True,
            )


#______________________________________________________________________________
# ANALYTICS DASHBOARD
#______________________________________________________________________________

with main_col:
    if st.session_state.analytics is not None:
        state = st.session_state.portfolio_state
        analytics = st.session_state.analytics
        insights = st.session_state.copilot_insights or fallback_insights(state, analytics)
        totals = state.get("totals", {})
        risk = analytics.get("risk", {})
        perf = analytics.get("performance", {})

        st.divider()
        st.subheader("3. Portfolio Analytics")
        st.markdown('<div class="pa-section-note">Accepted portfolio · deterministic analytics from the current Portfolio State.</div>', unsafe_allow_html=True)

        r1 = st.columns(4)
        r1[0].metric("Equity", money(totals.get("equity")))
        r1[1].metric("Cash", money(totals.get("cash")))
        r1[2].metric("Gross Exposure", money(totals.get("gross_exposure")))
        r1[3].metric("Net Exposure", money(totals.get("net_exposure")))
        insight_box(insights.get("exposure", ""))

        r2 = st.columns(4)
        r2[0].metric("Annual Volatility", percent(risk.get("annual_volatility")))
        r2[1].metric("VaR", money(risk.get("var_value")), help="Historical one-day VaR at the configured confidence level.")
        r2[2].metric("Expected Shortfall", money(risk.get("expected_shortfall_value")))
        r2[3].metric("Max Drawdown", percent(perf.get("max_drawdown")))

        risk_caption = " ".join(part for part in [insights.get("volatility", ""), insights.get("var", "")] if part)
        insight_box(risk_caption)

        st.markdown("#### Allocation & risk")
        chart1, chart2 = st.columns(2, gap="medium")
        with chart1:
            st.plotly_chart(holdings_donut(analytics), use_container_width=True, config={"displaylogo": False})
            insight_box(insights.get("holdings", ""))
        with chart2:
            st.plotly_chart(risk_contribution_donut(analytics), use_container_width=True, config={"displaylogo": False})
            insight_box(insights.get("risk_contribution", ""))

        st.markdown("#### Historical behaviour")
        actual = analytics.get("actual_performance", {})
        has_dated_history = bool(state.get("accounting_history", {}).get("available"))
        show_actual = False
        if has_dated_history:
            show_actual = st.toggle(
                "View your actual portfolio performance",
                value=False,
                key="show_actual_portfolio_performance",
                help="Switch between the historical simulation of today's holdings and the dated portfolio path reconstructed from supplied purchase dates/prices or transaction executions.",
            )
            if show_actual and actual.get("available"):
                method = str(actual.get("method") or "dated accounting history")
                if method == "reconstructed dated holdings":
                    st.caption("Reconstructed from the supplied purchase dates and quantities using historical market prices and FX. Each current position appears only from its purchase date. Purchase prices remain cost-basis information; position entry is neutralised at its first market mark so it is not mistaken for investment return. Previously sold positions and historical cash movements cannot be inferred from a holdings snapshot.")
                else:
                    st.caption("Reconstructed from the dated transaction ledger and supplied execution prices. External deposits and withdrawals are separated from investment return.")
            elif show_actual:
                st.info(actual.get("reason") or "Dated portfolio information was detected, but the reconstructed performance path is unavailable.")

        chart_analytics = analytics
        if show_actual and actual.get("available"):
            chart_analytics = dict(analytics)
            chart_analytics["series"] = dict(analytics.get("series", {}))
            chart_analytics["series"]["portfolio_path"] = actual.get("portfolio_path", [])

        chart3, chart4 = st.columns(2, gap="medium")
        with chart3:
            if show_actual and actual.get("available"):
                st.plotly_chart(reconstructed_value_figure(actual), use_container_width=True, config={"displaylogo": False})
                summary = actual.get("accounting_summary", {})
                insight_box(
                    f"Reconstructed value of the currently-held positions. Ending value: "
                    f"{number(summary.get('ending_equity'))} {actual.get('base_currency', 'USD')}. "
                    "Purchase dates control when each holding enters the history."
                )
            else:
                st.plotly_chart(performance_figure(chart_analytics), use_container_width=True, config={"displaylogo": False})
            if not (show_actual and actual.get("available")):
                pass
            else:
                insight_box(insights.get("volatility", ""))
        with chart4:
            st.plotly_chart(drawdown_figure(chart_analytics), use_container_width=True, config={"displaylogo": False})
            if show_actual and actual.get("available"):
                insight_box(f"Maximum drawdown on the reconstructed dated portfolio path is {percent(actual.get('metrics', {}).get('max_drawdown'))}.")
            else:
                insight_box(insights.get("drawdown", ""))

        st.markdown("#### Diversification & exposure")
        chart5, chart6 = st.columns([1.15, 0.85], gap="medium")
        with chart5:
            st.plotly_chart(correlation_heatmap(analytics), use_container_width=True, config={"displaylogo": False})
            insight_box(insights.get("correlation", ""))
        with chart6:
            st.plotly_chart(exposure_bar(analytics), use_container_width=True, config={"displaylogo": False})
            insight_box(insights.get("pnl", ""))

        st.markdown("#### Historical combination risk")
        combination_risk = analytics.get("combination_risk", {})
        eligible = combination_risk.get("eligible_tickers", [])
        excluded = combination_risk.get("excluded_tickers", {})

        st.caption(
            "Compares equal-weight groups of securities from this portfolio using their common historical returns. "
            "This tests which assets historically worked together from a risk perspective; it does not optimise weights or forecast future performance."
        )

        if eligible:
            cr_left, cr_right = st.columns([1.35, 0.65], gap="medium")
            with cr_left:
                selected = st.multiselect(
                    "Tickers available to combination analysis",
                    options=eligible,
                    default=eligible,
                    key="combination_risk_tickers",
                    help="All eligible portfolio tickers are selected by default. This selection does not alter the accepted portfolio.",
                )
            with cr_right:
                max_size = max(2, min(6, len(selected)))
                combination_size = st.number_input(
                    "Assets per combination",
                    min_value=2,
                    max_value=max_size,
                    value=2,
                    step=1,
                    key="combination_risk_size",
                )

            if st.button("Analyse combinations", use_container_width=False):
                try:
                    result = calculate_combination_risk(
                        returns_from_analytics(analytics),
                        [row.get("ticker") for row in state.get("positions", [])],
                        combination_size=int(combination_size),
                        selected_tickers=selected,
                        var_level=float(analytics.get("meta", {}).get("var_level", 0.95)),
                    )
                    st.session_state.analytics["combination_risk"] = result
                    combination_risk = result
                except Exception as exc:
                    st.error(str(exc))

            if excluded:
                with st.expander(f"Tickers unavailable for historical combination analysis ({len(excluded)})"):
                    st.dataframe(
                        pd.DataFrame([{"ticker": k, "reason": v} for k, v in excluded.items()]),
                        use_container_width=True,
                        hide_index=True,
                    )

            if combination_risk.get("available"):
                metric_labels = {
                    "Annual volatility": "annual_volatility",
                    "Maximum drawdown": "max_drawdown",
                    "VaR 95%": "var_pct",
                    "Expected Shortfall 95%": "expected_shortfall_pct",
                }
                chosen_label = st.selectbox("Risk measure", list(metric_labels), key="combination_risk_metric")
                metric = metric_labels[chosen_label]
                if int(combination_risk.get("combination_size", 2)) == 2:
                    st.plotly_chart(combination_risk_heatmap(combination_risk, metric), use_container_width=True, config={"displaylogo": False})
                else:
                    st.plotly_chart(combination_risk_ranked(combination_risk, metric), use_container_width=True, config={"displaylogo": False})

                summaries = [
                    ("Lowest volatility", combination_risk.get("lowest_volatility"), "annual_volatility"),
                    ("Smallest drawdown", combination_risk.get("smallest_drawdown"), "max_drawdown"),
                    ("Lowest VaR", combination_risk.get("lowest_var"), "var_pct"),
                    ("Lowest ES", combination_risk.get("lowest_expected_shortfall"), "expected_shortfall_pct"),
                ]
                summary_cols = st.columns(4)
                for col, (label, row, field) in zip(summary_cols, summaries):
                    if row:
                        col.metric(label, percent(row.get(field)), help=row.get("label"))
                        col.caption(row.get("label", ""))

                with st.expander(f"All evaluated combinations ({combination_risk.get('evaluated_count', 0):,})"):
                    result_df = pd.DataFrame(combination_risk.get("results", []))
                    if not result_df.empty:
                        display = result_df[[
                            "label", "observations", "annual_return", "annual_volatility",
                            "max_drawdown", "var_pct", "expected_shortfall_pct"
                        ]].copy()
                        for col in ["annual_return", "annual_volatility", "max_drawdown", "var_pct", "expected_shortfall_pct"]:
                            display[col] = display[col].map(lambda x: f"{float(x)*100:.2f}%" if pd.notna(x) else "—")
                        st.dataframe(display, use_container_width=True, hide_index=True)
            else:
                st.info(combination_risk.get("reason") or "Combination analysis is unavailable for this selection.")
        else:
            st.info("At least two portfolio tickers need sufficient historical return data for combination analysis.")

        st.caption(
            "Risk metrics use historical returns for the current signed book. Ledger uploads additionally use their reconstructed account path for performance where sufficient data exists. Historical combination results and scenarios are not forecasts."
        )


#______________________________________________________________________________
# LATEST SCENARIO DISPLAY
#______________________________________________________________________________

with main_col:
    if st.session_state.latest_scenario is not None:
        scenario = st.session_state.latest_scenario
        st.divider()
        st.subheader("4. Latest Copilot Scenario")
        st.markdown(
            '<div class="pa-scenario-banner"><strong>Hypothetical scenario.</strong> These values are temporary and are kept separate from the accepted portfolio.</div>',
            unsafe_allow_html=True,
        )
        comparison = scenario.get("comparison", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equity Change", money(comparison.get("equity_change")))
        c2.metric("Gross Exposure Change", money(comparison.get("gross_exposure_change")))
        c3.metric(
            "Volatility After",
            percent(comparison.get("annual_volatility_after")),
            delta=(
                f"from {percent(comparison.get('annual_volatility_before'))}"
                if comparison.get("annual_volatility_before") is not None
                else None
            ),
        )
        c4.metric(
            "VaR After",
            money(comparison.get("var_value_after")),
            delta=(
                f"from {money(comparison.get('var_value_before'))}"
                if comparison.get("var_value_before") is not None
                else None
            ),
        )

        scenario_chart1, scenario_chart2 = st.columns(2, gap="medium")
        with scenario_chart1:
            st.plotly_chart(scenario_comparison_bar(scenario), use_container_width=True, config={"displaylogo": False})
        with scenario_chart2:
            st.plotly_chart(scenario_position_change_bar(scenario), use_container_width=True, config={"displaylogo": False})

        with st.expander("Scenario action log"):
            st.json(scenario.get("actions", []))

        with st.expander("Scenario positions"):
            scenario_positions = pd.DataFrame(scenario.get("scenario_state", {}).get("positions", []))
            if not scenario_positions.empty:
                display_cols = [
                    "ticker", "side", "quantity", "average_entry_price", "current_price",
                    "signed_market_value", "exposure", "realised_pnl", "unrealised_pnl",
                ]
                st.dataframe(
                    scenario_positions[[col for col in display_cols if col in scenario_positions.columns]],
                    use_container_width=True,
                    hide_index=True,
                )

        st.info("Resetting the scenario returns the dashboard context to the accepted portfolio. No scenario action is booked into the accepted portfolio.")
