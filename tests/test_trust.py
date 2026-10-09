"""Independent trust/availability examples; canonical engines supply all figures."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.diagnostics.trust import TrustPolicy, diagnose_trust, fx_observations
from portfolio_analytics.input_engine.parser import parse_dataframe

ASOF = "2026-10-09"


def example(n=60):
    dates = pd.bdate_range(end=ASOF, periods=n+1)
    parsed = parse_dataframe(pd.DataFrame({
        "Ticker": ["US", "UK", "EU", "PENCE", "CASH"], "Quantity": [1, 1, -1, 1, 20],
        "Current Price": [100, 100, 40, 500, 1], "Currency": ["USD", "GBP", "EUR", "GBX", "EUR"],
    }))
    fx = pd.DataFrame({"GBP": 1.25, "EUR": 1.1}, index=dates)
    metadata = {"source": "test", "observations": {t: {"as_of": ASOF, "source": "test"} for t in ["US", "UK", "EU", "PENCE"]}}
    state = build_portfolio_state(parsed, fx_history=fx, market_metadata=metadata)
    prices = pd.DataFrame({t: np.cumprod(1+.01*np.sin(np.arange(n+1)+i)) for i, t in enumerate(["US", "UK", "EU", "PENCE"])}, index=dates)
    analytics = run_analytics(state, prices, fx_history=fx)
    return state, analytics, parsed, metadata, fx


def report(values, **kwargs):
    state, analytics, parsed, metadata, fx = values
    return diagnose_trust(state, analytics, parsed=parsed, market_metadata=metadata, fx_history=fx, as_of=ASOF, **kwargs)


def codes(diagnostic):
    return {i["code"] for i in diagnostic["issues"]}


def test_valid_mixed_currency_long_short_foreign_cash_and_gbx_are_not_revalued():
    values = example()
    before = copy.deepcopy(values[:4])
    d = report(values)
    assert d["valuation_status"] == "checked"
    assert d["reporting_currency"] == "USD"
    assert values[0]["totals"]["equity"] == pytest.approx(209.25)
    assert values[0]["cash"]["current"] == pytest.approx(22)
    assert d["risk_sample"]["common_observations"] == 60
    assert "CASH:EUR" in values[1]["meta"]["coverage"]["required_tickers"]
    assert next(r for r in d["fx_evidence"] if r["currency"] == "GBX")["pair"] == "GBP/USD"
    assert all(r["economic_currency_exposure"].startswith("Unknown") for r in d["currencies"] if not r["holding"].startswith("CASH:"))
    assert tuple(values[:4]) == before
    json.dumps(d, allow_nan=False)


@pytest.mark.parametrize("rate", [None, 0, -1, float("inf"), float("nan")])
def test_missing_invalid_fx_blocks_affected_aggregates(rate):
    values = list(example())
    values[4] = pd.DataFrame({"GBP": [1.25], "EUR": [rate]}, index=[ASOF])
    d = report(values)
    assert d["valuation_status"] == "blocked"
    assert "fx_missing_invalid" in codes(d)
    issue = next(i for i in d["issues"] if i["code"] == "fx_missing_invalid")
    assert {"EU", "CASH:EUR"}.issubset(issue["holdings"])
    assert "valuation" in issue["affects"] and "scenarios" in issue["affects"]


def test_stale_fx_exact_boundary_and_configurable_age():
    values = example()
    snapshot = {c: {"rate": r, "date": "2026-10-03"} for c, r in [("GBP", 1.25), ("EUR", 1.1)]}
    d = diagnose_trust(*values[:2], market_metadata=values[3], fx_snapshot=snapshot, as_of=ASOF)
    assert d["valuation_status"] == "blocked" and "fx_stale" in codes(d)
    d = diagnose_trust(*values[:2], market_metadata=values[3], fx_snapshot=snapshot, as_of=ASOF, policy=TrustPolicy(max_fx_age_days=6))
    assert d["valuation_status"] == "checked"


def test_missing_fx_dates_and_metadata_never_assume_freshness():
    values = example()
    snapshot = {"GBP": {"rate": 1.25}, "EUR": {"rate": 1.1}}
    d = diagnose_trust(*values[:2], fx_snapshot=snapshot, as_of=ASOF)
    assert "fx_date_unknown" in codes(d) and d["valuation_status"] == "blocked"
    # Dated source series are sufficient evidence without a provider metadata blob.
    assert report(values)["valuation_status"] == "checked"


def test_unpriced_asset_and_incomplete_canonical_totals_are_explicit():
    values = list(example())
    values[0] = copy.deepcopy(values[0])
    values[0]["positions"][1].update(current_price=None, signed_market_value=None)
    values[0]["totals"]["equity"] = float("nan")
    d = report(values)
    assert {"price_missing", "totals_invalid"}.issubset(codes(d))
    issue = next(i for i in d["issues"] if i["code"] == "price_missing")
    assert issue["holdings"] == ["UK"] and {"valuation", "risk", "performance"}.issubset(issue["affects"])


def test_priya_twelve_observations_preserve_estimates_but_warn():
    values = example(n=12)
    original_risk = copy.deepcopy(values[1]["risk"])
    d = report(values)
    assert d["risk_sample"]["common_observations"] == 12
    assert "risk_short_sample" in codes(d) and d["risk_status"] == "limited"
    assert values[1]["risk"] == original_risk
    assert "not a zero-risk conclusion" in next(i for i in d["issues"] if i["code"] == "risk_short_sample")["explanation"]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -.1])
def test_invalid_risk_is_unknown_not_zero(value):
    values = list(example())
    values[1]["risk"]["annual_volatility"] = value
    d = report(values)
    assert d["risk_status"] == "unavailable"
    assert "risk_values_unavailable" in codes(d)


def test_missing_history_identifies_holdings_and_actual_account_failure():
    values = list(example())
    values[1]["meta"]["coverage"].update(available=False, missing_tickers=["EU"], common_observations=0, reason="Missing return/FX coverage: EU")
    values[1]["risk"] = {}
    values[1]["actual_performance"] = {"available": False, "reason": "Incomplete active-position price/FX coverage; account performance was withheld."}
    d = report(values)
    assert next(i for i in d["issues"] if i["code"] == "risk_coverage")["holdings"] == ["EU"]
    assert d["risk_status"] == "unavailable" and d["actual_performance_status"] == "unavailable"


def test_stale_and_unknown_security_dates_cannot_borrow_other_tickers_date():
    values = list(example())
    values[3] = {"as_of": ASOF, "source": "test", "observations": {"US": {"as_of": "2026-09-01"}}}
    d = report(values)
    assert {"price_stale", "price_date_unknown"}.issubset(codes(d))
    assert d["valuation_status"] == "warning"
    assert next(i for i in d["issues"] if i["code"] == "price_stale")["evidence"]["age_calendar_days"] == 38


def test_reporting_currency_comes_from_state_and_mismatch_blocks():
    values = list(example())
    values[1]["meta"]["base_currency"] = "EUR"
    assert "reporting_currency_mismatch" in codes(report(values))
    values[0]["meta"].pop("base_currency")
    assert "reporting_currency_unknown" in codes(report(values))


def test_historical_invalid_fx_and_gaps_are_disclosed_without_methodology_change():
    values = list(example())
    values[4] = pd.DataFrame({"GBP": [1.2, -1, 1.25], "EUR": [1.1, 1.1, 1.1]}, index=["2026-09-01", "2026-10-01", ASOF])
    d = report(values)
    assert {"fx_history_invalid", "fx_history_gap"}.issubset(codes(d))
    assert d["valuation_status"] == "checked" and d["risk_status"] == "limited"


def test_empty_cash_only_and_assumed_currency_are_explicit():
    parsed = parse_dataframe(pd.DataFrame({"Ticker": ["CASH"], "Quantity": [10], "Current Price": [1]}))
    state = build_portfolio_state(parsed)
    d = diagnose_trust(state, run_analytics(state, pd.DataFrame()), parsed=parsed, as_of=ASOF)
    assert {"currency_assumed", "no_securities", "risk_values_unavailable"}.issubset(codes(d))
    assert d["valuation_status"] != "blocked" and d["risk_status"] == "unavailable"


def test_last_real_fx_observation_is_not_forward_filled_or_sanitised_away():
    snapshot = fx_observations(pd.DataFrame({"GBP": [1.2, np.nan, -1]}, index=pd.date_range("2026-10-01", periods=3)))
    assert snapshot["GBP"]["rate"] == -1 and snapshot["GBP"]["date"].startswith("2026-10-03")


def test_blocked_copilot_cannot_spend_or_run_a_scenario(monkeypatch):
    from portfolio_analytics.ai import copilot
    monkeypatch.setattr(copilot, "route_question", lambda *args, **kwargs: pytest.fail("No AI or route execution authorised for blocked valuation"))
    with pytest.raises(ValueError, match="blocked valuation"):
        copilot.ask_copilot("What if UK falls 20%?", parsed={}, state={}, analytics={"trust_diagnostics": {"valuation_status": "blocked"}})


def test_copilot_tool_output_carries_diagnostics_and_masks_invalid_values():
    from portfolio_analytics.ai.copilot import _explanation_result
    values = example()
    values[1]["risk"]["annual_volatility"] = float("nan")
    values[1]["trust_diagnostics"] = report(values)
    context = {"analytics": values[1], "portfolio_state": values[0], "scenario": None}
    result = _explanation_result({"scope": "accepted", "ticker": None}, context)
    assert result["analytics"]["risk"]["annual_volatility"] is None
    assert result["analytics"]["trust_diagnostics"]["risk_status"] == "unavailable"


@pytest.mark.parametrize("policy", [{"max_fx_age_days": -1}, {"max_price_age_days": True}, {"min_risk_observations": 1}])
def test_invalid_policies_rejected(policy):
    with pytest.raises(ValueError):
        TrustPolicy(**policy)
