"""Offline upload-pipeline and real Streamlit interaction tests for Discover."""
import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio_analytics.core import market_data, fx
from portfolio_analytics.ai import copilot
from portfolio_analytics.ui.discover import visible_findings

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def button(app, label):
    return next(b for b in app.button if b.label == label)


def workspace(app, value):
    next(r for r in app.radio if r.label == "Workspace").set_value(value).run()
    assert not app.exception, [e.message for e in app.exception]


def upload_app(monkeypatch, values, n=100, missing=()):
    rows = pd.DataFrame({"Ticker": list(values), "Quantity": list(values.values()),
                         "Current Price": [1.]*len(values), "Currency": ["USD"]*len(values)})
    uploaded = SimpleNamespace(name="synthetic.csv", getvalue=lambda: rows.to_csv(index=False).encode())
    # File-picker transport is mocked; the app's real CSV upload parser is exercised.
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploaded)
    calls = {"history": 0, "latest": 0, "fx": 0}
    def history(tickers, **kwargs):
        calls["history"] += 1
        x = np.arange(n)*2*np.pi/20
        prices = pd.DataFrame({t: np.r_[1., np.cumprod(1+(.02 if t == "HIGHVOL" else .005 if t == "LOWVOL" else .005+.015*i)*np.sin(x+(1 if t == "HIGHVOL" else 0 if t == "LOWVOL" else i)))]
                              for i, t in enumerate(tickers) if t not in missing},
                             index=pd.bdate_range("2025-01-01", periods=n+1))
        prices.attrs.update(price_basis=kwargs.get("price_basis", "total_return"), splits=pd.DataFrame())
        return prices, {"source": "test"}
    def latest(tickers):
        calls["latest"] += 1
        return {t: 1. for t in tickers}, {"source": "test"}
    def fx_history(*args, **kwargs):
        calls["fx"] += 1
        return pd.DataFrame(), {"source": "test"}
    monkeypatch.setattr(market_data, "fetch_price_history", history)
    monkeypatch.setattr(market_data, "fetch_latest_prices", latest)
    monkeypatch.setattr(fx, "fetch_fx_history", fx_history)
    st.cache_data.clear()
    app = AppTest.from_file(APP).run()
    button(app, "Parse & Analyse Portfolio").click().run(timeout=20)
    assert not app.exception, [e.message for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    return app, calls


def test_daniel_upload_concentration_evidence_unknown_lookthrough_and_maya_state_reuse(monkeypatch):
    app, calls = upload_app(monkeypatch, {"NVDA": 850, "A": 30, "B": 30, "C": 30, "D": 30, "E": 30})
    state = copy.deepcopy(app.session_state["portfolio_state"])
    fetched = calls.copy()
    workspace(app, "Deep Analytics")
    assert any("85.0%" in m.value and "NVDA" in m.value for m in app.markdown)
    assert any("unknown, not zero" in c.value for c in app.caption)
    assert any("NVDA" in e.label and "Evidence" in e.label for e in app.expander)
    # Expander tables are real rendered elements (not just a successful startup).
    tables = [d.value for d in app.dataframe]
    assert any("gross_exposure_share" in d.columns and d.iloc[0]["ticker"] == "NVDA" for d in tables)
    button(app, "Open supporting portfolio analysis").click().run()
    assert app.session_state["workspace"] == "Portfolio dashboard"
    assert app.session_state["dashboard_focus"] == "allocation"
    assert any("Allocation & Risk" in m.value for m in app.markdown)
    workspace(app, "Deep Analytics")
    assert app.session_state["portfolio_state"] == state
    assert calls == fetched  # Navigation triggers no provider calls or uploads.


def test_priya_risk_comparison_shock_and_existing_results_preserve_portfolio(monkeypatch):
    app, calls = upload_app(monkeypatch, {"LOWVOL": 500, "HIGHVOL": 500})
    state = copy.deepcopy(app.session_state["portfolio_state"])
    fetched = calls.copy()
    workspace(app, "Deep Analytics")
    assert any("HIGHVOL" in m.value and "85.2%" in m.value for m in app.markdown)
    tables = [d.value for d in app.dataframe if "risk_contribution_pct" in d.value.columns]
    assert tables and tables[0].iloc[0]["ticker"] == "HIGHVOL"
    assert tables[0].iloc[0]["signed_equity_weight"] == .5
    assert next(s for s in app.selectbox if s.label == "Position to investigate").value == "HIGHVOL"
    button(app, "Run hypothetical shock").click().run()
    assert app.session_state["latest_scenario"]["comparison"]["equity_change"] == pytest.approx(-75)
    assert app.session_state["portfolio_state"] == state
    button(app, "Open existing scenario results").click().run()
    assert app.session_state["workspace"] == "Portfolio dashboard"
    assert any(s.value == "4. Latest Scenario" for s in app.subheader)
    assert any(m.label == "Equity Change" and m.value == "$-75.00" for m in app.metric)
    assert calls == fetched


def test_copilot_question_is_prepared_without_sending_and_explanation_has_evidence(monkeypatch):
    app, calls = upload_app(monkeypatch, {"A": 85, "B": 15})
    state = copy.deepcopy(app.session_state["portfolio_state"])
    workspace(app, "Deep Analytics")
    button(app, "Prepare a Copilot question").click().run()
    assert "deterministic finding" in app.session_state["copilot_question"]
    assert app.session_state["copilot_uses"] == 0
    assert app.session_state["copilot_messages"] == []
    assert app.session_state["portfolio_state"] == state
    from portfolio_analytics.ai.copilot import _explanation_result
    context = {"portfolio_state": state, "analytics": app.session_state["analytics"], "scenario": None}
    result = _explanation_result({"scope": "accepted", "ticker": None}, context)
    assert result["analytics"]["deep_findings"] == app.session_state["analytics"]["deep_findings"]


@pytest.mark.parametrize("n,missing", [(12, ()), (100, ("B",))])
def test_unavailable_risk_has_explanation_and_no_fabricated_ranking(monkeypatch, n, missing):
    app, _ = upload_app(monkeypatch, {"A": 85, "B": 15}, n=n, missing=missing)
    workspace(app, "Deep Analytics")
    assert any("Risk interpretation is unavailable" in m.value for m in app.markdown)
    assert not any("largest positive volatility contributor" in m.value for m in app.markdown)
    assert not app.exception


def test_discover_before_analysis_and_cash_only_empty_portfolio(monkeypatch):
    st.cache_data.clear()
    app = AppTest.from_file(APP).run()
    workspace(app, "Deep Analytics")
    assert any("no second upload" in i.value for i in app.info)
    button(app, "Build your portfolio").click().run()
    assert app.session_state["workspace"] == "Portfolio dashboard"
    app, _ = upload_app(monkeypatch, {"CASH": 100})
    workspace(app, "Deep Analytics")
    assert any("no open security positions" in i.value for i in app.info)
    assert not any(b.label == "Run hypothetical shock" for b in app.button)


def test_findings_are_prioritised_and_capped_without_modifying_input():
    findings = [{"id": "capital_vs_risk"}, {"id": "risk_driver"}, {"id": "direct_concentration"}, {"id": "risk_unavailable"}]
    before = copy.deepcopy(findings)
    shown, more = visible_findings(findings)
    assert len(shown) == 3 and len(more) == 1
    assert shown[0]["id"] == "risk_unavailable"
    assert findings == before
