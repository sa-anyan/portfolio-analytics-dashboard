"""Gemini-backed explanation layer for portfolio analytics.

This module never calculates portfolio metrics. It only explains a sanitised
snapshot produced by the deterministic analytics/accounting engine.
"""

from __future__ import annotations

import json
import os
from typing import Any

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """You are the AI Portfolio Analyst inside a portfolio analytics application.

Rules:
1. Treat the supplied portfolio context as the only authoritative source for portfolio-specific numbers.
2. Never invent, recalculate, estimate, or silently alter portfolio metrics.
3. Clearly say when a requested figure is unavailable in the supplied context.
4. Explain financial concepts in plain English first, then add technical detail when useful.
5. Distinguish historical observations from forecasts.
6. Do not claim certainty about future returns or market movements.
7. Do not provide personalised investment instructions or tell the user what they should buy or sell.
8. You may explain concentration, volatility, drawdown, VaR, expected shortfall, Sharpe ratio,
   correlation, exposures, P&L, stress-test outputs, and risk contribution using the supplied data.
9. Do not expose or request account numbers, names, addresses, credentials, API keys, or other
   unnecessary personal information.
10. Be concise, specific, and transparent about limitations.
"""


def _safe_value(value: Any) -> Any:
    """Convert common analytics values into JSON-safe primitives."""
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    try:
        return value.item()
    except (AttributeError, ValueError):
        return str(value)


def sanitise_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep only structured analytics supplied by the application."""
    clean: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, dict):
            clean[key] = sanitise_context(value)
        elif isinstance(value, list):
            clean[key] = [
                sanitise_context(item) if isinstance(item, dict) else _safe_value(item)
                for item in value
            ]
        else:
            clean[key] = _safe_value(value)
    return clean


def _client(api_key: str | None = None) -> genai.Client:
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "Gemini is not configured. Add GEMINI_API_KEY to Streamlit secrets "
            "or the server environment."
        )
    return genai.Client(api_key=key)


def ask_portfolio_analyst(
    question: str,
    portfolio_context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = MODEL,
) -> str:
    """Answer a question using deterministic portfolio outputs as context."""
    question = str(question or "").strip()
    if not question:
        raise ValueError("Enter a question for the AI Portfolio Analyst.")

    safe_context = sanitise_context(portfolio_context)
    prompt = (
        "PORTFOLIO CONTEXT (authoritative application output):\n"
        + json.dumps(safe_context, indent=2, sort_keys=True)
        + "\n\nUSER QUESTION:\n"
        + question
    )

    # Keep the client alive for the full request. Some SDK/runtime combinations
    # can otherwise release the temporary client before the underlying HTTP
    # transport has completed the call.
    client = _client(api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=900,
            ),
        )
    finally:
        client.close()

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def explain_portfolio(
    portfolio_context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = MODEL,
) -> str:
    """Generate a concise explanation of the supplied portfolio results."""
    return ask_portfolio_analyst(
        "Explain the most important portfolio results, the main risk drivers, "
        "and any important limitations in plain English. Do not give buy/sell advice.",
        portfolio_context,
        api_key=api_key,
        model=model,
    )
