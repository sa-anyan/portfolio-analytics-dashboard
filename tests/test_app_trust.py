"""Persona acceptance through Streamlit and the real upload/accounting pipeline."""
import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio_analytics.core import fx, market_data
from portfolio_analytics.diagnostics import trust

APP = str(Path(__file__).resolve().parents[1] / "app.py")
ASOF = "2026-10-09"


def button(app, name):
    return next(b for b in app.button if b.label == name)


def discover(app):
    next(r for r in app.radio if r.label == "Workspace").set_value("Deep Analytics").run()
    next(r for r in app.radio if r.label == "Deep Analytics view").set_value("Discover").run()
    assert not app.exception, [e.message for e in app.exception]


def setup(monkeypatch, *, missing_fx=False, stale_fx=False, n=60, unpriced=False, usd_only=False):
    dates = pd.bdate_range(end=ASOF, periods=n+1)
    frame = pd.DataFrame({"Ticker": ["US", "UK", "EU", "PENCE", "CASH"],
                          "Quantity": [1, 1, -1, 1, 20], "Current Price": [100, 100, 40, 500, 1],
                          "Currency": ["USD", "GBP", "EUR", "GBX", "EUR"]})
    if usd_only:
        frame = frame.iloc[:2].copy()
        frame["Currency"] = "USD"
    if unpriced:
        frame.loc[frame.Ticker == "UK", "Current Price"] = None
    holder = {"frame": frame, "fx_end": pd.Timestamp(ASOF)-pd.Timedelta(days=10) if stale_fx else pd.Timestamp(ASOF)}
    monkeypatch.setattr(trust, "today", lambda: pd.Timestamp(ASOF))
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: SimpleNamespace(name="trust.csv", getvalue=lambda: holder["frame"].to_csv(index=False).encode()))
    calls = {"history": 0, "latest": 0, "fx": 0}
    def history(tickers, **kwargs):
        calls["history"] += 1
        result = pd.DataFrame({t: np.cumprod(1+.01*np.sin(np.arange(n+1)+i)) for i, t in enumerate(tickers)}, index=dates)
        result.attrs.update(price_basis=kwargs.get("price_basis", "total_return"), splits=pd.DataFrame())
        return result, {"source": "test"}
    def latest(tickers):
        calls["latest"] += 1
        prices = {r["Ticker"]: float(r["Current Price"]) for r in holder["frame"].to_dict("records") if pd.notna(r["Current Price"]) and r["Ticker"] in tickers}
        return prices, {"source": "test", "observations": {t: {"as_of": ASOF, "source": "test"} for t in prices}}
    def currencies(*args, **kwargs):
        calls["fx"] += 1
        result = pd.DataFrame({"GBP": 1.25, **({} if missing_fx else {"EUR": 1.1})}, index=pd.bdate_range(end=holder["fx_end"], periods=n+1))
        return result, {}  # Dates in existing source data suffice without an FX metadata blob.
    monkeypatch.setattr(market_data, "fetch_price_history", history)
    monkeypatch.setattr(market_data, "fetch_latest_prices", latest)
    monkeypatch.setattr(fx, "fetch_fx_history", currencies)
    st.cache_data.clear()
    app = AppTest.from_file(APP).run()
    button(app, "Parse & Analyse Portfolio").click().run(timeout=20)
    assert not app.exception, [e.message for e in app.exception]
    return app, holder, calls


@pytest.mark.parametrize("kind", ["missing", "stale"])
def test_oliver_rejected_mixed_fx_shows_evidence_and_no_aggregate(monkeypatch, kind):
    app, _, _ = setup(monkeypatch, missing_fx=kind == "missing", stale_fx=kind == "stale")
    assert app.session_state["portfolio_state"] is None
    d = app.session_state["trust_attempt"]
    assert d["valuation_status"] == "blocked"
    assert any(i["code"] == ("fx_missing_invalid" if kind == "missing" else "fx_stale") for i in d["issues"])
    discover(app)
    assert any("Affected portfolio totals are withheld" in e.value for e in app.error)
    assert any("Data Quality / Trust" in m.value for m in app.markdown)
    assert any("EU" in str(table.value) for table in app.dataframe)
    assert not any('class="pa-finance-kpi-value"' in m.value for m in app.markdown)


def test_oliver_valid_mixed_long_short_gbx_foreign_cash_and_policy_survive_navigation(monkeypatch):
    app, _, calls = setup(monkeypatch)
    assert not app.error
    state = copy.deepcopy(app.session_state["portfolio_state"])
    assert state["totals"]["equity"] == pytest.approx(209.25)
    fetched = calls.copy()
    discover(app)
    assert app.session_state["analytics"]["trust_diagnostics"]["valuation_status"] == "checked"
    next(i for i in app.number_input if i.label == "Maximum FX age (calendar days)").set_value(7).run()
    next(r for r in app.radio if r.label == "Workspace").set_value("Portfolio dashboard").run()
    discover(app)
    assert app.session_state["analytics"]["trust_diagnostics"]["policy"]["max_fx_age_days"] == 7
    assert app.session_state["portfolio_state"] == state and calls == fetched


