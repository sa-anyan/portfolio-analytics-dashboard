"""Natural-language intent router for Portfolio Analytics v4.

The router translates a user's question into a small validated instruction object.
It never performs portfolio mathematics. Deterministic Python engines execute the
validated instruction after routing.
"""

#______________________________________________________________________________
# IMPORT LIBRARIES
#______________________________________________________________________________

from __future__ import annotations

import json
import math
from typing import Any

from portfolio_analytics.scenarios.engine import validate_actions


#______________________________________________________________________________
# ROUTER CONTRACT
#______________________________________________________________________________

ALLOWED_INTENTS = {
    "lookup",
    "explain",
    "scenario",
    "general",
    "historical",
    "clarification",
}

ALLOWED_LOOKUP_FIELDS = {
    "quantity",
    "side",
    "current_price",
    "average_entry_price",
    "signed_market_value",
    "exposure",
    "cost_basis",
    "realised_pnl",
    "unrealised_pnl",
    "cash",
    "equity",
    "long_exposure",
    "short_exposure",
    "gross_exposure",
    "net_exposure",
    "gross_leverage",
    "net_leverage",
    "annual_return",
    "annual_volatility",
    "sharpe",
    "var_pct",
    "var_value",
    "expected_shortfall_pct",
    "expected_shortfall_value",
    "max_drawdown",
    "risk_contribution",
    "correlation",
    "performance",
    "combination_risk",
    "lowest_volatility_combination",
    "smallest_drawdown_combination",
    "lowest_var_combination",
    "lowest_expected_shortfall_combination",
    "highest_tail_risk_combination",
}

TICKER_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


#______________________________________________________________________________
# SYSTEM INSTRUCTION
#______________________________________________________________________________

ROUTER_INSTRUCTION = r"""
You are the intent router for a deterministic portfolio analytics application.
Your only job is to translate the user's request into JSON that Python can validate
and execute. Never calculate portfolio values yourself.

Allowed intents:
- lookup: retrieve existing portfolio/state/analytics values.
- explain: explain an existing deterministic result or why a metric behaves as it does.
- scenario: model one or more hypothetical changes.
- general: textbook finance education that does not require a portfolio calculation.
- historical: interrogate reconstructed historical portfolio behaviour, including a period, drawdown, recovery, or contribution by ticker.
- clarification: one missing detail prevents safe deterministic execution.

Supported scenario actions:

1. price_shock
{"action":"price_shock","ticker":"BTC-USD","value":-20}
value is percentage price change.

2. rate_shock
{"action":"rate_shock","value":100,"tickers":null}
value is basis points. Python applies the shock only to positions with duration or
explicit rate sensitivity.

3. resize
Use exactly one resize target:
{"action":"resize","ticker":"BTC-USD","percent_change":-50}
{"action":"resize","ticker":"NVDA","target_quantity":25}
{"action":"resize","ticker":"GLD","target_exposure":5000}

4. remove_position
{"action":"remove_position","ticker":"GLD"}

5. reallocation
{"action":"reallocation","from_ticker":"BTC-USD","to_ticker":"GLD","percent_of_source_exposure":20,"amount":null,"to_price":null,"to_side":"LONG"}
Or use amount instead of percent_of_source_exposure.

6. target_risk
{"action":"target_risk","target_annual_volatility":10,"allow_leverage":false}
An unqualified request like "make my risk 10%" is ambiguous unless the conversation
or question clearly means annualised volatility.

Routing rules:
- Preserve multiple scenario actions in the order the user stated them.
- "halve my position" means resize percent_change -50.
- "double my position" means resize percent_change +100.
- "close", "remove" or "exit" an entire position means remove_position.
- Do not invent tickers, prices, durations, sensitivities or portfolio values.
- If the user refers to a position that cannot be identified confidently, clarify.
- If reallocation creates a ticker not currently held and no target price is available,
  clarify instead of inventing a price.
- Rate shocks are not a universal equity/crypto pricing model. Route the request to
  rate_shock and let Python report which positions have a usable sensitivity model.
- If "risk" could mean volatility, VaR, drawdown or another measure, clarify.
- For questions such as "what caused the drawdown in 2020?", "which ticker contributed most?", "what happened in 2022?", or "what drove the recovery?", use intent historical.
- For historical requests set analysis_mode to "drawdown" when the question concerns a drawdown/trough/loss episode, otherwise "period".
- Convert an explicit year to start_date YYYY-01-01 and end_date YYYY-12-31.
- Convert an explicit month/year to that calendar month.
- If no period is specified, leave start_date and end_date null so Python uses the available reconstructed history.
- "caused" in a historical portfolio question means portfolio return attribution by holding. Do not claim external economic/news causation.
- Never give personalised buy/sell recommendations.

Lookup fields should use these canonical names when possible:
quantity, side, current_price, average_entry_price, signed_market_value, exposure,
cost_basis, realised_pnl, unrealised_pnl, cash, equity, long_exposure,
short_exposure, gross_exposure, net_exposure, gross_leverage, net_leverage,
annual_return, annual_volatility, sharpe, var_pct, var_value,
expected_shortfall_pct, expected_shortfall_value, max_drawdown,
risk_contribution, correlation, performance, combination_risk,
lowest_volatility_combination, smallest_drawdown_combination, lowest_var_combination,
lowest_expected_shortfall_combination, highest_tail_risk_combination.

Conversation rules:
- Recent conversation may be supplied. Resolve pronouns such as "it", "that position",
  and "after that" only when the referenced ticker or scenario is unambiguous.
- scope = "accepted" means answer from the user's accepted portfolio.
- scope = "latest_scenario" means answer from the most recent hypothetical scenario.
- scenario_base = "accepted" starts a new hypothetical from the accepted portfolio.
- scenario_base = "latest_scenario" continues the most recent hypothetical scenario.
- Use latest_scenario only when the user clearly refers to the prior hypothetical, for
  example "then halve it", "after that", or "what is my VaR now?".
- If there is no latest scenario, use accepted.

Return exactly this JSON shape:
{
  "intent": "lookup|explain|scenario|general|clarification",
  "ticker": null,
  "fields": [],
  "actions": [],
  "scope": "accepted|latest_scenario",
  "scenario_base": "accepted|latest_scenario",
  "start_date": null,
  "end_date": null,
  "analysis_mode": null,
  "clarification": null
}

Return JSON only. No markdown and no prose outside the JSON.
"""


