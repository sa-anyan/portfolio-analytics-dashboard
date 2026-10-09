"""Daniel's complete upload → underlying evidence → Copilot → navigation story."""
from copy import deepcopy
from decimal import Decimal

from tests.test_app_constituents import setup, stage, select, accept, exposure
from tests.test_discover import button, workspace
from tests.test_constituents import EXAMPLES
from portfolio_analytics.ai.copilot import build_copilot_context, _explanation_result
from portfolio_analytics.ui.constituents import security_identity


def eligible(app,parent):
    select(app,'Fund eligibility to review',parent)
    next(c for c in app.checkbox if c.label=='Confirm ordinary equity fund and whole-fund capital weights').set_value(True)
    next(t for t in app.text_input if t.label=='Eligibility evidence / reason').set_value('Synthetic ordinary physical long-only equity fixture; whole-fund capital weights').run()
    button(app,'Save fund eligibility').click().run()
    assert not app.exception


def test_daniel_passed_complete_known_story_and_navigation_without_market_requests(monkeypatch):
    app,calls,files=setup(monkeypatch)
    original=deepcopy(app.session_state['portfolio_state']); fetched=calls.copy()
    for parent in ('VOO','VGT'):
        stage(app,files,parent)
        select(app,'Dataset completeness declaration','complete')
        accept(app); eligible(app,parent)
    result=app.session_state['analytics']['lookthrough']
    assert result['complete_ranking'] and Decimal(result['reconciliation']['residual'])==0
    nvda=next(s for s in result['securities'] if s['identifiers']['ticker']==['NVDA'])
    assert Decimal(nvda['known_total'])==Decimal('1722.5')
    table=next(d.value for d in app.dataframe if 'Known total' in d.value.columns)
    assert list(table['Known total'])==[2650,1927.5,1722.5]
    select(app,'Investigate underlying security',security_identity(nvda))
    assert any('1,722.50 USD' in x.value for x in app.markdown)
    contributions=next(d.value for d in app.dataframe if 'Parent fund' in d.value.columns)
    assert dict(zip(contributions['Parent fund'],contributions['Indirect exposure']))=={'VGT':560,'VOO':162.5}
    assert any('Supporting calculations and identity evidence'==e.label for e in app.expander)
    button(app,'Prepare exposure question for Copilot').click().run()
    assert 'Nvidia' in app.session_state['copilot_question'] and app.session_state['copilot_uses']==0
    ctx=build_copilot_context(app.session_state['parsed'],original,app.session_state['analytics'])
    explained=_explanation_result({'fields':['lookthrough']},ctx)['analytics']['lookthrough']
    assert explained==app.session_state['analytics']['lookthrough']
    next(r for r in app.radio if r.label=='Deep Analytics view').set_value('Discover').run()
    assert any('Trust' in e.label or 'Data' in e.label for e in app.expander)
    button(app,'Run hypothetical shock').click().run()
    workspace(app,'Portfolio dashboard'); exposure(app)
    assert not app.exception and calls==fetched
    assert app.session_state['portfolio_state']==original and len(original['positions'])==3
    assert app.session_state['analytics']['lookthrough']['complete_ranking']


def test_partial_unknown_ui_replacement_and_revoke_eligibility(monkeypatch):
    app,calls,files=setup(monkeypatch); fetched=calls.copy()
    stage(app,files,'VOO'); select(app,'Dataset completeness declaration','complete'); accept(app); eligible(app,'VOO')
    stage(app,files,'VOO',(EXAMPLES/'synthetic_voo_top_ten.csv').read_bytes(),'partial.csv')
    select(app,'Dataset completeness declaration','partial'); accept(app)
    r=app.session_state['analytics']['lookthrough']
    voo=next(f for f in r['funds'] if f['parent']=='VOO')
    assert Decimal(voo['unknown_exposure'])==1175 and not r['complete_ranking']
    assert any('not a complete economic concentration ranking' in w.value for w in app.warning)
    assert len(app.session_state['fund_constituents']['VOO']['history'])==1
    button(app,'Revoke fund eligibility').click().run()
    assert app.session_state['analytics']['lookthrough']['funds'][0]['status']=='unavailable'
    assert calls==fetched and not app.exception


def test_selection_identity_survives_audit_rank_change_replacement_and_navigation(monkeypatch):
    app,calls,files=setup(monkeypatch)
    baseline=deepcopy(app.session_state['portfolio_state']); fetched=calls.copy()
    assert app.session_state['portfolio_state']['totals']['realised_pnl'] is None
    # Exact browser audit order: NVDA starts first, MSFT becomes first after VOO acceptance.
    for parent in ('VGT','VOO'):
        stage(app,files,parent); select(app,'Dataset completeness declaration','complete')
        accept(app); eligible(app,parent)
    assert any('Known Nvidia exposure: 1,722.50 USD' in m.value for m in app.markdown)
    securities=app.session_state['analytics']['lookthrough']['securities']
    assert securities[0]['identifiers']['ticker']==['MSFT']
    identities={s['identifiers']['ticker'][0]:security_identity(s) for s in securities}
    for ticker,expected in [('MSFT','Microsoft'),('AAPL','Apple'),('NVDA','Nvidia'),
                            ('AAPL','Apple'),('MSFT','Microsoft'),('NVDA','Nvidia')]:
        select(app,'Investigate underlying security',identities[ticker])
        assert any('Known '+expected+' exposure:' in m.value for m in app.markdown)
        app.run()
        assert app.session_state['exposure_selected_identity']==identities[ticker]
        button(app,'Prepare exposure question for Copilot').click().run()
        assert expected in app.session_state['copilot_question']
    workspace(app,'Portfolio dashboard'); exposure(app)
    assert next(s for s in app.selectbox if s.label=='Investigate underlying security').value==identities['NVDA']
    stage(app,files,'VOO',(EXAMPLES/'synthetic_voo_top_ten.csv').read_bytes(),'partial.csv')
    select(app,'Dataset completeness declaration','partial'); accept(app)
    assert any('Known Nvidia exposure: 1,935.00 USD' in m.value for m in app.markdown)
    # Legacy numeric / removed identity cannot index a different security after a rerun.
    workspace(app,'Portfolio dashboard')
    app.session_state['exposure_selected_identity']='removed-security'
    app.session_state['exposure_security']=0
    exposure(app)
    widget=next(s for s in app.selectbox if s.label=='Investigate underlying security')
    result=app.session_state['analytics']['lookthrough']
    selected=next(s for s in result['securities'] if security_identity(s)==widget.value)
    assert any('Known '+selected['name']+' exposure:' in m.value for m in app.markdown)
    select(app,'Investigate underlying security',identities['AAPL'])
    for parent in ('VOO','VGT'):
        select(app,'Fund eligibility to review',parent)
        button(app,'Revoke fund eligibility').click().run()
    widget=next(s for s in app.selectbox if s.label=='Investigate underlying security')
    assert widget.value==identities['NVDA']
    assert any('Known Nvidia exposure: 1,000.00 USD' in m.value for m in app.markdown)
    assert app.session_state['portfolio_state']==baseline and calls==fetched and not app.exception


def test_identity_is_typed_order_independent_and_unaffected_by_ranking_values():
    a={'identifiers':{'ticker':['NVDA'],'isin':['US67066G1040']},'name':'Nvidia','known_total':'1000'}
    b={'identifiers':{'isin':['US67066G1040'],'ticker':['NVDA']},'name':'Renamed','known_total':'1722.5'}
    assert security_identity(a)==security_identity(b)
    assert security_identity(a)!=security_identity({'identifiers':{'security_id':['NVDA']}})
