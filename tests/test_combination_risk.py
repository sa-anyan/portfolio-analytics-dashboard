import numpy as np
import pandas as pd

from portfolio_analytics.analytics.combination_risk import calculate_combination_risk


def _returns():
    idx = pd.date_range("2025-01-01", periods=80, freq="B")
    x = np.linspace(0, 8, len(idx))
    return pd.DataFrame({
        "AAA": 0.001 + 0.010 * np.sin(x),
        "BBB": 0.0005 + 0.006 * np.cos(x),
        "CCC": 0.0002 - 0.004 * np.sin(x),
    }, index=idx)


def test_pairs_use_all_eligible_portfolio_tickers():
    result = calculate_combination_risk(_returns(), ["AAA", "BBB", "CCC"], combination_size=2)
    assert result["available"] is True
    assert result["eligible_tickers"] == ["AAA", "BBB", "CCC"]
    assert result["candidate_count"] == 3
    assert result["evaluated_count"] == 3
    labels = {row["label"] for row in result["results"]}
    assert labels == {"AAA + BBB", "AAA + CCC", "BBB + CCC"}


def test_missing_history_is_reported_not_silently_removed_from_portfolio_list():
    result = calculate_combination_risk(_returns(), ["AAA", "BBB", "MISSING"], combination_size=2)
    assert result["portfolio_tickers"] == ["AAA", "BBB", "MISSING"]
    assert "MISSING" in result["excluded_tickers"]
    assert result["eligible_tickers"] == ["AAA", "BBB"]


def test_combination_is_equal_weighted():
    returns = _returns()
    result = calculate_combination_risk(returns, ["AAA", "BBB"], combination_size=2)
    row = result["results"][0]
    expected = returns[["AAA", "BBB"]].mean(axis=1)
    expected_vol = expected.std(ddof=1) * np.sqrt(252)
    assert row["weight_each"] == 0.5
    assert np.isclose(row["annual_volatility"], expected_vol)


def test_user_can_narrow_universe_without_changing_portfolio_ticker_record():
    result = calculate_combination_risk(
        _returns(), ["AAA", "BBB", "CCC"], combination_size=2, selected_tickers=["AAA", "CCC"]
    )
    assert result["portfolio_tickers"] == ["AAA", "BBB", "CCC"]
    assert result["selected_tickers"] == ["AAA", "CCC"]
    assert result["candidate_count"] == 1


def test_combinatorial_safety_limit_is_explicit():
    idx = pd.date_range("2025-01-01", periods=40, freq="B")
    data = pd.DataFrame({f"T{i}": np.linspace(-0.01, 0.01, 40) for i in range(10)}, index=idx)
    result = calculate_combination_risk(data, list(data.columns), combination_size=5, max_combinations=100)
    assert result["available"] is False
    assert result["candidate_count"] == 252
    assert "safety limit" in result["reason"]
