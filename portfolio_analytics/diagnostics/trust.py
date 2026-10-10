"""Diagnose existing inputs/results without valuing a competing portfolio.

Age rules are configurable calendar-day checks, not exchange calendars or a
validated confidence score. Missing timestamps are unknown, never fresh.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TrustPolicy:
    max_fx_age_days: int = 5
    max_price_age_days: int = 5
    min_risk_observations: int = 30
    min_tail_observations: int = 20

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < (2 if name == "min_risk_observations" else 0):
                raise ValueError(f"Invalid trust policy: {name}")


def finite(value: Any, *, positive: bool = False) -> bool:
    try:
        return value is not None and isfinite(float(value)) and (not positive or float(value) > 0)
    except (TypeError, ValueError):
        return False


def today() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)


def risk_values_for_display(analytics: dict) -> dict:
    """Mask unavailable/invalid risk in presentation only; retain engine outputs."""
    risk = dict(analytics.get("risk", {}))
    keys = ("annual_volatility", "var_pct", "expected_shortfall_pct", "var_value", "expected_shortfall_value")
    coverage = analytics.get("meta", {}).get("coverage", {})
    for key in keys:
        value = risk.get(key)
        if coverage.get("available") is False or not finite(value) or float(value) < 0:
            risk[key] = None
    return risk


def _date(value: Any) -> pd.Timestamp | None:
    date = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(date) else date.tz_convert(None).normalize()


def fx_observations(history: pd.DataFrame | None) -> dict[str, dict]:
    """Retain the last actual observation, including invalid values (no ffill)."""
    result = {}
    if history is None or history.empty:
        return result
    frame = history.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce", utc=True)
    frame = frame.loc[~frame.index.isna()].sort_index()
    for ccy in frame:
        series = frame[ccy].dropna()
        if not series.empty:
            valid = series.map(lambda v: finite(v, positive=True))
            dates = series.loc[valid].index
            gaps = dates.to_series().diff().dt.days.dropna()
            result[str(ccy).upper()] = {"rate": float(series.iloc[-1]) if finite(series.iloc[-1]) else None,
                                      "date": series.index[-1].isoformat(),
                                      "invalid_observation_count": int((~valid).sum()),
                                      "invalid_dates": [d.isoformat() for d in series.index[~valid][:5]],
                                      "maximum_gap_days": int(gaps.max()) if not gaps.empty else 0}
    return result


def diagnose_trust(state: dict | None, analytics: dict | None = None, *,
                   parsed: dict | None = None, latest_prices: dict | None = None,
                   market_metadata: dict | None = None, fx_history: pd.DataFrame | None = None,
                   fx_snapshot: dict | None = None, as_of: Any = None,
                   policy: TrustPolicy | None = None, failure: str | None = None) -> dict:
    policy = policy or TrustPolicy()
    declared_snapshot = (parsed or {}).get('source', {}).get('snapshot_validation', {}).get('valuation_date')
    # Only explicitly reconciled reporting snapshots establish a dated valuation.
    now = _date(as_of if as_of is not None else declared_snapshot) if as_of is not None or declared_snapshot else today()
    if now is None:
        raise ValueError("Invalid diagnostic as-of date")
    analytics, market_metadata = analytics or {}, market_metadata or {}
    normalised = (parsed or {}).get("normalised_dataset", {})
    if state:
        normalised = state.get("inputs", {}).get("normalised_dataset", normalised)
    records = normalised.get("holdings", []) + normalised.get("ledger", []) + normalised.get("cashflows", [])
    base = state.get("meta", {}).get("base_currency") if state else "USD"
    base = str(base).upper() if base else None
    issues: list[dict] = []
    currency_rows, price_rows = [], []

    def issue(code, severity, title, explanation, affects, holdings=(), evidence=None):
        issues.append({"code": code, "severity": severity, "title": title, "explanation": explanation,
                       "affects": list(affects), "holdings": sorted(set(holdings)), "evidence": evidence or {}})

    def age_check(date_value, maximum, kind, names, evidence):
        date = _date(date_value)
        if date is None:
            issue(f"{kind}_date_unknown", "blocker" if kind == "fx" else "warning",
                  f"{kind.upper()} observation date is unknown", "Freshness cannot be verified from the supplied metadata.",
                  ["valuation", "risk", "performance"], names, evidence)
            return
        age = (now-date).days
        evidence.update(observed_on=date.date().isoformat(), age_calendar_days=age, maximum_age_days=maximum)
        if age < 0 or age > maximum:
            issue(f"{kind}_{'future' if age < 0 else 'stale'}", "blocker" if kind == "fx" else "warning",
                  f"{kind.upper()} observation is {'future-dated' if age < 0 else 'stale'}",
                  "The observation does not satisfy the configured calendar-day freshness rule. Refresh the input before treating affected figures as current.",
                  ["valuation", "risk", "performance"], names, evidence)

    if not base:
        issue("reporting_currency_unknown", "blocker", "Reporting currency is unavailable",
              "Canonical reporting-currency metadata is missing; currencies cannot be safely aggregated.", ["valuation", "risk", "performance"])
    declared = analytics.get("meta", {}).get("base_currency")
    if declared and str(declared).upper() != base:
        issue("reporting_currency_mismatch", "blocker", "Reporting currencies disagree",
              "State and analytics reporting currencies must agree.", ["valuation", "risk", "performance"], evidence={"state": base, "analytics": declared})
    positions = state.get("positions", []) if state else [
        {"ticker": r.get("Ticker"), "currency": r.get("Currency", base), "current_price": (latest_prices or {}).get(r.get("Ticker"), r.get("Current Price")), "quantity": r.get("Quantity")}
        for r in normalised.get("holdings", []) if str(r.get("Ticker", "")).upper() != "CASH" and str(r.get("Asset Class", "")).lower() != "cash" and finite(r.get("Quantity")) and abs(float(r["Quantity"])) > 1e-15]
    for row in positions:
        ticker = row.get("ticker")
        ccy = str(row.get("currency") or base).upper()
        currency_rows.append({"holding": ticker, "trading_currency": ccy, "reporting_currency": base, "economic_currency_exposure": "Unknown: listing currency is not economic exposure"})
        if not finite(row.get("current_price"), positive=True) or (state and not finite(row.get("signed_market_value"))):
            issue("price_missing", "blocker", f"{ticker}: security price is missing or invalid",
                  "The complete portfolio is withheld; the asset is not silently excluded or assigned zero value.", ["valuation", "allocation", "risk", "performance", "scenarios"], [ticker])
        else:
            observation = market_metadata.get("observations", {}).get(ticker, {})
            # Aggregate as_of is safe only for a single market observation. A
            # timestamp from another ticker cannot prove this ticker is fresh.
            date = observation.get("as_of")
            if not observation and len(positions) == 1 and market_metadata.get("source") != "user supplied":
                date = market_metadata.get("as_of")
            evidence = {"holding": ticker, "source": observation.get("source", market_metadata.get("source", "unknown")), "price_date": date}
            price_rows.append(evidence)
            age_check(date, policy.max_price_age_days, "price", [ticker], evidence)
        if state and row.get("price_currency") != base:
            issue("price_currency_mismatch", "blocker", f"{ticker}: converted-price currency is inconsistent",
                  "The canonical price must be expressed in the reporting currency.", ["valuation", "risk", "performance"], [ticker])
    if state:
        balances = state.get("cash", {}).get("balances", {})
        required = {str(r.get("currency") or base).upper() for r in positions}
        required |= {str(c).upper() for c, amount in balances.items() if finite(amount) and abs(float(amount)) > 1e-12}
        for ccy, amount in balances.items():
            if not finite(amount):
                issue("cash_invalid", "blocker", f"{ccy}: cash balance is invalid", "Native cash cannot be valued safely.", ["valuation", "risk", "performance"], [f"CASH:{ccy}"])
            elif abs(float(amount)) > 1e-12:
                currency_rows.append({"holding": f"CASH:{ccy}", "trading_currency": ccy, "reporting_currency": base, "economic_currency_exposure": f"Native {ccy} cash balance"})
        if any(not finite(state.get("totals", {}).get(k)) for k in ("equity", "cash", "gross_exposure", "net_exposure")):
            issue("totals_invalid", "blocker", "Canonical portfolio totals are invalid", "Affected aggregate figures must be withheld.", ["valuation", "risk", "performance"])
    else:
        required = {str(r.get("Currency") or base).upper() for r in records}
    snapshot = fx_snapshot if fx_snapshot is not None else fx_observations(fx_history)
    fx_rows = []
    for ccy in sorted(required - {base}):
        parent = "GBP" if ccy in {"GBX", "GBPENCE"} else ccy
        observation = snapshot.get(parent, snapshot.get(ccy, {}))
        names = [r["holding"] for r in currency_rows if r["trading_currency"] == ccy]
        if not names:
            names = [str(r.get("Ticker") or f"CASH:{ccy}") for r in records if str(r.get("Currency") or base).upper() == ccy]
        evidence = {"currency": ccy, "pair": f"{parent}/{base}", "rate": observation.get("rate"), "fx_date": observation.get("date"), "quote_units": "GBP/100 (pence)" if ccy == "GBX" else ccy}
        fx_rows.append(evidence)
        if not finite(observation.get("rate"), positive=True):
            issue("fx_missing_invalid", "blocker", f"{ccy}/{base}: FX conversion is missing or invalid",
                  "A finite positive conversion rate is required for securities and native foreign cash. Affected aggregates are withheld.", ["valuation", "risk", "performance", "scenarios"], names, evidence)
        else:
            age_check(observation.get("date"), policy.max_fx_age_days, "fx", names, evidence)
        if observation.get("invalid_observation_count", 0):
            issue("fx_history_invalid", "warning", f"{ccy}: historical FX contains invalid observations",
                  "The existing FX adapter masks non-positive/non-finite rates and carries prior valid observations forward. Investigate affected history before trusting risk or account performance.",
                  ["risk", "performance"], names, {"invalid_observations": observation["invalid_observation_count"], "sample_dates": observation.get("invalid_dates", [])})
        if observation.get("maximum_gap_days", 0) > policy.max_fx_age_days:
            issue("fx_history_gap", "warning", f"{ccy}: historical FX has a freshness gap",
                  "A gap between valid observations exceeds the configured FX age rule. Historical conversions can carry older rates forward; the rule does not model exchange holidays.",
                  ["risk", "performance"], names, {"maximum_gap_days": observation["maximum_gap_days"], "maximum_age_days": policy.max_fx_age_days})
    for warning in (state or {}).get("meta", {}).get("warnings", []):
        issue("accounting_assumption", "warning", "Accounting approximation", warning, ["pnl", "performance"])
    currency_column = (parsed or {}).get("column_map", {}).get("currency")
    raw_rows = (parsed or {}).get("user_dataset", {}).get("records", [])
    currency_assumed = bool(parsed and raw_rows and (not currency_column or any(
        r.get(currency_column) is None or pd.isna(r.get(currency_column)) or str(r.get(currency_column)).strip() == "" for r in raw_rows)))
    if currency_assumed:
        issue("currency_assumed", "warning", "Some trading currencies were assumed",
              "Missing input Currency defaults to USD. Verify the listing's quote currency against the broker record.", ["valuation", "risk", "performance"])
    if failure:
        issue("valuation_rejected", "blocker", "Submitted portfolio was not accepted", failure,
              ["valuation", "allocation", "risk", "performance", "scenarios"])
    coverage = analytics.get("meta", {}).get("coverage", {})
    observations = coverage.get("common_observations", 0)
    risk = analytics.get("risk", {})
    missing_history = coverage.get("missing_tickers", [])
    risk_status = "unavailable"
    if analytics:
        if not coverage.get("available", False):
            issue("risk_coverage", "warning", "Full-portfolio risk is unavailable", coverage.get("reason") or "Complete historical coverage is missing.", ["risk", "simulated_performance", "risk_contribution"], missing_history, {"common_observations": observations})
        elif observations < policy.min_risk_observations:
            risk_status = "limited"
            issue("risk_short_sample", "warning", "Risk history is insufficient for interpretation",
                  f"Only {observations} common historical observations; the interpretation rule requires {policy.min_risk_observations}. Existing estimates are retained as limited-sample estimates, not a zero-risk conclusion.", ["risk", "risk_contribution", "simulated_performance"], evidence={"common_observations": observations, "interpretation_minimum": policy.min_risk_observations, "engine_minimum": 2})
        else:
            risk_status = "checked"
        invalid = [k for k in ("annual_volatility", "var_pct", "expected_shortfall_pct", "var_value", "expected_shortfall_value") if not finite(risk.get(k)) or float(risk[k]) < 0]
        if invalid:
            risk_status = "unavailable"
            issue("risk_values_unavailable", "warning", "Risk values are missing or invalid",
                  "Unavailable estimates are unknown, not zero risk. Valid historical zero estimates are still sample-dependent.", ["risk"], evidence={"metrics": invalid})
        tail = risk.get("tail_observations")
        if finite(tail) and tail < policy.min_tail_observations:
            issue("risk_small_tail", "warning", "Tail estimates have limited evidence",
                  "VaR and Expected Shortfall use a small historical tail and have substantial sampling uncertainty.", ["var", "expected_shortfall"], evidence={"tail_observations": tail, "warning_threshold": policy.min_tail_observations})
        actual = analytics.get("actual_performance", {})
        if not actual.get("available", False):
            issue("account_performance_unavailable", "warning", "Actual account performance is unavailable",
                  actual.get("reason") or "Complete dated account history is required. The current-weight simulation is a separate hypothetical analysis.", ["actual_performance"], (state or {}).get("accounting_history", {}).get("missing_dated_basis", []))
    if state and not positions:
        issue("no_securities", "info", "No open securities", "Security concentration is undefined. Base-currency cash earning zero is an explicit model assumption; missing risk is not zero risk.", ["concentration", "risk"])
    importance = {"valuation_rejected": -1, "risk_coverage": 0, "risk_values_unavailable": 1, "risk_short_sample": 2,
                  "price_stale": 3, "price_future": 3, "price_date_unknown": 4}
    if declared_snapshot and now < today():
        issue('historical_snapshot', 'info', 'Historical statement valuation',
              'Valuation and observation freshness refer to the user-declared statement date. This is not a current valuation; the source date has not been independently verified.',
              ['valuation'], evidence={'valuation_date': now.date().isoformat(), 'checked_on': today().date().isoformat()})
    issues.sort(key=lambda i: ({"blocker": 0, "warning": 1, "info": 2}[i["severity"]], importance.get(i["code"], 5), i["code"], i["holdings"]))
    blocked = any(i["severity"] == "blocker" and "valuation" in i["affects"] for i in issues)
    if blocked:
        risk_status = "unavailable"
    elif risk_status == "checked" and any(i["severity"] == "warning" and "risk" in i["affects"] for i in issues):
        risk_status = "limited"
    performance_status = "unavailable" if blocked or not analytics.get("actual_performance", {}).get("available") else (
        "warning" if any("performance" in i["affects"] for i in issues) else "checked")
    meta = analytics.get("meta", {})
    common_dates = [r.get("date") for r in analytics.get("series", {}).get("portfolio_returns", []) if r.get("date")]
    return {"as_of": now.date().isoformat(), "checked_on": today().date().isoformat(), "policy": asdict(policy), "reporting_currency": base,
            "valuation_status": "blocked" if blocked else "warning" if any("valuation" in i["affects"] for i in issues) else "checked",
            "risk_status": risk_status, "actual_performance_status": performance_status,
            "issues": issues, "currencies": currency_rows,
            "fx_evidence": fx_rows, "price_evidence": price_rows, "fx_snapshot": snapshot,
            "risk_sample": {"common_observations": observations, "missing_history": missing_history,
                            "history_start": meta.get("history_start"), "history_end": meta.get("history_end"),
                            "common_start": min(common_dates) if common_dates else None,
                            "common_end": max(common_dates) if common_dates else None,
                            "tail_observations": risk.get("tail_observations")},
            "methodology": {"risk": meta.get("risk_method"), "calendar": meta.get("calendar"),
                            "performance": meta.get("simulation"), "pnl": (state or {}).get("meta", {}).get("pnl_basis"),
                            "documentation": "docs/MATH_METHODOLOGY.md",
                            "freshness": "Calendar days from actual observation dates; no exchange-holiday calendar. A checked input is not a guarantee of accuracy."}}
