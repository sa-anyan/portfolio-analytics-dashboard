"""Portfolio Analytics v4 Streamlit interface.

The app gathers input and renders outputs. Financial calculations live exclusively
inside the package engines.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

import json
from hashlib import sha256
import os
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
from portfolio_analytics.security.ui import safe_dataframe
import streamlit as st

from portfolio_analytics.ai.copilot import ask_copilot
from portfolio_analytics.security.ai_access import AIUnavailable, policy, identity, authorised_request
from portfolio_analytics.security.demo_quota import remaining, authorised_demo_question, EXHAUSTED
from portfolio_analytics.security.session import synchronise_owner
from portfolio_analytics.analytics.engine import run_analytics, run_account_performance, returns_from_analytics
from portfolio_analytics.analytics.combination_risk import calculate_combination_risk
from portfolio_analytics.core.market_data import fetch_latest_prices
from portfolio_analytics.input_engine.price_history import spreadsheet_price_history
from portfolio_analytics.core.history_listings import fetch_listing_history, listing_rows
from portfolio_analytics.core.fx import fetch_fx_history
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.manual import parse_manual_holdings
from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.input_engine.snapshot import reported_snapshot
from portfolio_analytics.ui.discover import render_discover
from portfolio_analytics.ui.accepted_state import replace_accepted
from portfolio_analytics.ui.trust import render_trust
from portfolio_analytics.ui.consolidation import render_ingestion
from portfolio_analytics.diagnostics.trust import diagnose_trust, TrustPolicy, finite, risk_values_for_display
from portfolio_analytics.ui.insights import fallback_insights
from portfolio_analytics.ui.styles import APP_CSS, HERO_HTML
from portfolio_analytics.ui.chart_guard import render_chart
from portfolio_analytics.ui.visuals import (
    correlation_heatmap,
    combination_risk_heatmap,
    combination_risk_ranked,
    exposure_bar,
    holdings_donut,
    risk_contribution_donut,
    scenario_comparison_bar,
    scenario_position_change_bar,
)


#______________________________________________________________________________
# PAGE SETUP
#______________________________________________________________________________

UI_BUILD = "2026.10.03-premium-dark-2"
st.set_page_config(page_title="Portfolio Analytics v4", layout="wide")
st.markdown(APP_CSS, unsafe_allow_html=True)
if st.session_state.get('workspace') == 'Deep Analytics':
    st.markdown('### Portfolio Analytics')
else:
    st.markdown(HERO_HTML, unsafe_allow_html=True)
st.caption("Session-only workspace: accepted portfolios, upload reviews, ETF data and chat may be lost on refresh, expiry or restart. They are not saved for recovery. Remove uploads before leaving a shared computer; reset is not a secure-erasure guarantee.")


#______________________________________________________________________________
# SESSION STATE
#______________________________________________________________________________

synchronise_owner(st.session_state, st.user)

for key, default in {
    "parsed": None,
    "portfolio_state": None,
    "analytics": None,
    "market_metadata": None,
    "copilot_insights": None,
    "copilot_messages": [],
    "latest_scenario": None,
    "copilot_uses": 0,
    "trust_attempt": None,
    "trust_policy": {},
    "consolidation_batch": None,
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

def money(value: Any, *, compact: bool = False) -> str:
    try:
        if not finite(value):
            return "—"
        amount = float(value)
        base = (st.session_state.get("portfolio_state") or {}).get("meta", {}).get("base_currency", "USD")
        symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(base, base + " ")
        if compact:
            absolute = abs(amount)
            if absolute >= 1_000_000_000:
                return f"{symbol}{amount / 1_000_000_000:,.2f}B"
            if absolute >= 1_000_000:
                return f"{symbol}{amount / 1_000_000:,.2f}M"
            if absolute >= 1_000:
                return f"{symbol}{amount / 1_000:,.1f}K"
        return f"{symbol}{amount:,.2f}"
    except (TypeError, ValueError):
        return "—"


def metric_money(label: str, value: Any, *, help: str | None = None, tone: str = "neutral") -> None:
    """Finance-style KPI: readable full value without Streamlit truncation."""
    try:
        amount = float(value)
        base = (st.session_state.get("portfolio_state") or {}).get("meta", {}).get("base_currency", "USD")
        symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(base, base + " ") if finite(value) else None
    except (TypeError, ValueError):
        amount = None

    base = (st.session_state.get("portfolio_state") or {}).get("meta", {}).get("base_currency", "USD")
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(base, base + " ")
    if amount is None:
        display = "—"
    elif abs(amount) >= 1_000_000_000:
        display = f"{symbol}{amount / 1_000_000_000:,.2f}B"
    elif abs(amount) >= 1_000_000:
        display = f"{symbol}{amount / 1_000_000:,.2f}M"
    else:
        display = f"{symbol}{amount:,.2f}"

    help_attr = f' title="{help}"' if help else ""
    exact = money(amount) if amount is not None else "—"
    st.markdown(
        f"""
        <div class="pa-finance-kpi" data-tone="{tone}"{help_attr}>
            <div class="pa-finance-kpi-label">{label}</div>
            <div class="pa-finance-kpi-value">{display}</div>
            <div class="pa-finance-kpi-exact">{exact}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_percent(label: str, value: Any, *, help: str | None = None, tone: str = "neutral") -> None:
    """Finance-style percentage KPI matching the monetary summary strip."""
    try:
        if not finite(value):
            raise ValueError("Unavailable metric")
        amount = float(value) * 100.0
        display = f"{amount:,.2f}%"
    except (TypeError, ValueError):
        display = "—"

    help_attr = f' title="{help}"' if help else ""
    st.markdown(
        f"""
        <div class="pa-finance-kpi" data-tone="{tone}"{help_attr}>
            <div class="pa-finance-kpi-label">{label}</div>
            <div class="pa-finance-kpi-value">{display}</div>
            <div class="pa-finance-kpi-exact">Portfolio risk metric</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def inline_stat(label: str, value: str, *, tone: str = "neutral", detail: str = "") -> None:
    """Compact finance statistic without Streamlit's oversized metric card."""
    detail_html = f'<div class="pa-inline-stat-detail">{detail}</div>' if detail else ""
    st.markdown(
        f"""
        <div class="pa-inline-stat" data-tone="{tone}">
            <div class="pa-inline-stat-label">{label}</div>
            <div class="pa-inline-stat-value">{value}</div>
            {detail_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def section_header(title: str, eyebrow: str, note: str = "") -> None:
    """High-contrast section divider for fast dashboard scanning."""
    anchor = title.lower().replace(" & ", "-").replace(" ", "-")
    st.markdown(f'<span id="{anchor}"></span>', unsafe_allow_html=True)
    note_html = f'<div class="pa-section-header-note">{note}</div>' if note else ""
    st.markdown(
        f"""
        <div class="pa-section-header">
            <div class="pa-section-header-eyebrow">{eyebrow}</div>
            <div class="pa-section-header-title">{title}</div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_api_key() -> str | None:
    try:
        return st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    except Exception:
        return os.getenv("OPENAI_API_KEY")


# Paid access is enforced at the provider boundary, not by a session counter.


#______________________________________________________________________________
# CACHED MARKET DATA
#______________________________________________________________________________

@st.cache_data(ttl=900, max_entries=64, show_spinner=False)
def cached_latest_prices(tickers: tuple[str, ...]):
    return fetch_latest_prices(list(tickers))


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def cached_price_history(tickers: tuple[str, ...], period: str, listings: tuple = ()):
    return fetch_listing_history(list(tickers), period=period, listings=listings)

@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def cached_snapshot_price_history(tickers: tuple[str, ...], period: str, valuation_date: str, listings: tuple = ()):
    end = pd.Timestamp(valuation_date)
    start = end - pd.DateOffset(years=int(period[:-1]))
    return fetch_listing_history(list(tickers), start=start.date().isoformat(), end=(end + pd.Timedelta(days=1)).date().isoformat(), listings=listings)



@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def cached_dated_price_history(tickers: tuple[str, ...], start_date: str, price_basis: str = "accounting", listings: tuple = ()):
    return fetch_listing_history(list(tickers), start=start_date, price_basis=price_basis, listings=listings)

@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def cached_fx_history(currencies: tuple[str, ...], start_date: str):
    return fetch_fx_history(list(currencies), start=start_date, base="USD")


@st.cache_data(ttl=3600)
def cached_reporting_fx_history(currencies, start_date, base):
    return fetch_fx_history(list(currencies), start=start_date, base=base)


def prepare_account_candidate(batch, declarations, overrides, live, period, policy):
    from portfolio_analytics.input_engine.consolidate import prepare_consolidation, selected_inputs
    view, selected = selected_inputs(batch)
    preliminary = prepare_consolidation(batch, declarations=declarations, latest_prices=overrides, policy=policy)
    if preliminary['parsed'] is None:
        return preliminary
    as_of = pd.Timestamp(declarations['valuation_date'])
    if live and as_of.date() != pd.Timestamp.today().date():
        preliminary.update(ready=False, errors=['Live quotes cannot validate a historical snapshot date. Use aligned current statements or explicitly supplied historical quotes.'])
        return preliminary
    normalised = preliminary['parsed']['normalised_dataset']
    tickers = tuple(preliminary['parsed']['variables']['tickers'])
    latest = dict(preliminary['parsed']['variables']['provided_prices'])
    latest_meta = {'source': 'user supplied', 'as_of': None, 'missing': []}
    fx, history = pd.DataFrame(), pd.DataFrame()
    try:
        if live and tickers:
            market, latest_meta = cached_latest_prices(tickers)
            latest.update(market)
        currencies = tuple(sorted({str(r['data'].get('Currency') or 'USD').upper() for r in selected}))
        dates = [pd.Timestamp(r['data']['Date']) for r in selected if r['data'].get('Date')]
        history, history_meta = cached_price_history(tickers, period) if tickers else (pd.DataFrame(), {})
        history = history.loc[history.index <= as_of] if not history.empty else history
        starts = dates + ([history.index.min()] if not history.empty else []) + [as_of]
        fx, fx_meta = cached_fx_history(currencies, (min(starts) - pd.Timedelta(days=7)).date().isoformat())
        fx = fx.loc[fx.index <= as_of] if not fx.empty else fx
        actual_history, actual_meta = pd.DataFrame(), {}
        if normalised['ledger'] and not normalised['holdings']:
            actual_history, actual_meta = cached_dated_price_history(tickers, min(dates).date().isoformat(), 'accounting')
            if not actual_history.empty:
                actual_history = actual_history.loc[actual_history.index <= as_of]
        candidate = prepare_consolidation(batch, declarations=declarations, latest_prices=latest,
            market_metadata=latest_meta, fx_history=fx, split_history=actual_history.attrs.get('splits'), policy=policy)
        if candidate['ready']:
            state = candidate['state']
            analytics = run_analytics(state, history, fx_history=fx)
            if state['accounting_history']['available']:
                analytics['actual_performance'] = run_account_performance(state, actual_history, fx_history=fx)
            else:
                analytics['actual_performance'] = {'available': False, 'reason': state['accounting_history']['reason']}
            analytics['trust_diagnostics'] = diagnose_trust(state, analytics, parsed=candidate['parsed'],
                market_metadata=latest_meta, fx_history=fx, as_of=as_of, policy=policy)
            candidate['trust'] = analytics['trust_diagnostics']
            candidate['analytics'] = analytics
            candidate['market_metadata'] = {'latest': latest_meta, 'history': {**history_meta, 'period': period, 'actual_performance': actual_meta, 'fx': fx_meta}}
        return candidate
    except Exception as exc:
        preliminary.update(ready=False, errors=[str(exc)])
        preliminary['trust'] = diagnose_trust(None, parsed=preliminary['parsed'], latest_prices=latest,
            market_metadata=latest_meta, fx_history=fx, policy=policy, as_of=as_of, failure=str(exc))
        return preliminary


#______________________________________________________________________________
# MAIN + COPILOT LAYOUT
#______________________________________________________________________________

from portfolio_analytics.ui.demo_portfolios import render_demo_portfolios, dataset as demo_dataset, constituent_bundle
render_demo_portfolios()

trust_policy = TrustPolicy(**st.session_state.trust_policy)
if st.session_state.analytics is not None:
    previous_trust = st.session_state.analytics.get("trust_diagnostics", {})
    st.session_state.analytics["trust_diagnostics"] = diagnose_trust(
        st.session_state.portfolio_state, st.session_state.analytics, parsed=st.session_state.parsed,
        market_metadata=(st.session_state.market_metadata or {}).get("latest", {}),
        fx_snapshot=previous_trust.get("fx_snapshot", {}), policy=trust_policy)
if st.session_state.analytics is not None:
    from portfolio_analytics.analytics.lookthrough import calculate_lookthrough
    from portfolio_analytics.input_engine.constituents import identity_catalog
    exposure_catalog = identity_catalog(st.session_state.portfolio_state, parsed=st.session_state.parsed,
        accepted_batch=st.session_state.get('accepted_consolidation_batch'))
    st.session_state.analytics['lookthrough'] = calculate_lookthrough(st.session_state.portfolio_state,
        st.session_state.get('fund_constituents', {}), exposure_catalog,
        declarations=st.session_state.get('fund_eligibility', {}),
        trust=st.session_state.analytics['trust_diagnostics'],
        max_age_days=st.session_state.get('constituent_max_age', 90))
valuation_ok = st.session_state.analytics is None or st.session_state.analytics.get("trust_diagnostics", {}).get("valuation_status") != "blocked"
workspace = st.radio("Workspace", ["Portfolio dashboard", "Deep Analytics"], horizontal=True, key="workspace", label_visibility="collapsed")
main_col, copilot_col = st.columns([3.25, 1.15], gap="large")
with main_col:
    if st.session_state.trust_attempt:
        render_trust(st.session_state.trust_attempt, rejected=True, controls=st.session_state.analytics is None)
        st.info("The latest submission was rejected. Any accepted portfolio below is the previous portfolio, not the rejected upload.")
    if workspace == "Portfolio dashboard" and st.session_state.analytics and st.session_state.analytics.get("trust_diagnostics"):
        render_trust(st.session_state.analytics["trust_diagnostics"])
if workspace == "Deep Analytics":
    with main_col:
        render_discover(st.session_state.portfolio_state, st.session_state.analytics)
else:
    focus = st.session_state.get("dashboard_focus")
    if focus:
        with main_col:
            targets = {"allocation": ("Allocation & Risk", "allocation-risk"), "risk": ("Risk Snapshot", "risk-snapshot"), "data": ("Parser & Portfolio State", "parser-portfolio-state"), "scenario": ("Latest Scenario", "latest-scenario")}
            if focus in targets:
                label, anchor = targets[focus]
                st.markdown(f"Investigation · [{label}](#{anchor}). Your accepted portfolio is unchanged.")
api_key = get_api_key()

with copilot_col:
    with st.expander("AI Copilot · questions and scenarios", expanded=False):
        st.subheader("AI Copilot")
        st.caption("Investigate your accepted portfolio and hypothetical scenarios.")
        paid_access = False
        demo_access = False
        allowance = st.empty()
        try:
            access_policy = policy()
            demo_access = access_policy.mode == 'public_demo'
            if demo_access:
                questions_left = remaining(st.context.headers)
                allowance.caption(f'{questions_left} of 5 AI questions remaining for this network across the demo. No daily reset. Accepted questions count even if AI fails.')
                st.caption('Quota records use a private keyed network hash, not a stored raw IP. Shared networks share the allowance. Records are deleted within 7 days after the demo ends; analytics remain session-only.')
                if questions_left == 0:
                    raise AIUnavailable(EXHAUSTED)
            else:
                identity(st.user)
            paid_access = bool(api_key)
            if not paid_access:
                st.info("Paid Copilot is unavailable: server credentials are not configured.")
            else:
                st.caption("Authorised access · shared server request/token controls apply to every provider attempt.")
        except AIUnavailable as exc:
            st.info(str(exc))
            if os.getenv('AI_ACCESS_POLICY') == 'controlled_oidc' and not getattr(st.user, 'is_logged_in', False):
                if st.button("Sign in for controlled Copilot access"):
                    try:
                        st.login()
                    except Exception:
                        st.error("Server sign-in is unavailable; ask the operator to verify OIDC configuration.")
        ai_consent = st.checkbox("Allow this question and portfolio evidence to be sent to OpenAI", disabled=not paid_access, key='ai_submission_consent')
        st.caption("AI submission can include holdings, quantities, values, risk metrics and recent chat. Raw upload tables and provenance are excluded; identifiers in submitted prose may still be sent. No AI calls occur during upload or navigation.")
        if getattr(st.user, 'is_logged_in', False) and st.button('Sign out and reset session'):
            st.session_state.clear()
            st.logout()

        auto_ai_insights = st.toggle(
            "Automatic Copilot captions",
            value=False,
            disabled=True,
            help="Disabled in the public demo so the AI allowance is reserved for questions you choose to ask.",
        )

        if st.session_state.portfolio_state is not None:
            for message in st.session_state.copilot_messages[-8:]:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

            question = st.text_area(
                "Ask anything about the portfolio",
                placeholder=(
                    "Examples:\n"
                    "Which holdings have the largest weights?\n"
                    "Why is my VaR high?\n"
                    "What caused my drawdown in 2020?\n"
                    "Which ticker contributed most to that drawdown?\n"
                    "What if NVDA falls 25%?\n"
                    "What if rates rise 100 bps and I halve NVDA?\n"
                    "Target 10% annual volatility."
                ),
                height=135,
                max_chars=1000,
                key="copilot_question",
            )

            if not valuation_ok:
                st.warning("Refresh valuation inputs before asking Copilot or running scenarios.")
            if st.button("Ask Copilot", type="primary", use_container_width=True, disabled=not paid_access or not ai_consent or not valuation_ok):
                if not api_key:
                    st.error("Copilot needs an OpenAI API key.")
                elif question.strip():
                    st.session_state.copilot_messages = st.session_state.copilot_messages[-10:]
                    st.session_state.copilot_messages.append({"role": "user", "content": question})
                    try:
                        authorisation = (authorised_demo_question(st.context.headers, ai_consent, question)
                                         if demo_access else authorised_request(st.user, ai_consent))
                        with authorisation, st.spinner("Copilot is reading the portfolio engines..."):
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
                        st.session_state.copilot_uses += 1
                        if response.get("scenario") is not None:
                            st.session_state.latest_scenario = response["scenario"]
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc) if isinstance(exc, AIUnavailable) else "Copilot could not complete that request. No portfolio changes were accepted.")
                        if demo_access:
                            try:
                                allowance.caption(f'{remaining(st.context.headers)} of 5 AI questions remaining for this network across the demo. No daily reset.')
                            except AIUnavailable:
                                allowance.caption('AI allowance is unavailable; requests are paused.')

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



