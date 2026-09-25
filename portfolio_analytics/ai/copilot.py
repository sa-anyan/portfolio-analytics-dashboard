"""OpenAI Copilot orchestration for Portfolio Analytics v4.

Natural language is interpreted by the router. Validated instructions are then
executed by deterministic Python tools. OpenAI only explains deterministic output.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any

from portfolio_analytics.ai.router import ROUTER_INSTRUCTION, parse_route_text
from portfolio_analytics.scenarios.engine import run_scenario
from portfolio_analytics.analytics.engine import historical_attribution


#______________________________________________________________________________
# CONFIGURATION
#______________________________________________________________________________

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")


#______________________________________________________________________________
# EXPLANATION INSTRUCTIONS
#______________________________________________________________________________

EXPLAINER_INSTRUCTION = r"""
You are the explanation layer for a deterministic portfolio analytics system.

Rules:
- Portfolio-specific numbers in the supplied RESULT are authoritative.
- Never recalculate, estimate, change or invent portfolio values.
- Explain in plain English first, then state the relevant limitation or assumption.
- Do not present a portfolio as universally "safe" or "unsafe" from one metric.
- For VaR/Expected Shortfall, state the horizon and historical/model nature when available.
- For scenarios, distinguish a modelled hypothetical result from a forecast.
- Do not provide personalised buy/sell instructions.
- If a requested value is unavailable, say it is unavailable and why.
- Keep answers concise unless the user asks for detail.
"""

DASHBOARD_INSIGHT_INSTRUCTION = r"""
Create short educational dashboard captions from the supplied deterministic metrics.
Return JSON only with keys: exposure, holdings, risk_contribution, volatility, var, drawdown, correlation, pnl.
Each value must be one or two short sentences.
Do not call the portfolio safe or unsafe. Explain what the metric means and what stands out using only supplied numbers.
Do not give buy/sell advice.
"""


#______________________________________________________________________________
# OPENAI CLIENT
#______________________________________________________________________________

def _client(api_key: str | None = None):
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is not installed. Install project requirements to use Copilot."
        ) from exc

    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError(
            "OpenAI is not configured. Add OPENAI_API_KEY to Streamlit secrets or the environment."
        )
    return OpenAI(api_key=key)


#______________________________________________________________________________
# COPILOT CONTEXT
#______________________________________________________________________________

def _remove_private_identifiers(value: Any) -> Any:
    blocked = {
        "transaction id",
        "transaction_id",
        "account id",
        "account_id",
        "account number",
        "account_number",
    }
    if isinstance(value, dict):
        return {
            key: _remove_private_identifiers(item)
            for key, item in value.items()
            if str(key).strip().lower() not in blocked
        }
    if isinstance(value, list):
        return [_remove_private_identifiers(item) for item in value]
    return value


def build_copilot_context(
    parsed: dict[str, Any],
    state: dict[str, Any],
    analytics: dict[str, Any],
    scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the privacy-safe dictionary view available to Copilot.

    Every engine stage is represented, while raw uploaded rows, account/transaction
    identifiers and heavy calculation matrices remain inside deterministic Python.
    """
    parsed_view = _remove_private_identifiers(deepcopy(parsed))
    user_dataset = parsed_view.get("user_dataset", {})
    raw_records = user_dataset.pop("records", []) if isinstance(user_dataset, dict) else []
    if isinstance(user_dataset, dict):
        user_dataset["record_count"] = len(raw_records)
        user_dataset["raw_records_shared_with_ai"] = False

    state_view = _remove_private_identifiers(deepcopy(state))
    state_view.pop("scenario_baseline", None)
    if isinstance(state_view.get("inputs"), dict):
        state_view["inputs"].pop("user_dataset", None)

    analytics_view = deepcopy(analytics)
    analytics_view.pop("engine_data", None)
    actual_view = analytics_view.get("actual_performance")
    if isinstance(actual_view, dict):
        path = actual_view.get("portfolio_path", [])
        actual_view["portfolio_path"] = []
        actual_view["portfolio_path_observations"] = len(path) if isinstance(path, list) else 0
        actual_view["per_position_history_shared_with_ai"] = False

    # Short recent series may help explanations, but the full historical matrices
    # stay in Python and are never required for intent routing.
    series = analytics_view.get("series", {})
    if isinstance(series, dict):
        for key, values in list(series.items()):
            if isinstance(values, list) and len(values) > 60:
                series[key] = values[-60:]
                series[f"{key}_truncated"] = True

    return {
        "parser": parsed_view,
        "portfolio_state": state_view,
        "analytics": analytics_view,
        "scenario": _remove_private_identifiers(deepcopy(scenario)) if scenario else None,
    }


