from pathlib import Path


def test_restored_visual_helpers_and_calls_present():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "def plot_growth_and_drawdown" in app
    assert "def plot_risk_contribution" in app
    assert "def plot_holdings_donut" in app
    assert "def plot_monte_carlo" in app
    assert "def render_on_demand_monte_carlo" in app
    assert app.count("plot_risk_contribution(") >= 3  # definition + both analytics paths
    assert app.count("render_on_demand_monte_carlo(") >= 3  # definition + both analytics paths
    assert app.count("plot_growth_and_drawdown(") >= 3
    assert 'xaxis_title="Date"' in app
    assert 'yaxis_title="Drawdown (%)"' in app
    assert 'x="Annual volatility (%)"' in app
    assert 'y="Annual return (%)"' in app
    assert "Run Monte Carlo Simulation" in app
    assert "Today's holdings" in app


def test_monte_carlo_is_not_called_directly_from_analytics_routes():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    # Monte Carlo should be computed inside the on-demand renderer only, not
    # unconditionally in each analytics route.
    assert app.count("cached_monte_carlo(") == 2  # cache helper definition + on-demand renderer
