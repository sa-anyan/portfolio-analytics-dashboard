"""AI interpretation and explanation layer for Portfolio Analytics v4."""

from portfolio_analytics.ai.copilot import (
    ask_copilot,
    build_copilot_context,
    execute_route,
    generate_dashboard_insights,
    route_question,
)
from portfolio_analytics.ai.router import (
    ALLOWED_INTENTS,
    ALLOWED_LOOKUP_FIELDS,
    normalise_route,
    parse_route_text,
    validate_route,
)

__all__ = [
    "ALLOWED_INTENTS",
    "ALLOWED_LOOKUP_FIELDS",
    "ask_copilot",
    "build_copilot_context",
    "execute_route",
    "generate_dashboard_insights",
    "normalise_route",
    "parse_route_text",
    "route_question",
    "validate_route",
]
