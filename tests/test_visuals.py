from __future__ import annotations

from portfolio_analytics.ui.visuals import correlation_heatmap, holdings_donut, risk_contribution_donut


def test_core_visuals_build_from_analytics_dictionary():
    analytics = {
        "holdings_mix": [
            {"ticker": "AAA", "exposure_share": 0.6},
            {"ticker": "BBB", "exposure_share": 0.4},
        ],
        "risk_contribution": [
            {"ticker": "AAA", "absolute_risk_share": 0.7, "risk_contribution_pct": 0.8},
            {"ticker": "BBB", "absolute_risk_share": 0.3, "risk_contribution_pct": 0.2},
        ],
        "correlation": {
            "AAA": {"AAA": 1.0, "BBB": 0.2},
            "BBB": {"AAA": 0.2, "BBB": 1.0},
        },
    }
    assert len(holdings_donut(analytics).data) == 1
    assert len(risk_contribution_donut(analytics).data) == 1
    assert len(correlation_heatmap(analytics).data) == 1


def test_scenario_comparison_visuals_use_engine_output_only():
    from portfolio_analytics.ui.visuals import scenario_comparison_bar, scenario_position_change_bar

    scenario = {
        "comparison": {
            "portfolio": {
                "before": {"equity": 1000.0, "cash": 200.0, "gross_exposure": 800.0, "net_exposure": 800.0},
                "after": {"equity": 950.0, "cash": 200.0, "gross_exposure": 750.0, "net_exposure": 750.0},
            },
            "positions": [
                {"ticker": "AAA", "market_value_before": 800.0, "market_value_after": 750.0},
            ],
        }
    }
    comparison = scenario_comparison_bar(scenario)
    positions = scenario_position_change_bar(scenario)
    assert len(comparison.data) == 2
    assert len(positions.data) == 2


def test_scenario_comparison_visuals_handle_missing_data():
    from portfolio_analytics.ui.visuals import scenario_comparison_bar, scenario_position_change_bar

    assert len(scenario_comparison_bar({}).data) == 0
    assert len(scenario_position_change_bar({}).data) == 0
