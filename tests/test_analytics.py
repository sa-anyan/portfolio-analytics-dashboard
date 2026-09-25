from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


def _history():
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2025-01-01", periods=300)
    r1 = rng.normal(0.0004, 0.02, len(dates))
    r2 = rng.normal(0.0002, 0.01, len(dates))
    return pd.DataFrame({
        "AAA": 100 * np.cumprod(1 + r1),
        "BBB": 50 * np.cumprod(1 + r2),
    }, index=dates)


def test_analytics_returns_core_metrics_and_charts_data():
    parsed = parse_dataframe(pd.DataFrame({
        "Ticker": ["AAA", "BBB"],
        "Quantity": [10, 20],
        "Current Price": [100, 50],
    }))
    state = build_portfolio_state(parsed)
    analytics = run_analytics(state, _history())
    assert analytics["risk"]["annual_volatility"] is not None
    assert analytics["risk"]["var_value"] is not None
    assert analytics["risk"]["expected_shortfall_value"] is not None
    assert len(analytics["risk_contribution"]) == 2
    assert len(analytics["holdings_mix"]) == 2
    assert "AAA" in analytics["correlation"]
    assert analytics["series"]["portfolio_path"]