#______________________________________________________________________________
# NATURAL LANGUAGE -> VALIDATED ROUTE
#______________________________________________________________________________

def route_question(
    question: str,
    context: dict[str, Any],
    *,
    conversation_history: list[dict[str, str]] | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Translate one natural-language request into a Python-validated route."""
    question = str(question or "").strip()
    if not question:
        raise ValueError("Enter a question for Copilot.")

    recent = []
    for message in (conversation_history or [])[-6:]:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            recent.append({"role": role, "content": content[:1200]})

    prompt = (
        "PORTFOLIO CONTEXT:\n"
        + json.dumps(context, default=str, separators=(",", ":"))
        + "\n\nRECENT CONVERSATION:\n"
        + json.dumps(recent, separators=(",", ":"))
        + "\n\nUSER QUESTION:\n"
        + question
    )

    response = _client(api_key).responses.create(
        model=model,
        instructions=ROUTER_INSTRUCTION,
        input=prompt,
        max_output_tokens=1000,
    )
    return parse_route_text(response.output_text, context)


#______________________________________________________________________________
# DETERMINISTIC LOOKUP TOOLS
#______________________________________________________________________________

def _position_lookup(state: dict[str, Any], ticker: str | None) -> dict[str, Any]:
    target = str(ticker or "").strip().upper()
    if not target:
        return {
            "totals": state.get("totals", {}),
            "cash": state.get("cash", {}),
            "positions": state.get("positions", []),
        }

    for row in state.get("positions", []):
        if str(row.get("ticker") or "").upper() == target:
            return {
                "position": row,
                "totals": state.get("totals", {}),
                "cash": state.get("cash", {}),
            }

    return {"error": f"{target} is not an open position in the accepted portfolio."}


def _selected_views(
    route: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Select accepted or latest hypothetical state without changing either."""
    if route.get("scope") == "latest_scenario" and context.get("scenario"):
        scenario = context["scenario"]
        return (
            scenario.get("scenario_state", context["portfolio_state"]),
            scenario.get("scenario_analytics", context["analytics"]),
            "latest_scenario",
        )
    return context["portfolio_state"], context["analytics"], "accepted"


def _lookup_result(
    route: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return deterministic values relevant to a portfolio lookup."""
    selected_state, selected_analytics, scope = _selected_views(route, context)
    return {
        "scope": scope,
        "requested_fields": route.get("fields", []),
        "ticker": route.get("ticker"),
        "parser": {
            "source": context["parser"].get("source", {}),
            "classification": context["parser"].get("classification"),
            "confidence": context["parser"].get("confidence"),
            "column_map": context["parser"].get("column_map", {}),
            "notes": context["parser"].get("notes", []),
            "issues": context["parser"].get("issues", []),
        },
        "portfolio": _position_lookup(selected_state, route.get("ticker")),
        "analytics": {
            "exposure": selected_analytics.get("exposure", {}),
            "pnl": selected_analytics.get("pnl", {}),
            "risk": selected_analytics.get("risk", {}),
            "performance": selected_analytics.get("performance", {}),
            "holdings_mix": selected_analytics.get("holdings_mix", []),
            "risk_contribution": selected_analytics.get("risk_contribution", []),
            "correlation": selected_analytics.get("correlation", {}),
            "asset_metrics": selected_analytics.get("asset_metrics", []),
            "combination_risk": selected_analytics.get("combination_risk", {}),
        },
    }


def _explanation_result(
    route: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Return the deterministic context needed for explanation questions."""
    ticker = route.get("ticker")
    selected_state, selected_analytics, scope = _selected_views(route, context)
    return {
        "scope": scope,
        "requested_fields": route.get("fields", []),
        "ticker": ticker,
        "portfolio": _position_lookup(selected_state, ticker),
        "analytics": {
            "meta": selected_analytics.get("meta", {}),
            "exposure": selected_analytics.get("exposure", {}),
            "pnl": selected_analytics.get("pnl", {}),
            "risk": selected_analytics.get("risk", {}),
            "performance": selected_analytics.get("performance", {}),
            "holdings_mix": selected_analytics.get("holdings_mix", []),
            "risk_contribution": selected_analytics.get("risk_contribution", []),
            "correlation": selected_analytics.get("correlation", {}),
            "asset_metrics": selected_analytics.get("asset_metrics", []),
            "combination_risk": selected_analytics.get("combination_risk", {}),
        },
        "latest_scenario": context.get("scenario"),
    }


#______________________________________________________________________________
# DETERMINISTIC ROUTE EXECUTION
#______________________________________________________________________________

def execute_route(
    route: dict[str, Any],
    *,
    state: dict[str, Any],
    analytics: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a validated route without calling the language model."""
    intent = route["intent"]

    if intent == "clarification":
        return {
            "kind": "clarification",
            "result": {
                "clarification": route.get("clarification")
                or "I need one more detail before I can run that safely."
            },
            "scenario": None,
        }

    if intent == "scenario":
        scenario_state = state
        scenario_analytics = analytics
        if route.get("scenario_base") == "latest_scenario" and context.get("scenario"):
            scenario_state = context["scenario"].get("scenario_state", state)
            scenario_analytics = context["scenario"].get("scenario_analytics", analytics)
        scenario = run_scenario(scenario_state, scenario_analytics, route["actions"])
        scenario.setdefault("meta", {})["conversation_base"] = route.get("scenario_base", "accepted")
        return {
            "kind": "scenario",
            "result": scenario,
            "scenario": scenario,
        }

    if intent == "historical":
        result = historical_attribution(
            analytics.get("actual_performance", {}),
            start_date=route.get("start_date"),
            end_date=route.get("end_date"),
            analysis_mode=route.get("analysis_mode") or "drawdown",
        )
        return {
            "kind": "historical",
            "result": result,
            "scenario": None,
        }

    if intent == "lookup":
        return {
            "kind": "lookup",
            "result": _lookup_result(route, context),
            "scenario": None,
        }

    if intent == "explain":
        return {
            "kind": "explain",
            "result": _explanation_result(route, context),
            "scenario": None,
        }

    return {
        "kind": "general",
        "result": {
            "portfolio_context": {
                "portfolio_totals": context["portfolio_state"].get("totals", {}),
                "analytics_meta": context["analytics"].get("meta", {}),
            },
            "note": "Answer the textbook concept without inventing portfolio calculations.",
        },
        "scenario": None,
    }


#______________________________________________________________________________
# RESULT EXPLANATION
#______________________________________________________________________________

def explain_result(
    question: str,
    result: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    prompt = (
        "USER QUESTION:\n"
        + str(question)
        + "\n\nDETERMINISTIC RESULT:\n"
        + json.dumps(result, default=str, indent=2)
    )
    response = _client(api_key).responses.create(
        model=model,
        instructions=EXPLAINER_INSTRUCTION,
        input=prompt,
        max_output_tokens=1000,
    )
    text = (response.output_text or "").strip()
    if not text:
        raise RuntimeError("OpenAI returned an empty explanation.")
    return text


#______________________________________________________________________________
# END-TO-END COPILOT QUESTION
#______________________________________________________________________________

def ask_copilot(
    question: str,
    *,
    parsed: dict[str, Any],
    state: dict[str, Any],
    analytics: dict[str, Any],
    previous_scenario: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Route, validate, execute and explain one user question."""
    context = build_copilot_context(parsed, state, analytics, previous_scenario)
    route = route_question(
        question,
        context,
        conversation_history=conversation_history,
        api_key=api_key,
        model=model,
    )
    execution = execute_route(route, state=state, analytics=analytics, context=context)

    if execution["kind"] == "clarification":
        answer = execution["result"]["clarification"]
    else:
        answer = explain_result(question, execution["result"], api_key=api_key, model=model)

    return {
        "intent": route["intent"],
        "route": route,
        "answer": answer,
        "result": execution["result"],
        "scenario": execution["scenario"],
    }


#______________________________________________________________________________
# AUTO DASHBOARD INSIGHTS
#______________________________________________________________________________

def generate_dashboard_insights(
    state: dict[str, Any],
    analytics: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, str]:
    payload = {
        "portfolio_totals": state.get("totals", {}),
        "positions": state.get("positions", []),
        "analytics": {
            "exposure": analytics.get("exposure", {}),
            "pnl": analytics.get("pnl", {}),
            "risk": analytics.get("risk", {}),
            "performance": analytics.get("performance", {}),
            "holdings_mix": analytics.get("holdings_mix", []),
            "risk_contribution": analytics.get("risk_contribution", []),
            "correlation": analytics.get("correlation", {}),
            "meta": analytics.get("meta", {}),
        },
    }
    response = _client(api_key).responses.create(
        model=model,
        instructions=DASHBOARD_INSIGHT_INSTRUCTION,
        input=json.dumps(payload, default=str, separators=(",", ":")),
        max_output_tokens=900,
    )
    text = (response.output_text or "").strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Copilot returned invalid dashboard insight JSON.") from exc
    return {str(k): str(v) for k, v in result.items()}
