from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_analytics.ai.copilot import build_copilot_context
from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


def test_copilot_receives_each_engine_stage_but_not_heavy_engine_data():
    parsed = parse_dataframe(pd.DataFrame({
        "Ticker": ["AAA"],
        "Quantity": [10],
        "Current Price": [100],
    }))
    state = build_portfolio_state(parsed)
    dates = pd.bdate_range("2025-01-01", periods=100)
    prices = pd.DataFrame({"AAA": 100 * np.cumprod(1 + np.linspace(-0.01, 0.011, 100))}, index=dates)
    analytics = run_analytics(state, prices)

    context = build_copilot_context(parsed, state, analytics)
    assert set(context) == {"parser", "portfolio_state", "analytics", "scenario"}
    assert context["parser"]["classification"] == "holdings"
    assert context["portfolio_state"]["positions"][0]["ticker"] == "AAA"
    assert "risk" in context["analytics"]
    assert "engine_data" not in context["analytics"]
    assert "scenario_baseline" not in context["portfolio_state"]
    assert context["parser"]["user_dataset"]["raw_records_shared_with_ai"] is False
    assert "records" not in context["parser"]["user_dataset"]