#______________________________________________________________________________
# INPUT ENGINE
#______________________________________________________________________________

with main_col:
    if workspace == "Portfolio dashboard":
        st.subheader("1. Build Your Portfolio")
        source_type = st.radio(
            "Input method",
            ["Upload file", "Manual entry", "Multiple account files"],
            horizontal=True, key="input_method",
        )

        st.caption("Use the listing's quote currency (GBX for pence). Holdings snapshots use current shares and cost per current share; ledger rows use original execution quantities/prices. Additional cash uses the reporting currency: USD normally, GBP for a validated GBP statement.")

        settings_col1, settings_col2, settings_col3 = st.columns(3)
        with settings_col1:
            starting_cash = st.number_input("Starting / current cash", value=0.0, step=1000.0, key="portfolio_starting_cash")
        with settings_col2:
            history_period = st.selectbox("Historical window", ["1y", "3y", "5y", "10y"], index=1)
        with settings_col3:
            use_live_prices = st.toggle("Use live market prices", value=True, key="use_live_prices")

        use_sheet_history = st.checkbox("Use spreadsheet price history", value=False, key="use_spreadsheet_history") if source_type != "Multiple account files" else False
        history_upload = None
        if use_sheet_history:
            st.caption("Upload dated daily total-return adjusted prices: Date, Ticker, Adjusted Close. Prices must use each holding’s trading currency/units. Current quotes and purchase dates are not price history. FX history still comes from Yahoo Finance.")
            history_upload = st.file_uploader("Spreadsheet price history", type=["csv", "xlsx"], key="history_upload")
        else:
            st.caption("Historical risk and instrument charts use Yahoo Finance adjusted prices over the selected window. Supplied snapshot values remain separate; this models today’s holdings, not actual past account performance.")
        uploaded = None
        manual_frame = None

        if source_type == "Upload file":
            uploaded = st.file_uploader(
                "Upload CSV or Excel portfolio",
                type=["csv", "xlsx"],
                help="The parser classifies holdings vs ledger and normalises it before anything reaches Portfolio State.",
            )
        elif source_type == "Multiple account files":
            render_ingestion(prepare_account_candidate, use_live_prices=use_live_prices, history_period=history_period, policy=trust_policy)
        else:
            st.caption("Enter holdings directly. Positive quantity = long; negative quantity = short. Entry price and current price are optional when live valuation is enabled.")
            if "Currency" not in st.session_state.manual_rows:
                st.session_state.manual_rows["Currency"] = "USD"
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
                    "Currency": st.column_config.TextColumn(help="Quote currency, e.g. USD, EUR, GBP or GBX (pence)."),
                    "Asset Class": st.column_config.TextColumn(),
                    "Duration": st.column_config.NumberColumn(help="Optional modified duration for rate scenarios."),
                    "Rate Sensitivity": st.column_config.NumberColumn(help="Optional % price change for a +100 bp rate move."),
                },
                key="manual_editor",
            )
            st.session_state.manual_rows = manual_frame

        demo_request = st.session_state.pop('demo_request', None)
        if demo_request:
            demo_source = demo_dataset(demo_request['portfolio'])
            uploaded = BytesIO(demo_source['data'])
            uploaded.name = demo_source['filename']

        snapshot_date = None
        upload_fingerprint = None
        supplied_snapshot = False
        if uploaded is not None and hasattr(uploaded, 'getvalue'):
            try:
                upload_fingerprint = sha256(uploaded.getvalue()).hexdigest()
                if st.session_state.get('_upload_preview_fingerprint') != upload_fingerprint:
                    st.session_state['_upload_preview_fingerprint'] = upload_fingerprint
                    st.session_state.pop('snapshot_valuation_date', None)
                preview = parse_upload(uploaded.getvalue(), uploaded.name)
                accepted_source = (st.session_state.get('parsed') or {}).get('source', {})
                if st.session_state.get('portfolio_state') and accepted_source.get('fingerprint') != upload_fingerprint:
                    st.caption('Replacement selected. The analysis below remains the previous accepted portfolio until Parse & Analyse succeeds.')
                supplied_snapshot = preview['classification'] == 'holdings' and 'Market Value (GBP)' in preview['user_dataset']['columns']
            except ValueError:
                pass  # The submission path reports the validation failure.
        if supplied_snapshot:
            st.caption('GBP statement detected. Supplied quotes, FX and cost basis take precedence after reconciliation; historical performance is separate.')
            snapshot_date = st.date_input('Statement valuation date', value=None, key='snapshot_valuation_date')
            st.caption('Use the date of the statement prices and FX, not a purchase date. An undated statement cannot establish fresh currency conversion evidence.')

        analyse = st.button("Parse & Analyse Portfolio", type="primary", disabled=source_type == "Multiple account files")

        if analyse or demo_request:
            st.session_state.trust_attempt = None
            parsed, latest_prices, latest_meta, fx_history = None, {}, {}, pd.DataFrame()
            attempt_trust = None
            try:
                with st.spinner("Parsing and normalising portfolio..."):
                    if source_type == "Upload file":
                        if uploaded is None:
                            raise ValueError("Upload a portfolio file first.")
                        parsed = parse_upload(uploaded.getvalue(), uploaded.name, starting_cash=starting_cash)
                        parsed["source"]["fingerprint"] = sha256(uploaded.getvalue()).hexdigest()
                    else:
                        parsed = parse_manual_holdings(manual_frame, starting_cash=starting_cash)

                snapshot = reported_snapshot(parsed, snapshot_date) if supplied_snapshot else None
                if supplied_snapshot and snapshot_date is None:
                    raise ValueError('Choose the statement valuation date before accepting the GBP snapshot.')
                if snapshot:
                    parsed, supplied_fx = snapshot
                reporting_currency = 'GBP' if snapshot else 'USD'
                tickers = tuple(parsed.get("variables", {}).get("tickers", []))
                # CASH is a reserved non-market asset. Never send it to a market-data
                # provider where the symbol may resolve to an unrelated security.
                market_tickers = tuple(t for t in tickers if str(t).strip().upper() != "CASH")
                supplied_prices = parsed.get("variables", {}).get("provided_prices", {})

                latest_prices: dict[str, float] = dict(supplied_prices)
                latest_meta: dict[str, Any] = {"source": "user supplied", "as_of": snapshot_date.isoformat() if snapshot else None, "missing": []}

                if snapshot:
                    latest_meta['observations'] = {t: {'as_of': snapshot_date.isoformat(), 'source': 'User-supplied statement; valuation date declared by user'} for t in market_tickers}

                if use_live_prices and market_tickers and not snapshot:
                    with st.spinner("Fetching current market prices..."):
                        try:
                            market_prices, latest_meta = cached_latest_prices(market_tickers)
                        except Exception:
                            market_prices = {}
                            latest_meta = {"source": "user supplied; live quote provider unavailable", "as_of": None, "missing": [t for t in market_tickers if t not in supplied_prices]}
                    latest_prices.update(market_prices)

                with st.spinner("Fetching market and currency history..."):
                    try:
                        if use_sheet_history:
                            if history_upload is None:
                                raise ValueError("Upload dated spreadsheet price history or uncheck the spreadsheet option.")
                            history, history_meta = spreadsheet_price_history(history_upload.getvalue(), history_upload.name, market_tickers, history_period, as_of=snapshot_date if snapshot else None)
                        else:
                            if snapshot:
                                history, history_meta = cached_snapshot_price_history(market_tickers, history_period, snapshot_date.isoformat(), listing_rows(parsed.get("user_dataset",{}).get("records",[])))
                            else:
                                history, history_meta = cached_price_history(market_tickers, history_period, listing_rows(parsed.get("user_dataset",{}).get("records",[])))
                    except Exception:
                        if use_sheet_history:
                            raise
                        history, history_meta = pd.DataFrame(), {"source": "Yahoo Finance unavailable", "reason": "Price-history provider unavailable"}
                    normalised = parsed.get("normalised_dataset", {})
                    records = normalised.get("holdings", []) + normalised.get("ledger", []) + normalised.get("cashflows", [])
                    currencies = tuple(sorted({str(row.get("Currency") or "USD").upper() for row in records}))
                    dated_values = [pd.to_datetime(row.get("Date", row.get("Purchase Date")), errors="coerce") for row in records]
                    dated_values = [d for d in dated_values if pd.notna(d)]
                    starts = dated_values + ([history.index.min()] if not history.empty else [])
                    earliest = min(starts) if starts else pd.Timestamp.today()
                    # Include prior FX observations for holidays/weekend acquisitions.
                    fx_start = (earliest - pd.Timedelta(days=7)).date().isoformat()
                    if snapshot:
                        try:
                            fx_history, fx_meta = cached_reporting_fx_history(currencies, fx_start, reporting_currency)
                        except Exception:
                            fx_history, fx_meta = pd.DataFrame(), {'source': 'unavailable', 'reason': 'Historical FX unavailable'}
                        # Dated statement observations replace same-date provider rates only.
                        if not fx_history.empty:
                            fx_history = fx_history.loc[fx_history.index <= pd.Timestamp(snapshot_date)]
                        fx_history = pd.concat([fx_history, supplied_fx]).groupby(level=0).last().sort_index()
                        history = history.loc[history.index <= pd.Timestamp(snapshot_date)] if not history.empty else history
                    else:
                        fx_history, fx_meta = cached_fx_history(currencies, fx_start)
                    actual_history, actual_meta = pd.DataFrame(), {}
                    if dated_values and not snapshot:
                        price_basis = "accounting" if parsed["classification"] == "ledger" else "split_adjusted_close"
                        try:
                            actual_history, actual_meta = cached_dated_price_history(market_tickers, min(dated_values).date().isoformat(), price_basis, listing_rows(parsed.get("user_dataset",{}).get("records",[])))
                        except Exception:
                            actual_history, actual_meta = pd.DataFrame(), {"source": "unavailable", "reason": "Dated price history unavailable"}
                    state = build_portfolio_state(
                        parsed, latest_prices=latest_prices, market_metadata=latest_meta,
                        fx_history=fx_history, split_history=actual_history.attrs.get("splits"), base_currency=reporting_currency,
                    )
                    if snapshot:
                        expected_equity = parsed['source']['snapshot_validation']['supplied_total'] + starting_cash
                        if abs(state['totals']['equity'] - expected_equity) > .02:
                            raise ValueError('Canonical valuation does not reconcile to the supplied snapshot; acceptance withheld.')
                    attempt_trust = diagnose_trust(state, parsed=parsed, market_metadata=latest_meta,
                                                   fx_history=fx_history, policy=trust_policy)
                    if attempt_trust["valuation_status"] == "blocked":
                        raise ValueError("Portfolio acceptance blocked by currency/valuation trust diagnostics. Expand Trust evidence for affected holdings and dates.")
                    analytics = run_analytics(state, history, fx_history=fx_history)
                    if state.get("accounting_history", {}).get("available"):
                        analytics["actual_performance"] = run_account_performance(state, actual_history, fx_history=fx_history)
                    analytics["trust_diagnostics"] = diagnose_trust(state, analytics, parsed=parsed,
                        market_metadata=latest_meta, fx_snapshot=attempt_trust["fx_snapshot"], policy=trust_policy)
                    history_meta = {**history_meta, "period": history_period, "actual_performance": actual_meta, "fx": fx_meta}
                    coverage = analytics.get("meta", {}).get("coverage", {})
                    if not coverage.get("available", True):
                        st.warning("Portfolio risk is unavailable: " + str(coverage.get("reason")))
                    for warning in state.get("meta", {}).get("warnings", []):
                        st.warning(warning)

                demo_store, demo_declarations = {}, {}
                if demo_request and demo_request['parents']:
                    demo_store, demo_declarations = constituent_bundle(state, parsed, demo_request['parents'],
                        maximum=st.session_state.get('constituent_max_age', 90))
                replace_accepted(st.session_state, parsed, state, analytics,
                                 {"latest": latest_meta, "history": history_meta})
                st.session_state.pop('demo_notice', None)
                if demo_request:
                    st.session_state['demo_notice'] = ('success', 'Loaded ' + demo_source['name'] +
                        (' with SPY + QQQ partial constituents.' if demo_request['parents'] else '.') +
                        ' Synthetic inputs; Yahoo history is used when available.')
                    st.session_state.update(fund_constituents=demo_store, fund_eligibility=demo_declarations)
                    st.session_state['overview_history_preferences'] = {
                        'mode': 'Actual Portfolio History' if parsed['classification'] == 'ledger' else "Simulate Today's Holdings",
                        'period': history_period}

                # The Copilot column is rendered before the input engine on each
                # Streamlit run. Re-run once after a successful first analysis so
                # the newly-created Portfolio State is immediately available to
                # Copilot without requiring a second click or upload.
                st.rerun()

            except Exception as exc:
                if parsed is not None:
                    if attempt_trust and attempt_trust["valuation_status"] == "blocked":
                        st.session_state.trust_attempt = attempt_trust
                    else:
                        st.session_state.trust_attempt = diagnose_trust(
                            state if attempt_trust else None, parsed=parsed, latest_prices=latest_prices,
                            market_metadata=latest_meta, fx_history=fx_history, policy=trust_policy, failure=str(exc))
                    st.rerun()
                st.error(str(exc))