#______________________________________________________________________________
# SMALL HELPERS
#______________________________________________________________________________

def standardise_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper()
    if not ticker:
        return None
    return TICKER_ALIASES.get(ticker, ticker)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def open_tickers(context: dict[str, Any]) -> set[str]:
    return {
        str(row.get("ticker") or "").strip().upper()
        for row in context.get("portfolio_state", {}).get("positions", [])
        if str(row.get("ticker") or "").strip()
    }


def known_prices(context: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in context.get("portfolio_state", {}).get("positions", []):
        ticker = standardise_ticker(row.get("ticker"))
        price = _finite_number(row.get("current_price"))
        if ticker and price is not None and price > 0:
            result[ticker] = price
    return result


#______________________________________________________________________________
# ROUTE NORMALISATION
#______________________________________________________________________________

def normalise_route(route: dict[str, Any]) -> dict[str, Any]:
    """Return one predictable route shape before deterministic validation."""
    if not isinstance(route, dict):
        raise ValueError("Copilot route must be a dictionary.")

    normalised = {
        "intent": str(route.get("intent") or "").strip().lower(),
        "ticker": standardise_ticker(route.get("ticker")),
        "fields": route.get("fields") if isinstance(route.get("fields"), list) else [],
        "actions": route.get("actions") if isinstance(route.get("actions"), list) else [],
        "scope": str(route.get("scope") or "accepted").strip().lower(),
        "scenario_base": str(route.get("scenario_base") or "accepted").strip().lower(),
        "start_date": str(route.get("start_date") or "").strip() or None,
        "end_date": str(route.get("end_date") or "").strip() or None,
        "analysis_mode": str(route.get("analysis_mode") or "").strip().lower() or None,
        "clarification": route.get("clarification"),
    }

    fields: list[str] = []
    for field in normalised["fields"]:
        canonical = str(field or "").strip().lower().replace(" ", "_")
        if canonical and canonical not in fields:
            fields.append(canonical)
    normalised["fields"] = fields

    actions: list[dict[str, Any]] = []
    for raw in normalised["actions"]:
        if not isinstance(raw, dict):
            raise ValueError("Every Copilot scenario action must be a dictionary.")
        item = dict(raw)
        action = str(item.get("action") or "").strip().lower()
        item["action"] = action

        if "ticker" in item:
            item["ticker"] = standardise_ticker(item.get("ticker"))
        if "from_ticker" in item:
            item["from_ticker"] = standardise_ticker(item.get("from_ticker"))
        if "to_ticker" in item:
            item["to_ticker"] = standardise_ticker(item.get("to_ticker"))
        if isinstance(item.get("tickers"), list):
            item["tickers"] = [
                ticker
                for ticker in (standardise_ticker(value) for value in item["tickers"])
                if ticker
            ]
        actions.append(item)

    normalised["actions"] = actions

    if normalised["clarification"] is not None:
        normalised["clarification"] = str(normalised["clarification"]).strip() or None

    return normalised


#______________________________________________________________________________
# ROUTE VALIDATION
#______________________________________________________________________________

def validate_route(
    route: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Validate an AI route against both schema and the accepted portfolio.

    This is the security boundary between language interpretation and deterministic
    financial functions. The LLM may propose instructions; Python decides whether
    those instructions are allowed to execute.
    """
    route = normalise_route(route)
    intent = route["intent"]

    if route["scope"] not in {"accepted", "latest_scenario"}:
        raise ValueError("Copilot scope must be accepted or latest_scenario.")
    if route["scenario_base"] not in {"accepted", "latest_scenario"}:
        raise ValueError("Scenario base must be accepted or latest_scenario.")
    if context.get("scenario") is None:
        route["scope"] = "accepted"
        route["scenario_base"] = "accepted"

    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"Unsupported Copilot intent: {intent or '<empty>'}")

    unknown_fields = [field for field in route["fields"] if field not in ALLOWED_LOOKUP_FIELDS]
    if unknown_fields:
        raise ValueError(f"Unsupported lookup field(s): {', '.join(unknown_fields)}")

    if intent == "clarification":
        if not route.get("clarification"):
            route["clarification"] = "I need one more detail before I can run that safely."
        route["actions"] = []
        return route

    if intent != "scenario" and route["actions"]:
        raise ValueError(f"Intent '{intent}' cannot contain scenario actions.")

    if intent == "historical":
        if route.get("analysis_mode") not in {None, "drawdown", "period", "drawdown_attribution"}:
            raise ValueError("Historical analysis_mode must be drawdown or period.")
        for key in ("start_date", "end_date"):
            value = route.get(key)
            if value:
                try:
                    import datetime as _dt
                    _dt.date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{key} must be an ISO date YYYY-MM-DD.") from exc
        return route

    accepted_tickers = open_tickers(context)
    selected_state = context.get("portfolio_state", {})
    if route.get("scope") == "latest_scenario" and context.get("scenario"):
        selected_state = context["scenario"].get("scenario_state", selected_state)
    selected_tickers = {
        str(row.get("ticker") or "").strip().upper()
        for row in selected_state.get("positions", [])
        if str(row.get("ticker") or "").strip()
    }

    if intent in {"lookup", "explain"} and route.get("ticker"):
        if route["ticker"] not in selected_tickers:
            label = "latest scenario" if route.get("scope") == "latest_scenario" else "accepted portfolio"
            raise ValueError(f"{route['ticker']} is not an open position in the {label}.")

    if intent != "scenario":
        return route

    actions = route.get("actions", [])
    if not actions:
        raise ValueError("Copilot identified a scenario but returned no actions.")

    # First enforce the scenario engine's own numerical/action contract.
    validate_actions(actions)

    base_state = context.get("portfolio_state", {})
    if route.get("scenario_base") == "latest_scenario" and context.get("scenario"):
        base_state = context["scenario"].get("scenario_state", base_state)
    base_tickers = {
        str(row.get("ticker") or "").strip().upper()
        for row in base_state.get("positions", [])
        if str(row.get("ticker") or "").strip()
    }
    prices = {
        str(row.get("ticker") or "").strip().upper(): float(row.get("current_price"))
        for row in base_state.get("positions", [])
        if str(row.get("ticker") or "").strip() and row.get("current_price") is not None
    }
    for item in actions:
        action = item["action"]

        if action in {"price_shock", "resize", "remove_position"}:
            ticker = item.get("ticker")
            base_state = context.get("portfolio_state", {})
            if route.get("scenario_base") == "latest_scenario" and context.get("scenario"):
                base_state = context["scenario"].get("scenario_state", base_state)
            base_tickers = {
                str(row.get("ticker") or "").strip().upper()
                for row in base_state.get("positions", [])
                if str(row.get("ticker") or "").strip()
            }
            if ticker not in base_tickers:
                raise ValueError(f"{ticker or 'Requested ticker'} is not an open position in the selected scenario base.")

        elif action == "rate_shock":
            requested = item.get("tickers")
            if requested:
                missing = sorted(set(requested) - base_tickers)
                if missing:
                    raise ValueError(
                        "Rate-shock ticker(s) are not open positions: " + ", ".join(missing)
                    )

        elif action == "reallocation":
            source = item.get("from_ticker")
            target = item.get("to_ticker")
            if source not in base_tickers:
                raise ValueError(f"Reallocation source {source} is not an open position.")
            if target not in base_tickers:
                supplied_price = _finite_number(item.get("to_price"))
                if supplied_price is None or supplied_price <= 0:
                    raise ValueError(
                        f"Reallocation target {target} is not currently held and needs a positive to_price."
                    )
            elif item.get("to_price") is None and target in prices:
                # Existing positions do not need the LLM to repeat a market price.
                item["to_price"] = None

    return route


#______________________________________________________________________________
# MODEL OUTPUT PARSING
#______________________________________________________________________________

def parse_route_text(
    text: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("OpenAI returned an empty route.")

    try:
        route = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Copilot returned invalid route JSON.") from exc

    return validate_route(route, context)
