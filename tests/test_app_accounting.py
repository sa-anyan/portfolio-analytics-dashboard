"""Exercise Streamlit's actual parse/FX/analytics wiring without provider or AI calls."""
from pathlib import Path
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
import streamlit as st
from portfolio_analytics.core import market_data, fx
from portfolio_analytics.ai import copilot


def test_manual_mixed_currency_portfolio_reaches_dashboard_with_consistent_account_value(monkeypatch):
    dates = pd.bdate_range("2025-01-02", periods=4)
    def history(tickers, **kwargs):
        result = pd.DataFrame({ticker: [200]*4 if ticker == "UKTEST" else [100]*4 for ticker in tickers}, index=dates)
        result.attrs["price_basis"] = kwargs.get("price_basis", "total_return")
        result.attrs["splits"] = pd.DataFrame()
        return result, {"source": "test"}
    monkeypatch.setattr(market_data, "fetch_price_history", history)
    monkeypatch.setattr(market_data, "fetch_latest_prices", lambda tickers: ({"UKTEST": 200, "USTEST": 100}, {"source": "test"}))
    monkeypatch.setattr(fx, "fetch_fx_history", lambda *args, **kwargs: (pd.DataFrame({"GBP": [1.25, 1.3, 1.35, 1.4]}, index=dates), {"source": "test"}))
    monkeypatch.setattr(copilot, "generate_dashboard_insights", lambda *args, **kwargs: {})
    st.cache_data.clear()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["manual_rows"] = pd.DataFrame({
        "Ticker": ["UKTEST", "USTEST", "CASH"], "Quantity": [1, 1, 10],
        "Current Price": [200, 100, 1], "Average Entry Price": [200, 100, 1],
        "Currency": ["GBX", "USD", "GBP"], "Purchase Date": [dates[0], dates[0], None],
        "Asset Class": ["Equity", "Equity", "Cash"],
    })
    app.run()
    next(r for r in app.radio if r.label == "Input method").set_value("Manual entry").run()
    next(button for button in app.button if button.label == "Parse & Analyse Portfolio").click().run(timeout=20)
    assert not app.exception, [e.message for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    portfolio = app.session_state["portfolio_state"]
    assert portfolio["totals"]["equity"] == pytest.approx(116.8)
    analytics = app.session_state["analytics"]
    assert analytics["meta"]["coverage"]["available"]
    assert analytics["actual_performance"]["accounting_summary"]["ending_equity"] == pytest.approx(116.8)
    st.cache_data.clear()