#______________________________________________________________________________
# PARSER + PORTFOLIO STATE REVIEW
#______________________________________________________________________________

with main_col:
    if workspace == "Portfolio dashboard":
        if st.session_state.parsed is not None and valuation_ok:
            parsed = st.session_state.parsed
            state = st.session_state.portfolio_state
            analytics = st.session_state.analytics
            insights = (fallback_insights(state, analytics) if analytics.get("trust_diagnostics", {}).get("risk_status") == "unavailable"
                        else st.session_state.copilot_insights or fallback_insights(state, analytics))

            st.divider()
            section_header(
                "Parser & Portfolio State",
                "DATA QUALITY & ENGINE INPUT",
                "Review what the engine detected, data confidence and the accepted portfolio state before analysis.",
            )
            a, b, c, d = st.columns(4)
            with a:
                inline_stat("Detected path", str(parsed.get("classification", "")).title(), tone="gold")
            with b:
                inline_stat("Parser confidence", percent(parsed.get("confidence")), tone="positive")
            with c:
                inline_stat("Open positions", number(state.get("totals", {}).get("position_count"), 0), tone="neutral")
            with d:
                missing_count = len(state.get("meta", {}).get("missing_prices", []))
                inline_stat("Missing prices", number(missing_count, 0), tone="positive" if missing_count == 0 else "risk")

            if parsed.get("issues"):
                with st.expander(f"Parser review items ({len(parsed['issues'])})"):
                    safe_dataframe(pd.DataFrame(parsed["issues"]), use_container_width=True, hide_index=True)

            with st.expander("Normalised input used by the engine"):
                normalised = parsed.get("normalised_dataset", {})
                if parsed.get("classification") in {"holdings", "consolidated"} and normalised.get("holdings"):
                    st.markdown("**Holdings snapshots**")
                    safe_dataframe(pd.DataFrame(normalised.get("holdings", [])), use_container_width=True, hide_index=True)
                if parsed.get("classification") != "holdings":
                    st.markdown("**Trades**")
                    safe_dataframe(pd.DataFrame(normalised.get("ledger", [])), use_container_width=True, hide_index=True)
                    if normalised.get("cashflows"):
                        st.markdown("**Cash flows**")
                        safe_dataframe(pd.DataFrame(normalised.get("cashflows", [])), use_container_width=True, hide_index=True)

            positions = pd.DataFrame(state.get("positions", []))
            if not positions.empty:
                with st.expander(
                    f"Current Portfolio State · {len(positions)} positions",
                    expanded=False,
                ):
                    st.caption(
                        "Accepted positions used by the analytics engine. "
                        "Expand only when you want to inspect position-level detail."
                    )
                    display_cols = [
                        "ticker", "side", "quantity", "average_entry_price", "current_price",
                        "signed_market_value", "exposure", "realised_pnl", "unrealised_pnl",
                    ]
                    safe_dataframe(
                        positions[[col for col in display_cols if col in positions.columns]],
                        use_container_width=True,
                        hide_index=True,
                    )