def test_james_unpriced_asset_identified_with_affected_calculations(monkeypatch):
    app, _, _ = setup(monkeypatch, unpriced=True)
    assert app.session_state["portfolio_state"] is None
    discover(app)
    d = app.session_state["trust_attempt"]
    issue = next(i for i in d["issues"] if i["code"] == "price_missing")
    assert issue["holdings"] == ["UK"]
    assert {"valuation", "allocation", "risk", "performance", "scenarios"}.issubset(issue["affects"])
    assert any("UK: security price is missing" in w.value for w in app.warning)
    assert not any(b.label == "Run hypothetical shock" for b in app.button)


def test_rejected_replacement_retains_and_labels_previous_accepted_portfolio(monkeypatch):
    app, holder, _ = setup(monkeypatch)
    state = copy.deepcopy(app.session_state["portfolio_state"])
    holder["frame"].loc[holder["frame"].Ticker == "UK", "Current Price"] = None
    st.cache_data.clear()
    button(app, "Parse & Analyse Portfolio").click().run(timeout=20)
    assert not app.exception
    assert app.session_state["portfolio_state"] == state
    discover(app)
    assert any("previous portfolio, not the rejected upload" in i.value for i in app.info)
    assert app.session_state["trust_attempt"]["valuation_status"] == "blocked"


def test_priya_short_sample_and_invalid_metric_never_appear_as_zero_risk(monkeypatch):
    app, _, _ = setup(monkeypatch, n=12, usd_only=True)
    original = copy.deepcopy(app.session_state["analytics"]["risk"])
    discover(app)
    d = app.session_state["analytics"]["trust_diagnostics"]
    assert d["risk_sample"]["common_observations"] == 12
    assert any("Only 12 common historical observations" in w.value for w in app.warning)
    assert app.session_state["analytics"]["risk"] == original
    app.session_state["analytics"]["risk"]["annual_volatility"] = float("nan")
    next(r for r in app.radio if r.label == "Workspace").set_value("Portfolio dashboard").run()
    assert not app.exception
    assert app.session_state["analytics"]["trust_diagnostics"]["risk_status"] == "unavailable"
    metric = next(m.value for m in app.markdown if "pa-finance-kpi-label\">Annualised Volatility" in m.value)
    assert "—" in metric and "0.00%" not in metric and "nan%" not in metric


def test_expiring_accepted_fx_withholds_dashboard_discover_scenarios_and_copilot(monkeypatch):
    app, _, calls = setup(monkeypatch)
    state = copy.deepcopy(app.session_state["portfolio_state"])
    fetched = calls.copy()
    monkeypatch.setattr(trust, "today", lambda: pd.Timestamp("2026-10-16"))
    app.run()
    assert app.session_state["analytics"]["trust_diagnostics"]["valuation_status"] == "blocked"
    assert not any('class="pa-finance-kpi-value"' in m.value for m in app.markdown)
    assert button(app, "Ask Copilot").disabled
    discover(app)
    assert not any(b.label == "Run hypothetical shock" for b in app.button)
    assert not any("Evidence and calculations" in e.label for e in app.expander)
    assert app.session_state["portfolio_state"] == state and calls == fetched


def test_existing_session_without_fx_diagnostics_cannot_bypass_trust_gate(monkeypatch):
    app, _, calls = setup(monkeypatch)
    fetched = calls.copy()
    del app.session_state["analytics"]["trust_diagnostics"]
    app.run()
    assert not app.exception
    assert app.session_state["analytics"]["trust_diagnostics"]["valuation_status"] == "blocked"
    assert not any('class="pa-finance-kpi-value"' in m.value for m in app.markdown)
    assert calls == fetched


def test_first_rejected_portfolio_can_configure_freshness_rule_and_reanalyse(monkeypatch):
    app, _, _ = setup(monkeypatch, stale_fx=True)
    assert app.session_state["portfolio_state"] is None
    next(i for i in app.number_input if i.label == "Maximum FX age (calendar days)").set_value(10).run()
    button(app, "Parse & Analyse Portfolio").click().run(timeout=20)
    assert not app.exception
    assert app.session_state["portfolio_state"] is not None
    assert app.session_state["trust_attempt"] is None
    assert app.session_state["analytics"]["trust_diagnostics"]["policy"]["max_fx_age_days"] == 10


def test_failure_after_valid_valuation_cannot_report_rejected_analysis_as_checked(monkeypatch):
    from portfolio_analytics.analytics import engine
    def unavailable(*args, **kwargs):
        raise ValueError("Synthetic analysis failure after valuation")
    monkeypatch.setattr(engine, "run_analytics", unavailable)
    app, _, _ = setup(monkeypatch)
    assert app.session_state["portfolio_state"] is None
    assert app.session_state["trust_attempt"]["valuation_status"] == "blocked"
    assert any(i["code"] == "valuation_rejected" and "Synthetic analysis failure" in i["explanation"] for i in app.session_state["trust_attempt"]["issues"])
