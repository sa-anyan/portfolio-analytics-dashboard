"""Daniel's complete upload → underlying evidence → Copilot → navigation story."""
from copy import deepcopy
from decimal import Decimal

from tests.test_app_constituents import setup, stage, select, accept, exposure
from tests.test_discover import button, workspace
from tests.test_constituents import EXAMPLES
from portfolio_analytics.ai.copilot import build_copilot_context, _explanation_result


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
    index=next(i for i,s in enumerate(result['securities']) if s['identifiers']['ticker']==['NVDA'])
    select(app,'Investigate underlying security',index)
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
