"""Historical equal-weight combination risk analytics.

This module answers a narrow question: how did groups of securities from the
user's portfolio behave together historically? It does not optimise weights and
it does not forecast future returns.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from itertools import combinations
from math import comb
from typing import Any, Iterable

import numpy as np
import pandas as pd

from portfolio_analytics.analytics.engine import TRADING_DAYS


#______________________________________________________________________________
# CONSTANTS
#______________________________________________________________________________

MIN_COMMON_OBSERVATIONS = 30
MAX_COMBINATIONS = 100_000


#______________________________________________________________________________
# HELPERS
#______________________________________________________________________________

def _clean_returns(returns: pd.DataFrame) -> pd.DataFrame:
    if returns is None or returns.empty:
        return pd.DataFrame()
    clean = returns.copy()
    clean.columns = [str(c).strip().upper() for c in clean.columns]
    clean = clean.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return clean


def eligible_tickers(
    returns: pd.DataFrame,
    portfolio_tickers: Iterable[str],
    *,
    min_observations: int = MIN_COMMON_OBSERVATIONS,
) -> tuple[list[str], dict[str, str]]:
    """Return every portfolio ticker individually eligible for combination analysis."""
    clean = _clean_returns(returns)
    eligible: list[str] = []
    excluded: dict[str, str] = {}
    seen: set[str] = set()

    for raw in portfolio_tickers:
        ticker = str(raw or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        if ticker not in clean.columns:
            excluded[ticker] = "No historical return series is available."
            continue
        observations = int(clean[ticker].notna().sum())
        if observations < min_observations:
            excluded[ticker] = f"Only {observations} historical observations are available."
            continue
        eligible.append(ticker)
    return eligible, excluded


def _combination_metrics(
    selected: pd.DataFrame,
    tickers: tuple[str, ...],
    *,
    var_level: float,
) -> dict[str, Any] | None:
    aligned = selected[list(tickers)].dropna(how="any")
    if len(aligned) < MIN_COMMON_OBSERVATIONS:
        return None

    # Equal weights deliberately isolate asset grouping from weight optimisation.
    portfolio_returns = aligned.mean(axis=1)
    mean_daily = float(portfolio_returns.mean())
    annual_return = mean_daily * TRADING_DAYS
    annual_volatility = float(portfolio_returns.std(ddof=1)) * np.sqrt(TRADING_DAYS)

    cutoff = float(portfolio_returns.quantile(1.0 - var_level))
    var_pct = max(0.0, -cutoff)
    tail = portfolio_returns[portfolio_returns <= cutoff]
    es_pct = max(0.0, -float(tail.mean())) if not tail.empty else None

    wealth = (1.0 + portfolio_returns).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    max_drawdown = float(drawdown.min())

    return {
        "combination": list(tickers),
        "label": " + ".join(tickers),
        "size": len(tickers),
        "weighting": "equal_weight",
        "weight_each": 1.0 / len(tickers),
        "observations": int(len(aligned)),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility),
        "max_drawdown": max_drawdown,
        "var_pct": float(var_pct),
        "expected_shortfall_pct": float(es_pct) if es_pct is not None and np.isfinite(es_pct) else None,
    }


def _lowest(rows: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    valid = [row for row in rows if row.get(field) is not None and np.isfinite(float(row[field]))]
    if not valid:
        return None
    if field == "max_drawdown":
        # Drawdowns are negative. Closest to zero is the least severe.
        return max(valid, key=lambda row: float(row[field]))
    return min(valid, key=lambda row: float(row[field]))


def _highest_risk(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [row for row in rows if row.get("expected_shortfall_pct") is not None]
    return max(valid, key=lambda row: float(row["expected_shortfall_pct"])) if valid else None


#______________________________________________________________________________
# PUBLIC ENGINE
#______________________________________________________________________________

def calculate_combination_risk(
    returns: pd.DataFrame,
    portfolio_tickers: Iterable[str],
    *,
    combination_size: int = 2,
    selected_tickers: Iterable[str] | None = None,
    var_level: float = 0.95,
    max_combinations: int = MAX_COMBINATIONS,
) -> dict[str, Any]:
    """Evaluate historical risk for equal-weight combinations of portfolio assets.

    All portfolio tickers are considered for eligibility. ``selected_tickers`` only
    narrows the analyst's requested universe; it never mutates Portfolio State.
    """
    clean = _clean_returns(returns)
    portfolio = [str(t or "").strip().upper() for t in portfolio_tickers if str(t or "").strip()]
    eligible, excluded = eligible_tickers(clean, portfolio)

    if selected_tickers is None:
        universe = eligible
    else:
        requested = [str(t or "").strip().upper() for t in selected_tickers]
        universe = [ticker for ticker in eligible if ticker in requested]

    size = int(combination_size)
    if size < 2:
        raise ValueError("Combination size must be at least 2.")
    if size > len(universe):
        return {
            "available": False,
            "reason": f"Combination size {size} requires at least {size} eligible tickers.",
            "methodology": "equal-weight historical combinations",
            "combination_size": size,
            "portfolio_tickers": portfolio,
            "eligible_tickers": eligible,
            "excluded_tickers": excluded,
            "selected_tickers": universe,
            "candidate_count": 0,
            "evaluated_count": 0,
            "results": [],
        }

    candidate_count = comb(len(universe), size)
    if candidate_count > int(max_combinations):
        return {
            "available": False,
            "reason": (
                f"{candidate_count:,} combinations would be required, above the "
                f"{int(max_combinations):,} safety limit. Reduce the selected tickers or combination size."
            ),
            "methodology": "equal-weight historical combinations",
            "combination_size": size,
            "portfolio_tickers": portfolio,
            "eligible_tickers": eligible,
            "excluded_tickers": excluded,
            "selected_tickers": universe,
            "candidate_count": int(candidate_count),
            "evaluated_count": 0,
            "results": [],
        }

    rows: list[dict[str, Any]] = []
    skipped_common_history = 0
    for group in combinations(universe, size):
        row = _combination_metrics(clean, group, var_level=float(var_level))
        if row is None:
            skipped_common_history += 1
        else:
            rows.append(row)

    return {
        "available": bool(rows),
        "reason": None if rows else "No combinations have at least 30 common historical observations.",
        "methodology": "equal-weight historical combinations",
        "interpretation": (
            "Compares historical risk of asset groupings using equal weights. "
            "It isolates which securities historically worked together from the separate question of optimal weights."
        ),
        "var_level": float(var_level),
        "combination_size": size,
        "portfolio_tickers": portfolio,
        "eligible_tickers": eligible,
        "excluded_tickers": excluded,
        "selected_tickers": universe,
        "candidate_count": int(candidate_count),
        "evaluated_count": len(rows),
        "skipped_insufficient_common_history": int(skipped_common_history),
        "lowest_volatility": _lowest(rows, "annual_volatility"),
        "smallest_drawdown": _lowest(rows, "max_drawdown"),
        "lowest_var": _lowest(rows, "var_pct"),
        "lowest_expected_shortfall": _lowest(rows, "expected_shortfall_pct"),
        "highest_tail_risk": _highest_risk(rows),
        "results": rows,
    }
