import copy
import numpy as np
import pandas as pd
import pytest
from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.analytics.findings import concentration, generate_findings
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.input_engine.parser import parse_dataframe


def portfolio(values):
    return build_portfolio_state(parse_dataframe(pd.DataFrame({
        "Ticker": list(values), "Quantity": list(values.values()), "Current Price": [1]*len(values),
    })))


def analytics(state, n=100, correlated=False):
    x = np.arange(n) * 2*np.pi/20
    returns = {row["ticker"]: .01*np.sin(x) if correlated else (.005 + .015*i)*np.sin(x+i)
               for i, row in enumerate(state["positions"])}
    dates = pd.bdate_range("2025-01-01", periods=n+1)
    prices = pd.DataFrame({ticker: np.r_[1., np.cumprod(1+r)] for ticker, r in returns.items()}, index=dates)
    return run_analytics(state, prices)


def test_daniel_85_percent_direct_concentration_and_top_five():
    s = portfolio({"NVDA": 850, "A": 30, "B": 30, "C": 30, "D": 30, "E": 30})
    result = analytics(s)["deep_findings"]
    c = result["concentration"]
    assert c["top_five_share"] == pytest.approx(.97)
    assert c["hhi"] == pytest.approx(.85**2+5*.03**2)
    assert c["effective_holdings"] == pytest.approx(1/(.85**2+5*.03**2))
    assert c["holdings"][0]["signed_equity_weight"] == .85
    assert "85.0%" in result["findings"][0]["explanation"]
    assert "unknown, not zero" in result["limitations"][0]


def test_long_short_gross_concentration_and_negative_hedge_contribution():
    s = portfolio({"LONG": 150, "SHORT": -50})
    result = analytics(s, correlated=True)["deep_findings"]
    c = result["concentration"]
    assert c["hhi"] == pytest.approx(.625)
    assert c["effective_holdings"] == pytest.approx(1.6)
    assert c["holdings"][0]["gross_exposure_share"] == .75
    assert c["holdings"][0]["signed_equity_weight"] == 1.5
    ranked = result["risk_interpretation"]["ranked_contributors"]
    assert ranked[0]["risk_contribution_pct"] == pytest.approx(1.5)
    assert ranked[1]["risk_contribution_pct"] == pytest.approx(-.5)
    assert ranked[1]["effect"] == "hedge"
    assert sum(row["absolute_risk_share"] for row in ranked) == pytest.approx(1)


def test_priya_ranking_reuses_covariance_results_and_compares_capital_with_risk():
    s = portfolio({"LOWVOL": 500, "HIGHVOL": 500})
    a = analytics(s)
    result = a["deep_findings"]["risk_interpretation"]
    assert result["available"]
    leader = result["ranked_contributors"][0]
    assert leader["ticker"] == "HIGHVOL"
    original = next(row for row in a["risk_contribution"] if row["ticker"] == "HIGHVOL")
    assert leader["risk_contribution_pct"] == original["risk_contribution_pct"]
    assert leader["gross_security_exposure_share"] == .5
    assert leader["absolute_risk_share"] > .5
    assert leader["absolute_risk_share_minus_gross_share"] == pytest.approx(leader["absolute_risk_share"]-.5)


def test_perfectly_correlated_assets_are_interpreted_without_claiming_independence():
    s = portfolio({"A": 50, "B": 50})
    a = analytics(s, correlated=True)
    assert a["correlation"]["A"]["B"] == pytest.approx(1)
    assert a["deep_findings"]["risk_interpretation"]["available"]
    assert "not correlation-adjusted" in a["deep_findings"]["limitations"][1]


def test_missing_market_history_keeps_risk_unknown_but_current_concentration_available():
    s = portfolio({"A": 85, "B": 15})
    prices = pd.DataFrame({"A": [1, 1.1, 1.2]}, index=pd.bdate_range("2025-01-01", periods=3))
    a = run_analytics(s, prices)
    result = a["deep_findings"]
    assert result["concentration"]["available"]
    assert not result["risk_interpretation"]["available"]
    assert result["risk_interpretation"]["ranked_contributors"] == []
    assert "B" in result["risk_interpretation"]["reason"]
    assert a["risk"]["annual_volatility"] is None


def test_twelve_observations_withhold_interpretation_without_changing_engine_methodology():
    s = portfolio({"A": 85, "B": 15})
    a = analytics(s, n=12)
    before = copy.deepcopy(a)
    result = generate_findings(s, a)
    assert a == before
    assert a["risk"]["observations"] == 12
    assert a["risk"]["annual_volatility"] is not None
    assert not result["risk_interpretation"]["available"]
    assert "12" in result["risk_interpretation"]["reason"]


@pytest.mark.parametrize("volatility", [None, 0., 1e-14])
def test_unavailable_or_near_zero_volatility_has_no_invented_risk_rank(volatility):
    s = portfolio({"A": 100})
    a = analytics(s)
    a["risk"]["annual_volatility"] = volatility
    result = generate_findings(s, a)
    assert not result["risk_interpretation"]["available"]
    assert not result["risk_interpretation"]["ranked_contributors"]


@pytest.mark.parametrize("missing_value", [None, float("nan"), float("inf")])
def test_invalid_market_value_never_becomes_zero_concentration(missing_value):
    s = portfolio({"A": 85, "B": 15})
    s["positions"][1]["signed_market_value"] = missing_value
    assert not concentration(s)["available"]


def test_cash_excluded_and_canonical_state_not_mutated():
    s = build_portfolio_state(parse_dataframe(pd.DataFrame({"Ticker": ["A", "CASH"],
        "Quantity": [10, 90], "Current Price": [1, 1]})))
    before = copy.deepcopy(s)
    c = concentration(s)
    assert s == before
    assert c["hhi"] == 1 and c["effective_holdings"] == 1
    assert c["holdings"][0]["signed_equity_weight"] == .1
    assert c["top_five_share"] == 1


def test_empty_portfolio_and_inconsistent_gross_are_unavailable():
    assert not concentration({"positions": [], "totals": {"gross_exposure": 0}})["available"]
    s = portfolio({"A": 100})
    s["totals"]["gross_exposure"] = 200
    assert not concentration(s)["available"]


def test_invalid_risk_contributions_are_not_explained():
    s = portfolio({"A": 100})
    a = analytics(s)
    a["risk_contribution"][0]["risk_contribution_pct"] = float("inf")
    result = generate_findings(s, a)
    assert not result["risk_interpretation"]["available"]
    assert not result["risk_interpretation"]["ranked_contributors"]
