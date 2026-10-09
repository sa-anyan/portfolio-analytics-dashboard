"""Phase 4 audit reproduction: snapshots cannot establish realised P&L."""
import pandas as pd

from tests.test_lookthrough import fixture
from tests.test_math_regressions import state, DATES
from portfolio_analytics.analytics.engine import run_analytics
from portfolio_analytics.core.portfolio_state import refresh_state_totals
from portfolio_analytics.ai.copilot import build_copilot_context, _explanation_result
from portfolio_analytics.scenarios.engine import run_scenario
from portfolio_analytics.scenarios.engine import apply_resize
from portfolio_analytics.ui.insights import fallback_insights, _money


def test_exact_audit_daniel_snapshot_unavailable_through_scenario_ui_and_copilot():
    canonical, parsed, *_ = fixture()
    assert canonical['totals']['equity'] == 6300
    assert canonical['totals']['realised_pnl'] is None
    assert all(p['realised_pnl'] is None for p in canonical['positions'])
    assert refresh_state_totals(canonical)['totals']['realised_pnl'] is None
    prices = pd.DataFrame({p['ticker']: [p['current_price']]*4 for p in canonical['positions']}, index=DATES)
    analytics = run_analytics(canonical, prices)
    assert analytics['pnl']['realised'] is None
    assert 'Realised P&L is unavailable' in fallback_insights(canonical, analytics)['pnl']
    context = build_copilot_context(parsed, canonical, analytics)
    assert context['portfolio_state']['totals']['realised_pnl'] is None
    assert _explanation_result({'fields': ['pnl']}, context)['analytics']['pnl']['realised'] is None
    for action in ({'action': 'price_shock', 'ticker': 'NVDA', 'value': -.1},
                   {'action': 'resize', 'ticker': 'NVDA', 'target_quantity': 5}):
        scenario = run_scenario(canonical, analytics, [action])
        assert scenario['scenario_state']['totals']['realised_pnl'] is None
        assert all(p['realised_pnl'] is None for p in scenario['scenario_state']['positions'])
    assert canonical['totals']['realised_pnl'] is None
    assert _money(None) == 'unavailable'
    assert _money(0) == '$0.00'


def test_calculated_ledger_zero_and_realised_gain_remain_known():
    ledger = state({'Date': [DATES[0]], 'Type': ['BUY'], 'Ticker': ['AAA'],
                    'Quantity': [10], 'Price': [100]}, cash=1000, latest_prices={'AAA': 110})
    assert ledger['totals']['realised_pnl'] == 0
    assert ledger['positions'][0]['realised_pnl'] == 0
    analytics = run_analytics(ledger, pd.DataFrame({'AAA': [110]*4}, index=DATES))
    assert 'Realised P&L is $0.00' in fallback_insights(ledger, analytics)['pnl']
    scenario = run_scenario(ledger, analytics, [{'action': 'resize', 'ticker': 'AAA', 'target_quantity': 5}])
    assert scenario['scenario_state']['totals']['realised_pnl'] == 50
    assert scenario['scenario_state']['positions'][0]['realised_pnl'] == 50


def test_snapshot_disposal_without_basis_has_unavailable_incremental_pnl():
    canonical = state({'Ticker':['AAA'], 'Quantity':[10], 'Current Price':[100]})
    for target in (5, 0, -5):
        resized, detail = apply_resize(canonical, 'AAA', target_quantity=target)
        assert detail['realised_pnl_change'] is None
        assert resized['totals']['realised_pnl'] is None
        assert resized['totals']['equity'] == canonical['totals']['equity']
    # No disposal genuinely realises nothing, even if earlier history is unknown.
    for target in (10, 15):
        resized, detail = apply_resize(canonical, 'AAA', target_quantity=target)
        assert detail['realised_pnl_change'] == 0
        assert resized['totals']['realised_pnl'] is None


def test_mixed_consolidation_preserves_known_position_only_when_all_its_accounts_are_known():
    from tests.test_consolidation import snapshot, declarations, csv
    from portfolio_analytics.input_engine.batch import ingest_files
    from portfolio_analytics.input_engine.review import confirm_review
    from portfolio_analytics.input_engine.consolidate import prepare_consolidation
    ledger = csv([
        dict(Account='Ledger', Date='2026-01-02', Type='DEPOSIT', Amount=100, Currency='USD'),
        dict(Account='Ledger', Date='2026-01-03', Type='BUY', Ticker='X', Quantity=2, Price=10, Currency='USD'),
        dict(Account='Ledger', Date='2026-01-04', Type='SELL', Ticker='X', Quantity=1, Price=12, Currency='USD'),
        dict(Account='Ledger', Date='2026-01-05', Type='BUY', Ticker='Z', Quantity=1, Price=5, Currency='USD')])
    for snapshot_ticker in ('Y', 'X'):
        batch = confirm_review(ingest_files([('ledger.csv', ledger),
            ('snapshot.csv', snapshot('Snapshot', 1, ticker=snapshot_ticker))]))
        result = prepare_consolidation(batch, declarations=declarations(['Ledger']), latest_prices={'X':12, 'Z':5})
        assert result['ready'], result['errors']
        canonical = result['state']
        assert canonical['totals']['realised_pnl'] is None
        assert result['reconciliation']['residuals']['realised_pnl'] is None
        positions = {p['ticker']:p for p in canonical['positions']}
        assert positions['X']['realised_pnl'] == (2 if snapshot_ticker == 'Y' else None)
        assert positions['Z']['realised_pnl'] == 0
        assert positions[snapshot_ticker]['realised_pnl'] is None
