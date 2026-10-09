"""Compact data-quality view of deterministic diagnostics."""
from pathlib import Path

import pandas as pd
from portfolio_analytics.security.ui import safe_dataframe
import streamlit as st


def _save_policy() -> None:
    st.session_state["trust_policy"] = {
        "max_fx_age_days": st.session_state.get("trust_fx_max_age_days", 5),
        "max_price_age_days": st.session_state.get("trust_price_max_age_days", 5),
    }


def render_trust(report: dict, *, rejected: bool = False, controls: bool = True, key_prefix: str = "") -> None:
    st.markdown("**Data Quality / Trust**" + (" · Rejected submission" if rejected else ""))
    status = report.get("valuation_status", "unknown")
    summary = f"Reporting currency: {report.get('reporting_currency') or 'unknown'} · Valuation: {status} · Risk: {report.get('risk_status', 'unavailable')} · Actual performance: {report.get('actual_performance_status', 'unavailable')} · Checked on: {report.get('as_of')}"
    if status == "blocked":
        st.error("Affected portfolio totals are withheld. " + summary)
    else:
        st.caption(summary + ". Checked means no detected blocking issue; it is not a guarantee.")
    issues = report.get("issues", [])
    important = [i for i in issues if i["severity"] == "blocker"]
    if not important:
        important = [i for i in issues if i["severity"] == "warning"]
    for issue in important[:3]:
        st.warning(issue["title"] + " · " + issue["explanation"])
    if len(issues) > len(important[:3]):
        st.caption(f"{len(issues)} diagnostic items. Expand for affected holdings, dates and assumptions.")
    with st.expander("Trust evidence, affected calculations and methodology", expanded=False):
        if issues:
            safe_dataframe(pd.DataFrame([{"Severity": i["severity"], "Issue": i["title"],
                                       "Holdings": ", ".join(i["holdings"]) or "Whole portfolio",
                                       "Affected calculations": ", ".join(i["affects"]),
                                       "Explanation": i["explanation"], "Evidence": str(i["evidence"])} for i in issues]), hide_index=True, use_container_width=True)
        for label, key in (("Currency distinctions", "currencies"), ("FX observations", "fx_evidence"), ("Pricing dates", "price_evidence")):
            if report.get(key):
                st.caption(label)
                safe_dataframe(pd.DataFrame(report[key]), hide_index=True, use_container_width=True)
        sample = report.get("risk_sample", {})
        st.write(f"Common historical observations: {sample.get('common_observations', 0)}. Common return sample: {sample.get('common_start') or 'unavailable'} to {sample.get('common_end') or 'unavailable'}. Source price history: {sample.get('history_start') or 'unavailable'} to {sample.get('history_end') or 'unavailable'}. Tail observations: {sample.get('tail_observations') if sample.get('tail_observations') is not None else 'unavailable'}.")
        st.caption("Trading currency is the listing's quote unit; reporting currency is the unit of aggregate figures. Economic currency exposure cannot be inferred from a listing or ETF ticker. GBX means GBP pence, not pounds.")
        for key, value in report.get("methodology", {}).items():
            if value and key != "documentation":
                st.caption(f"{key.title()}: {value}")
        if controls:
            st.number_input("Maximum FX age (calendar days)", min_value=0, max_value=365, value=report["policy"]["max_fx_age_days"], step=1, key="trust_fx_max_age_days", on_change=_save_policy)
            st.number_input("Maximum price age (calendar days)", min_value=0, max_value=365, value=report["policy"]["max_price_age_days"], step=1, key="trust_price_max_age_days", on_change=_save_policy)
            st.caption("Age exceeding the rule is stale. Unknown FX dates block aggregates. Stale or undated prices carry warnings. These are configurable review rules, not validated confidence scores or exchange calendars.")
        path = Path(__file__).resolve().parents[2] / "docs" / "MATH_METHODOLOGY.md"
        st.download_button("Calculation methodology", path.read_text(), file_name="MATH_METHODOLOGY.md", mime="text/markdown", key=key_prefix + ("trust_methodology_rejected" if rejected else "trust_methodology_accepted"))
