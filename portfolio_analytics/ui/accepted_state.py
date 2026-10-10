"""Replace an already validated candidate and invalidate portfolio-dependent UI state."""
from copy import deepcopy

from portfolio_analytics.ui.insights import fallback_insights

DEPENDENT_KEYS = {
    'latest_scenario', 'copilot_question', 'copilot_messages', 'copilot_insights',
    'accepted_consolidation_batch', 'consolidation_candidate', 'consolidation_accepted_key',
    'exposure_selected_identity', 'exposure_security', 'constituent_parent',
    'constituent_draft', 'fund_constituents', 'fund_eligibility', 'eligibility_parent',
    'show_actual_portfolio_performance', 'dashboard_focus',
}


def replace_accepted(session, parsed, state, analytics, metadata, *, batch=None):
    # Construct everything before touching the previously accepted workspace.
    updates = deepcopy({'parsed': parsed, 'portfolio_state': state, 'analytics': analytics,
                        'market_metadata': metadata, 'accepted_consolidation_batch': batch})
    updates.update(latest_scenario=None, trust_attempt=None, copilot_messages=[],
                   copilot_insights=fallback_insights(state, analytics))
    for key in list(session):
        if key in DEPENDENT_KEYS or key.startswith(('constituent_', 'scenario_', 'bi_', 'macro_')):
            del session[key]
    session.update(updates)
