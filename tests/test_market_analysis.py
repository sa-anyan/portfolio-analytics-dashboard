from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.ingestion.parsing_engine import build_parsing_report
from portfolio_analytics.analytics.market_analysis import (
    standardise_market_data,
    asset_metrics,
    portfolio_metrics,
    risk_contribution,
    monte_carlo_portfolios,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sp500_market_data_sample.csv"


def test_sp500_fixture_routes_to_market_analytics():
    frame = pd.read_csv(FIXTURE)
    report = build_parsing_report(frame)
    assert report["classification"]["mode"] == "market_data"
    market = standardise_market_data(frame, report["schema"])
    assert market.prices.shape[1] == 1
    assert market.prices.columns.tolist() == ["GSPC"]
    assert len(market.returns.dropna()) > 100


def test_single_asset_var_and_es_are_finite():
    frame = pd.read_csv(FIXTURE)
    report = build_parsing_report(frame)
    market = standardise_market_data(frame, report["schema"])
    metrics = asset_metrics(market.returns, var_level=0.95).iloc[0]
    assert np.isfinite(metrics["annual_volatility"])
    assert metrics["var"] >= 0
    assert metrics["expected_shortfall"] >= metrics["var"]
    assert metrics["max_drawdown"] <= 0


def test_multi_asset_portfolio_analytics():
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.default_rng(7)
    returns = pd.DataFrame({
        "AAA": rng.normal(0.0005, 0.01, len(dates)),
        "BBB": rng.normal(0.0002, 0.008, len(dates)),
        "CCC": rng.normal(0.0007, 0.015, len(dates)),
    }, index=dates)
    weights = pd.Series({"AAA": 0.4, "BBB": 0.4, "CCC": 0.2})
    metrics = portfolio_metrics(returns, weights)
    rc = risk_contribution(returns, weights)
    sims, candidates = monte_carlo_portfolios(returns, simulations=1000, seed=42)
    assert metrics["observations"] == 300
    assert np.isfinite(metrics["annual_volatility"])
    assert len(rc) == 3
    assert abs(rc["Risk Contribution %"].sum() - 1.0) < 1e-8
    assert len(sims) == 1000
    assert set(candidates["Portfolio"]) == {"Max Sharpe", "Min Volatility", "Max Return"}


def test_a2m_filename_ticker_inference_supports_analytics():
    fixture = Path(__file__).parent / "fixtures" / "a2m_market_data_sample.csv"
    frame = pd.read_csv(fixture)
    report = build_parsing_report(frame)
    market = standardise_market_data(frame, report["schema"], inferred_ticker="A2M")
    assert market.prices.columns.tolist() == ["A2M"]
    assert market.price_field == "adjusted_close"
    assert len(market.observations) == len(frame)
    assert len(market.returns.dropna()) == len(frame) - 1


def test_signed_portfolio_returns_preserves_short_direction():
    import pandas as pd
    from portfolio_analytics.analytics.market_analysis import signed_portfolio_returns

    returns = pd.DataFrame({
        "LONG": [0.10, -0.02],
        "SHORT": [0.05, -0.10],
    })
    weights = pd.Series({"LONG": 0.8, "SHORT": -0.3})

    result = signed_portfolio_returns(returns, weights)

    assert result.iloc[0] == pytest.approx(0.065)
    assert result.iloc[1] == pytest.approx(0.014)


def test_signed_portfolio_metrics_accepts_long_short_weights():
    import pandas as pd
    from portfolio_analytics.analytics.market_analysis import signed_portfolio_metrics

    returns = pd.DataFrame({
        "LONG": [0.01, 0.02, -0.01, 0.005],
        "SHORT": [-0.02, 0.01, 0.03, -0.005],
    })
    weights = pd.Series({"LONG": 1.2, "SHORT": -0.4})

    metrics = signed_portfolio_metrics(returns, weights, var_level=0.95)

    assert metrics["observations"] == 4
    assert np.isfinite(metrics["annual_return"])
    assert np.isfinite(metrics["annual_volatility"])


def test_signed_risk_contribution_supports_negative_weights():
    import pandas as pd
    from portfolio_analytics.analytics.market_analysis import signed_risk_contribution

    returns = pd.DataFrame({
        "LONG": [0.01, 0.02, -0.01, 0.005, 0.015],
        "SHORT": [-0.02, 0.01, 0.03, -0.005, 0.0],
    })
    weights = pd.Series({"LONG": 1.1, "SHORT": -0.35})

    contribution = signed_risk_contribution(returns, weights)

    assert not contribution.empty
    assert set(contribution["Ticker"]) == {"LONG", "SHORT"}
    assert contribution["Risk Contribution %"].sum() == pytest.approx(1.0)
