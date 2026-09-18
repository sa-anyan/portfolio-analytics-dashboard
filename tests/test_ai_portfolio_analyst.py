from portfolio_analytics.ai.portfolio_analyst import sanitise_context


def test_sanitise_context_handles_nested_values():
    context = {
        "portfolio_equity": 1000.0,
        "risk": {"sharpe": 0.72, "missing": float("nan")},
        "positions": [{"ticker": "SPY", "weight": 0.5}],
    }
    clean = sanitise_context(context)
    assert clean["portfolio_equity"] == 1000.0
    assert clean["risk"]["sharpe"] == 0.72
    assert clean["risk"]["missing"] is None
    assert clean["positions"][0]["ticker"] == "SPY"
