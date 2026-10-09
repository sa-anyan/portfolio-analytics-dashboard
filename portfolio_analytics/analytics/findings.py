"""Deterministic interpretation of canonical values; no prices, covariance or AI calls.

Concentration uses absolute security market values divided by gross exposure,
excluding cash. This measures direct security concentration, not economic look-through.
The observation floor governs interpretation only and never changes engine metrics.
"""
from __future__ import annotations

from math import isfinite, isclose
from typing import Any

MIN_FINDING_OBSERVATIONS = 30


def _finite(value: Any) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def concentration(state: dict[str, Any]) -> dict[str, Any]:
    """HHI in [0,1] and effective holdings, using complete signed position values."""
    values: dict[str, float] = {}
    for row in state.get("positions", []):
        ticker = str(row.get("ticker") or "").upper()
        if not ticker or not _finite(row.get("signed_market_value")):
            return {"available": False, "reason": "Direct holdings have missing or invalid market values."}
        values[ticker] = values.get(ticker, 0.0) + float(row["signed_market_value"])
    gross = state.get("totals", {}).get("gross_exposure")
    if state.get("meta", {}).get("missing_prices") or not _finite(gross):
        return {"available": False, "reason": "Complete reporting-currency valuation is required."}
    gross = float(gross)
    if not isclose(sum(abs(v) for v in values.values()), gross, rel_tol=1e-9, abs_tol=1e-8):
        return {"available": False, "reason": "Position values do not reconcile with canonical gross exposure."}
    if gross <= 0:
        return {"available": False, "reason": "No non-zero direct security exposure; concentration is undefined."}
    equity = state.get("totals", {}).get("equity")
    rows = [{"ticker": t, "signed_market_value": v, "gross_exposure_share": abs(v)/gross,
             "signed_equity_weight": v/float(equity) if _finite(equity) and float(equity) > 0 else None,
             "side": "LONG" if v > 0 else "SHORT"}
            for t, v in values.items() if v != 0]
    rows.sort(key=lambda r: (-r["gross_exposure_share"], r["ticker"]))
    hhi = sum(row["gross_exposure_share"]**2 for row in rows)
    return {"available": True, "holdings": rows, "holding_count": len(rows),
            "top_five": rows[:5], "top_five_share": sum(row["gross_exposure_share"] for row in rows[:5]),
            "hhi": hhi, "effective_holdings": 1/hhi,
            "method": "absolute net security market value / canonical gross security exposure; cash excluded",
            "hhi_scale": "0–1 (multiply by 10,000 for index points)"}


