
import pandas as pd

from portfolio_analytics.analytics.engine import historical_attribution
from portfolio_analytics.ai.router import validate_route


def _actual_fixture():
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    a = [60.0, 60.0, 48.0, 45.0, 46.0]
    b = [40.0, 40.0, 38.0, 37.0, 39.0]
    equity = [x + y for x, y in zip(a, b)]
    returns = [0.0]
    for prev, cur in zip(equity[:-1], equity[1:]):
        returns.append(cur / prev - 1.0)
    wealth = pd.Series([(1.0)])
    path = []
    running = 1.0
    peak = 1.0
    for i, d in enumerate(dates):
        if i:
            running *= 1.0 + returns[i]
        peak = max(peak, running)
        path.append({
            "date": d.isoformat(),
            "equity": equity[i],
            "daily_return": returns[i],
            "drawdown": running / peak - 1.0,
            "position__AAA": a[i],
            "position__BBB": b[i],
        })
    return {"available": True, "portfolio_path": path}


def test_historical_drawdown_attributes_largest_negative_contributor():
    result = historical_attribution(
        _actual_fixture(),
        start_date="2020-01-01",
        end_date="2020-01-05",
        analysis_mode="drawdown",
    )
    assert result["available"] is True
    assert result["max_drawdown_pct"] < 0
    assert result["largest_negative_contributors"][0]["ticker"] == "AAA"
    assert result["attribution_period"]["start"] <= result["attribution_period"]["end"]


def test_historical_route_accepts_year_dates():
    context = {"portfolio_state": {"positions": []}, "scenario": None}
    route = validate_route({
        "intent": "historical",
        "ticker": None,
        "fields": [],
        "actions": [],
        "scope": "accepted",
        "scenario_base": "accepted",
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "analysis_mode": "drawdown",
        "clarification": None,
    }, context)
    assert route["intent"] == "historical"
    assert route["analysis_mode"] == "drawdown"
