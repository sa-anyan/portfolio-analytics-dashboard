"""Offline educational captions used when OpenAI insights are unavailable."""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from typing import Any


#______________________________________________________________________________
# FORMATTERS
#______________________________________________________________________________

def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "unavailable"


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "unavailable"


#______________________________________________________________________________
# FALLBACK INSIGHTS
#______________________________________________________________________________

def fallback_insights(state: dict[str, Any], analytics: dict[str, Any]) -> dict[str, str]:
    risk = analytics.get("risk", {})
    exposure = analytics.get("exposure", {})
    performance = analytics.get("performance", {})
    pnl = analytics.get("pnl", {})
    rc = analytics.get("risk_contribution", [])
    mix = analytics.get("holdings_mix", [])

    largest_holding = max(mix, key=lambda row: row.get("exposure_share", 0.0), default=None)
    largest_risk = max(rc, key=lambda row: row.get("absolute_risk_share", 0.0), default=None)

    holdings_text = (
        f"{largest_holding['ticker']} is the largest gross-exposure share at {_pct(largest_holding.get('exposure_share'))}."
        if largest_holding
        else "Holdings concentration is unavailable until positions can be valued."
    )
    risk_text = (
        f"{largest_risk['ticker']} contributes the largest absolute share of modelled volatility at {_pct(largest_risk.get('absolute_risk_share'))}."
        if largest_risk
        else "Risk contribution needs enough overlapping return history to estimate covariance."
    )

    return {
        "exposure": (
            f"Gross exposure is {_money(exposure.get('gross'))} and net exposure is {_money(exposure.get('net'))}. "
            "Gross measures total market exposure; net reflects long exposure minus short exposure."
        ),
        "holdings": holdings_text,
        "risk_contribution": risk_text,
        "volatility": (
            f"Annualised historical volatility is {_pct(risk.get('annual_volatility'))}. "
            "This describes how widely the current signed book moved historically; it is not a forecast."
        ),
        "var": (
            f"Historical VaR is {_money(risk.get('var_value'))} ({_pct(risk.get('var_pct'))}) at the selected confidence level. "
            "It is a loss threshold from historical daily returns, not a maximum possible loss or a universal safe/unsafe test."
        ),
        "drawdown": (
            f"Maximum drawdown is {_pct(performance.get('max_drawdown'))}. "
            "Drawdown measures the largest peak-to-trough decline in the modelled or reconstructed performance path."
        ),
        "correlation": (
            "Correlation shows which holdings tended to move together historically. Values near +1 moved together, near -1 moved oppositely, and near 0 had little linear relationship."
        ),
        "pnl": (
            f"Realised P&L is {_money(pnl.get('realised'))} and unrealised P&L is {_money(pnl.get('unrealised'))}. "
            "Unrealised P&L is only available where a usable cost basis exists."
        ),
    }
