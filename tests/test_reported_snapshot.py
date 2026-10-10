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


def test_historical_reconciled_statement_is_diagnosed_at_declared_date():
    from portfolio_analytics.diagnostics.trust import diagnose_trust
    from portfolio_analytics.core.portfolio_state import build_portfolio_state
    from portfolio_analytics.input_engine.parser import parse_upload
    from pathlib import Path
    raw=Path('examples/demo_portfolio_holdings.csv').read_bytes()
    import pandas as pd
    from io import BytesIO
    historical=pd.read_csv(BytesIO(raw))
    historical['Purchase Date']='01/01/2020'
    parsed,fx=reported_snapshot(parse_upload(historical.to_csv(index=False).encode(),'sample.csv'),'2023-10-26')
    state=build_portfolio_state(parsed,fx_history=fx,base_currency='GBP')
    report=diagnose_trust(state,parsed=parsed,fx_history=fx)
    assert report['as_of']=='2023-10-26'
    assert report['valuation_status']!='blocked'
    assert not any(i['code']=='fx_stale' for i in report['issues'])
    assert any(i['code']=='historical_snapshot' for i in report['issues'])
    # Ordinary current valuation still rejects historical FX evidence.
    parsed['source'].pop('snapshot_validation')
    current=diagnose_trust(state,parsed=parsed,fx_history=fx)
    assert current['valuation_status']=='blocked'


def test_statement_date_must_not_precede_supplied_purchases():
    from pathlib import Path
    from portfolio_analytics.input_engine.parser import parse_upload
    import pytest
    raw=Path('examples/demo_portfolio_holdings.csv').read_bytes()
    with pytest.raises(ValueError,match='precedes its supplied purchase date'):
        reported_snapshot(parse_upload(raw,'sample.csv'),'2023-10-26')
