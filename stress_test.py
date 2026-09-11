"""Scenario stress testing for current portfolio positions.

This module keeps the simple, auditable stress-test behaviour from the earlier
portfolio prototype while supporting the signed market values used by the
current accounting engine.
"""
from __future__ import annotations

import pandas as pd


def run_stress_test(
    portfolio: pd.DataFrame,
    scenario_changes: dict[str, float],
    value_column: str | None = None,
):
    """Apply per-security percentage price shocks to a portfolio value column.

    Parameters
    ----------
    portfolio:
        Must contain ``Ticker`` and either ``Signed Market Value`` or
        ``Market Value`` (or a custom ``value_column``).
    scenario_changes:
        Mapping such as ``{"AAPL": -0.10, "GLD": 0.05}``.
    value_column:
        Optional explicit valuation column. Signed values correctly preserve
        long/short economics.
    """
    if portfolio is None or portfolio.empty:
        empty = pd.DataFrame()
        return empty, 0.0, 0.0, 0.0

    if "Ticker" not in portfolio.columns:
        raise ValueError("Stress-test input must contain a Ticker column.")

    if value_column is None:
        if "Signed Market Value" in portfolio.columns:
            value_column = "Signed Market Value"
        elif "Market Value" in portfolio.columns:
            value_column = "Market Value"
        else:
            raise ValueError(
                "Stress-test input must contain Signed Market Value or Market Value."
            )

    if value_column not in portfolio.columns:
        raise ValueError(f"Stress-test value column '{value_column}' was not found.")

    stress_results = portfolio.copy()
    stress_results[value_column] = pd.to_numeric(
        stress_results[value_column], errors="coerce"
    ).fillna(0.0)
    stress_results["Scenario Change"] = (
        stress_results["Ticker"].astype(str).map(scenario_changes).fillna(0.0)
    )
    stress_results["Stressed Value"] = stress_results[value_column] * (
        1.0 + stress_results["Scenario Change"]
    )
    stress_results["Impact"] = (
        stress_results["Stressed Value"] - stress_results[value_column]
    )

    original_value = float(stress_results[value_column].sum())
    stressed_value = float(stress_results["Stressed Value"].sum())
    portfolio_change = stressed_value - original_value
    portfolio_change_percent = (
        portfolio_change / original_value if abs(original_value) > 1e-12 else 0.0
    )

    return (
        stress_results,
        stressed_value,
        portfolio_change,
        portfolio_change_percent,
    )