def generate_findings(state: dict[str, Any], analytics: dict[str, Any], *,
                      min_observations: int = MIN_FINDING_OBSERVATIONS) -> dict[str, Any]:
    """Explain existing results and preserve unavailable/insufficient risk as unknown."""
    if isinstance(min_observations, bool) or not isinstance(min_observations, int) or min_observations < 2:
        raise ValueError("Finding observation floor must be an integer of at least two.")
    direct = concentration(state)
    findings: list[dict[str, Any]] = []
    if direct["available"]:
        largest = direct["holdings"][0]
        findings.append({"id": "direct_concentration", "topic": "concentration",
            "title": f"{largest['ticker']} is the largest direct security exposure",
            "explanation": f"{largest['ticker']} accounts for {largest['gross_exposure_share']:.1%} of gross security exposure. "
                f"The largest {len(direct['top_five'])} holdings account for {direct['top_five_share']:.1%}. "
                f"HHI is {direct['hhi']:.4f}, equivalent to {direct['effective_holdings']:.2f} equally sized holdings. "
                "Position count alone can conceal concentration. These measures exclude cash and do not imply suitability.",
            "evidence": {"ticker": largest["ticker"], "largest_gross_share": largest["gross_exposure_share"],
                         "top_five_share": direct["top_five_share"], "hhi": direct["hhi"],
                         "effective_holdings": direct["effective_holdings"]},
            "investigate": "Compare the largest exposures, their signed equity weights and existing stress scenarios."})
    else:
        findings.append({"id": "concentration_unavailable", "topic": "data_quality",
                         "title": "Direct concentration is unavailable", "explanation": direct["reason"],
                         "evidence": {"available": False}, "investigate": "Resolve valuation coverage before interpreting allocation."})
    risk = analytics.get("risk", {})
    coverage = analytics.get("meta", {}).get("coverage", analytics.get("coverage", {}))
    observations = risk.get("observations", 0)
    observed = int(observations) if _finite(observations) and float(observations) >= 0 else 0
    source = analytics.get("risk_contribution", [])
    reason = None
    if not coverage.get("available", False):
        reason = coverage.get("reason") or "Complete portfolio return coverage is unavailable."
    elif observed < min_observations:
        reason = f"Only {observed} common return observations are available; findings require at least {min_observations}. Existing engine metrics are unchanged."
    elif not _finite(risk.get("annual_volatility")) or float(risk["annual_volatility"]) <= 1e-12:
        reason = "Volatility is unavailable or effectively zero; relative risk contributions cannot be interpreted reliably."
    elif not source or any(not all(_finite(row.get(k)) for k in ("weight", "risk_contribution", "risk_contribution_pct", "absolute_risk_share")) for row in source):
        reason = "Finite, complete risk-contribution estimates are unavailable."
    elif not isclose(sum(float(row["risk_contribution_pct"]) for row in source), 1, rel_tol=1e-7, abs_tol=1e-7):
        reason = "Existing risk contributions do not reconcile to portfolio volatility."
    ranked: list[dict[str, Any]] = []
    if reason is None:
        allocation = {row["ticker"]: row for row in direct.get("holdings", [])}
        for row in source:
            ticker = str(row["ticker"])
            holding = allocation.get(ticker, {})
            capital_share = holding.get("gross_exposure_share")
            absolute_share = float(row["absolute_risk_share"])
            ranked.append({**row, "signed_equity_weight": float(row["weight"]),
                "gross_security_exposure_share": capital_share,
                "absolute_risk_share_minus_gross_share": absolute_share-capital_share if capital_share is not None else None,
                "effect": "hedge" if float(row["risk_contribution_pct"]) < 0 else "risk contributor",
                "comparison_note": "Gross security allocation excludes cash; absolute risk shares include all modelled risk sources. Signed equity weights can exceed 100%."})
        ranked.sort(key=lambda row: (-float(row["risk_contribution_pct"]), str(row["ticker"])))
        leader = ranked[0]
        findings.append({"id": "risk_driver", "topic": "risk",
            "title": f"{leader['ticker']} is the largest positive volatility contributor",
            "explanation": f"{leader['ticker']} contributes {leader['risk_contribution_pct']:.1%} of modelled portfolio volatility, "
                f"with a signed equity weight of {leader['signed_equity_weight']:.1%}. "
                "Capital weights and volatility contributions differ because contribution depends on covariance with the entire signed book. Negative contributions identify modelled hedge effects.",
            "evidence": {"ticker": leader["ticker"], "signed_risk_contribution": leader["risk_contribution_pct"],
                         "signed_equity_weight": leader["signed_equity_weight"], "annual_volatility": risk["annual_volatility"],
                         "observations": observed},
            "investigate": "Compare ranked contributors and correlations, then apply an existing price-shock scenario."})
        if direct["available"] and leader["gross_security_exposure_share"] is not None:
            findings.append({"id": "capital_vs_risk", "topic": "risk",
                "title": "Allocation and risk shares measure different things",
                "explanation": f"{leader['ticker']} has {leader['gross_security_exposure_share']:.1%} of gross security exposure "
                    f"and {leader['absolute_risk_share']:.1%} of absolute modelled volatility contribution. "
                    "This comparison uses different denominators; it is descriptive, not a recommendation to rebalance.",
                "evidence": {"ticker": leader["ticker"], "gross_share": leader["gross_security_exposure_share"],
                             "absolute_risk_share": leader["absolute_risk_share"]},
                "investigate": "Inspect signed contributions to distinguish risk drivers from hedges."})
    else:
        findings.append({"id": "risk_unavailable", "topic": "data_quality", "title": "Risk interpretation is unavailable",
                         "explanation": reason, "evidence": {"observations": observed, "required_observations": min_observations},
                         "investigate": "Review coverage, historical sample size and existing risk-calculation limitations."})
    return {"concentration": direct, "risk_interpretation": {"available": reason is None, "reason": reason,
            "observations": observed, "minimum_observations": min_observations,
            "ranked_contributors": ranked,
            "ranked_absolute_contributors": sorted(ranked, key=lambda row: (-float(row["absolute_risk_share"]), str(row["ticker"])))},
            "findings": findings, "scope": "accepted canonical portfolio; direct securities only",
            "limitations": ["ETF constituent and economic look-through exposure is unknown, not zero.",
                            "HHI/effective holdings measure size concentration, not correlation-adjusted diversification.",
                            "Risk results are historical current-book estimates, not forecasts.",
                            "The 30-observation default is an interpretation floor, not evidence of statistical precision."]}