#______________________________________________________________________________
# ANALYTICS DASHBOARD
#______________________________________________________________________________

with main_col:
    if workspace == "Portfolio dashboard":
        if st.session_state.analytics is not None and valuation_ok:
            state = st.session_state.portfolio_state
            analytics = st.session_state.analytics
            insights = (fallback_insights(state, analytics) if analytics.get("trust_diagnostics", {}).get("risk_status") == "unavailable"
                        else st.session_state.copilot_insights or fallback_insights(state, analytics))
            totals = state.get("totals", {})
            risk = risk_values_for_display(analytics)
            perf = analytics.get("performance", {})

            st.divider()
            st.subheader("3. Portfolio Analytics")
            st.caption(f"Monetary values and scenario prices: {state.get('meta', {}).get('base_currency', 'USD')}. GBX quotes denote pence. Historical simulations are separate from snapshot valuation.")
            st.markdown('<div class="pa-section-note">Accepted portfolio · deterministic analytics from the current Portfolio State.</div>', unsafe_allow_html=True)

            st.markdown('<div class="pa-kpi-group-label">PORTFOLIO SUMMARY</div>', unsafe_allow_html=True)
            r1 = st.columns(4)
            with r1[0]:
                metric_money("Equity", totals.get("equity"), tone="positive")
            with r1[1]:
                metric_money("Cash", totals.get("cash"), tone="liquidity")
            with r1[2]:
                metric_money("Gross Exposure", totals.get("gross_exposure"), tone="exposure")
            with r1[3]:
                metric_money("Net Exposure", totals.get("net_exposure"), tone="exposure")
            insight_box(insights.get("exposure", ""))

            section_header(
                "Risk Snapshot",
                "RISK & DOWNSIDE",
                "VaR and Expected Shortfall are 1-business-day historical measures at 95% confidence, in the reporting currency. Volatility uses 252 business-day observations/year; max drawdown covers the selected history. Current-weight simulation assumes daily rebalancing and zero base-cash interest.",
            )
            coverage = analytics.get("meta", {}).get("coverage", {})
            if not coverage.get("available", True):
                st.warning("Full-portfolio risk withheld: " + str(coverage.get("reason")))
            st.caption(f"Base currency: {state['meta']['base_currency']} · Common observations: {coverage.get('common_observations', 0)} · Tail observations: {risk.get('tail_observations', 0)} · Risk-free rate: {percent(analytics.get('meta', {}).get('risk_free_rate', 0))}.")
            if risk.get("tail_observations", 0) < 20:
                st.caption("The historical tail sample is small; VaR and ES estimates have substantial sampling uncertainty.")
            r2 = st.columns(4)
            with r2[0]:
                metric_percent("Annualised Volatility", risk.get("annual_volatility"), help="Annualised from daily historical returns using 252 trading days.", tone="risk")
            with r2[1]:
                metric_money("1-Day VaR · 95%", risk.get("var_value"), help="Historical one-day Value at Risk at 95% confidence, calculated from daily portfolio returns.", tone="risk")
            with r2[2]:
                metric_money("1-Day Expected Shortfall · 95%", risk.get("expected_shortfall_value"), help="Average one-day loss in the historical tail beyond the 95% VaR threshold.", tone="risk")
            with r2[3]:
                metric_percent("Max Drawdown · History", perf.get("max_drawdown"), help="Largest peak-to-trough decline across the selected historical window.", tone="risk")

            risk_caption = " ".join(part for part in [insights.get("volatility", ""), insights.get("var", "")] if part)
            insight_box(risk_caption)

            section_header("Allocation & Risk", "PORTFOLIO STRUCTURE", "See where capital is concentrated and which holdings contribute most to portfolio risk.")
            chart1, chart2 = st.columns(2, gap="medium")
            with chart1:
                render_chart(holdings_donut(analytics), use_container_width=True, config={"displaylogo": False})
                insight_box(insights.get("holdings", ""))
            with chart2:
                render_chart(risk_contribution_donut(analytics), use_container_width=True, config={"displaylogo": False})
                st.caption("Slices show absolute normalised contributions. A negative signed contribution is a hedge; see the table.")
                if analytics.get("risk_contribution"):
                    safe_dataframe(pd.DataFrame(analytics["risk_contribution"])[["ticker", "risk_contribution_pct", "absolute_risk_share"]], hide_index=True, use_container_width=True)
                insight_box(insights.get("risk_contribution", ""))

            section_header("Historical Behaviour", "PERFORMANCE THROUGH TIME", "Portfolio growth and peak-to-trough drawdown across the selected historical window.")
            from portfolio_analytics.ui.history_workspace import render_history_workspace
            render_history_workspace(state, analytics)

            section_header("Diversification & Exposure", "PORTFOLIO RELATIONSHIPS", "Correlation and signed exposure show how positions interact and where concentration can build.")
            chart5, chart6 = st.columns([1.15, 0.85], gap="medium")
            with chart5:
                render_chart(correlation_heatmap(analytics), use_container_width=True, config={"displaylogo": False})
                insight_box(insights.get("correlation", ""))
            with chart6:
                render_chart(exposure_bar(analytics), use_container_width=True, config={"displaylogo": False})
                insight_box(insights.get("pnl", ""))

            section_header("Historical Combination Risk", "SECURITY COMBINATIONS", "Compare equal-weight groups using historical daily returns. These are historical risk diagnostics, not forecasts.")
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
                        safe_dataframe(
                            pd.DataFrame([{"ticker": k, "reason": v} for k, v in excluded.items()]),
                            use_container_width=True,
                            hide_index=True,
                        )

                if combination_risk.get("available"):
                    st.caption(f"All combinations share {combination_risk.get('common_observations', 0)} observations, from {combination_risk.get('common_history_start', '')} to {combination_risk.get('common_history_end', '')}.")
                    metric_labels = {
                        "Annual volatility": "annual_volatility",
                        "Maximum drawdown": "max_drawdown",
                        "VaR 95%": "var_pct",
                        "Expected Shortfall 95%": "expected_shortfall_pct",
                    }
                    chosen_label = st.selectbox("Risk measure", list(metric_labels), key="combination_risk_metric")
                    metric = metric_labels[chosen_label]
                    if int(combination_risk.get("combination_size", 2)) == 2:
                        render_chart(combination_risk_heatmap(combination_risk, metric), use_container_width=True, config={"displaylogo": False})
                    else:
                        render_chart(combination_risk_ranked(combination_risk, metric), use_container_width=True, config={"displaylogo": False})

                    summaries = [
                        ("Lowest volatility", combination_risk.get("lowest_volatility"), "annual_volatility"),
                        ("Smallest drawdown", combination_risk.get("smallest_drawdown"), "max_drawdown"),
                        ("Lowest VaR", combination_risk.get("lowest_var"), "var_pct"),
                        ("Lowest ES", combination_risk.get("lowest_expected_shortfall"), "expected_shortfall_pct"),
                    ]
                    summary_cols = st.columns(4)
                    for col, (label, row, field) in zip(summary_cols, summaries):
                        if row:
                            horizon = {
                                "annual_volatility": "Annualised · daily returns",
                                "max_drawdown": "Selected history",
                                "var_pct": "1-day · 95%",
                                "expected_shortfall_pct": "1-day · 95%",
                            }.get(field, "")
                            with col:
                                inline_stat(
                                    label,
                                    percent(row.get(field)),
                                    tone="risk",
                                    detail=f"{row.get('label', '')} · {horizon}" if horizon else row.get("label", ""),
                                )

                    with st.expander(f"All evaluated combinations ({combination_risk.get('evaluated_count', 0):,})"):
                        result_df = pd.DataFrame(combination_risk.get("results", []))
                        if not result_df.empty:
                            display = result_df[[
                                "label", "observations", "annual_return", "geometric_annual_return", "annual_volatility",
                                "max_drawdown", "var_pct", "expected_shortfall_pct"
                            ]].copy()
                            for col in ["annual_return", "geometric_annual_return", "annual_volatility", "max_drawdown", "var_pct", "expected_shortfall_pct"]:
                                display[col] = display[col].map(lambda x: f"{float(x)*100:.2f}%" if pd.notna(x) else "—")
                            display = display.rename(columns={"annual_return": "Arithmetic annualised mean", "geometric_annual_return": "Geometric annualised return"})
                            safe_dataframe(display, use_container_width=True, hide_index=True)
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
    if workspace == "Portfolio dashboard":
        if st.session_state.latest_scenario is not None and valuation_ok:
            scenario = st.session_state.latest_scenario
            st.divider()
            st.markdown('<span id="latest-scenario"></span>', unsafe_allow_html=True)
            st.subheader("4. Latest Scenario")
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
                render_chart(scenario_comparison_bar(scenario), use_container_width=True, config={"displaylogo": False})
            with scenario_chart2:
                render_chart(scenario_position_change_bar(scenario), use_container_width=True, config={"displaylogo": False})

            with st.expander("Scenario action log"):
                st.json(scenario.get("actions", []))

            with st.expander("Scenario positions"):
                scenario_positions = pd.DataFrame(scenario.get("scenario_state", {}).get("positions", []))
                if not scenario_positions.empty:
                    display_cols = [
                        "ticker", "side", "quantity", "average_entry_price", "current_price",
                        "signed_market_value", "exposure", "realised_pnl", "unrealised_pnl",
                    ]
                    safe_dataframe(
                        scenario_positions[[col for col in display_cols if col in scenario_positions.columns]],
                        use_container_width=True,
                        hide_index=True,
                    )

            st.info("Resetting the scenario returns the dashboard context to the accepted portfolio. No scenario action is booked into the accepted portfolio.")
