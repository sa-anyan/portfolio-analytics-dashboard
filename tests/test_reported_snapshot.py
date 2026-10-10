from copy import deepcopy
from pathlib import Path
import pandas as pd
import pytest

from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.input_engine.snapshot import reported_snapshot
from portfolio_analytics.core.portfolio_state import build_portfolio_state
from portfolio_analytics.analytics.engine import run_analytics

SAMPLE = Path(__file__).resolve().parents[1] / 'examples/demo_portfolio_holdings.csv'


def fixture():
    return parse_upload(SAMPLE.read_bytes(), SAMPLE.name)


def test_58_row_statement_reconciles_without_provider_quotes_or_history():
    parsed, fx = reported_snapshot(fixture(), '2026-10-09')
    state = build_portfolio_state(parsed, fx_history=fx, base_currency='GBP')
    assert state['totals']['equity'] == pytest.approx(439514.35)
    assert state['totals']['unrealised_pnl'] == pytest.approx(sum(r['Unrealised P/L (GBP)'] for r in parsed['user_dataset']['records'] if r['Asset Class'] != 'Cash'))
    assert state['totals']['realised_pnl'] is None
    assert not state['accounting_history']['available']
    analytics = run_analytics(state, pd.DataFrame(), fx_history=fx)
    assert not analytics['meta']['coverage']['available']
    assert analytics['deep_findings']['concentration']['available']


@pytest.mark.parametrize('column,value', [('FX to GBP', 0), ('Market Value (GBP)', 0), ('Cost Basis (GBP)', 1), ('Weight', .99)])
def test_invalid_source_evidence_rejected(column, value):
    parsed = fixture()
    parsed['user_dataset']['records'][0][column] = value
    with pytest.raises(ValueError):
        reported_snapshot(parsed, '2026-10-09')


def test_no_invented_valuation_date():
    with pytest.raises((ValueError, TypeError)):
        reported_snapshot(fixture(), None)
