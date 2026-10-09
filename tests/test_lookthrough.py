"""Independent numerical oracle and conservative capital-exposure safeguards."""
from copy import deepcopy
from decimal import Decimal as D

import pandas as pd
import pytest

from tests.test_constituents import daniel, draft, DATE, EXAMPLES, changed
from portfolio_analytics.input_engine.constituents import accept_constituents, stage_constituents
from portfolio_analytics.analytics.lookthrough import calculate_lookthrough, eligibility_declaration
from portfolio_analytics.ai.copilot import build_copilot_context, _explanation_result


def fixture():
    state, parsed, catalog = daniel()
    store = {}
    for t in ('VOO', 'VGT'):
        store = accept_constituents(store, draft(f'synthetic_{t.lower()}.csv', t), catalog,
            decisions={'completeness':'complete'}, as_of=DATE)
    declarations = {t: eligibility_declaration(catalog[t], 'Synthetic ordinary physical equity fund; original whole-fund capital weights') for t in store}
    return state, parsed, catalog, store, declarations


def calc(state, catalog, store, declarations, **kwargs):
    return calculate_lookthrough(state, store, catalog, declarations=declarations, as_of=DATE, **kwargs)


def nvda(result):
    return next(s for s in result['securities'] if s['identifiers'].get('ticker') == ['NVDA'])


def test_independent_oracle_overlap_concentration_and_no_accounting_mutation():
    s,p,c,store,decl=fixture(); original=deepcopy([s,store,c,decl])
    r=calc(s,c,store,decl)
    # Oracle derives all figures independently from the user's shares/prices/weights.
    direct = D(10)*D(100)
    voo = D(5)*D(500)*D('6.5')/100
    vgt = D(4)*D(700)*D(20)/100
    equity = D(5)*D(500)+D(4)*D(700)+direct
    n=nvda(r)
    assert D(n['direct'])==direct==1000
    assert D(n['indirect_by_fund']['VOO'])==voo==D('162.50')
    assert D(n['indirect_by_fund']['VGT'])==vgt==560
    assert D(n['known_total'])==direct+voo+vgt==D('1722.50')
    assert float(n['equity_fraction'])==pytest.approx(float((direct+voo+vgt)/equity))
    assert D(r['reconciliation']['known_total'])==equity==6300
    assert D(r['reconciliation']['residual'])==0 and r['complete_ranking']
    assert {'overlap','direct_indirect','concentration'} <= {f['code'] for f in r['findings'] if f.get('security')=='Nvidia'}
    assert [s,store,c,decl]==original


def test_partial_53_percent_unknown_not_rescaled_or_zero():
    s,p,c,store,decl=fixture()
    store=accept_constituents(store,draft('synthetic_voo_top_ten.csv'),c,decisions={'completeness':'partial'},as_of=DATE)
    r=calc(s,c,store,decl)
    f=next(f for f in r['funds'] if f['parent']=='VOO')
    assert D(f['known_exposure'])==D(2500)*D('.53')==1325
    assert D(f['unknown_exposure'])==D(2500)*D('.47')==1175
    assert D(nvda(r)['indirect_by_fund']['VOO'])==375
    assert D(r['reconciliation']['residual'])==0
    assert not r['complete_ranking'] and r['known_is_lower_bound']


def test_unverified_100_percent_is_not_verified_by_zero_arithmetic_residual():
    s,p,c,store,decl=fixture()
    store=accept_constituents(store,draft(),c,decisions={},as_of=DATE)
    r=calc(s,c,store,decl); f=r['funds'][0]
    assert f['status']=='unverified' and f['unknown_exposure'] is None
    assert not r['complete_ranking'] and D(r['reconciliation']['residual'])==0


def test_no_data_eligibility_is_explicit_and_reversible():
    s,p,c,store,decl=fixture()
    r=calc(s,c,{},decl)
    assert D(r['reconciliation']['unknown_residual'])==5300
    assert D(r['reconciliation']['known_total'])==1000
    assert D(r['reconciliation']['residual'])==0
    r=calc(s,c,store,{})
    assert all(f['status']=='unavailable' for f in r['funds'])
    assert {i['code'] for i in r['issues']}=={'eligibility_unverified'}


@pytest.mark.parametrize('kind', ['leveraged','inverse','derivative','short','cash','nested','missing_type'])
def test_unsupported_instruments_never_get_fabricated_exposure(kind):
    s,p,c,store,decl=fixture()
    if kind in ('leveraged','inverse','derivative'):
        s['positions'][0]['asset_name']=kind+' ETF'
    elif kind=='short':
        s['positions'][0].update(side='SHORT',quantity=-5,signed_market_value=-2500)
    else:
        asset={'cash':'Cash','nested':'ETF','missing_type':''}[kind]
        store=accept_constituents(store,changed('Asset Class',asset),c,decisions={'completeness':'complete'},as_of=DATE)
    r=calc(s,c,store,decl)
    assert not r['complete_ranking'] and not r['known_is_lower_bound']
    assert 'VOO' not in nvda(r)['indirect_by_fund']
    assert r['funds'][0]['status'] in ('unavailable','unsupported or unresolved composition')
    assert D(r['reconciliation']['residual'])==0


@pytest.mark.parametrize('kind', ['blocked_trust','unpriced','nan_equity'])
def test_invalid_valuation_suppresses_all_amounts_and_percentages(kind):
    s,p,c,store,decl=fixture(); kwargs={}
    if kind=='blocked_trust': kwargs['trust']={'valuation_status':'blocked'}
    if kind=='unpriced': s['positions'][0]['signed_market_value']=None
    if kind=='nan_equity': s['totals']['equity']=float('nan')
    r=calc(s,c,store,decl,**kwargs)
    assert not r['valuation_valid'] and not r['securities']
    assert r['reconciliation']['eligible_total'] is None
    assert all(f['market_value'] is None for f in r['funds'])


def test_stale_different_dates_and_missing_source_are_disclosed():
    s,p,c,store,decl=fixture()
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame['As Of Date']='2026-01-01'
    stale=stage_constituents([('stale.csv',frame.to_csv(index=False).encode())],selected_parent='VOO')
    store=accept_constituents(store,stale,c,decisions={'completeness':'complete'},as_of=DATE)
    r=calc(s,c,store,decl)
    assert {'stale','different_dates'} <= {i['code'] for i in r['issues']}
    assert not r['complete_ranking'] and not r['known_is_lower_bound']
    assert D(nvda(r)['known_total'])==D('1722.5') # explicitly historical estimates
    assert 'historical-composition' in next(i['explanation'] for i in r['issues'] if i['code']=='stale')


def test_foreign_parent_uses_only_canonical_reporting_value_and_changed_quantity():
    s,p,c,store,decl=fixture()
    s['positions'][0].update(currency='EUR',price_currency='EUR',local_current_price=400,quantity=10,signed_market_value=6000)
    s['totals']['equity']=9800
    s['meta']['base_currency']='GBP'
    r=calc(s,c,store,decl)
    assert D(nvda(r)['indirect_by_fund']['VOO'])==D(6000)*D('.065')==390
    assert r['reporting_currency']=='GBP' and D(r['reconciliation']['residual'])==0
    assert D(nvda(r)['known_total'])==1950


def test_changed_parent_identity_or_alias_target_invalidates_metadata():
    s,p,c,store,decl=fixture()
    c['VOO']['identifiers']['isin']=['DIFFERENT']
    r=calc(s,c,store,decl)
    assert 'eligibility_unverified' in {i['code'] for i in r['issues']}
    decl['VOO']=eligibility_declaration(c['VOO'],'Reviewed new identity')
    r=calc(s,c,store,decl)
    assert r['funds'][0]['status']=='unsupported or unresolved composition'
    assert 'VOO' not in nvda(r)['indirect_by_fund']


def test_identical_names_distinct_ids_do_not_merge_and_conflicting_ids_are_unresolved():
    s,p,c,store,decl=fixture()
    frame=pd.read_csv(EXAMPLES/'synthetic_vgt.csv',dtype=str)
    frame.loc[1,'Constituent Name']='Microsoft'
    d=stage_constituents([('names.csv',frame.to_csv(index=False).encode())],selected_parent='VGT')
    store=accept_constituents(store,d,c,decisions={'completeness':'complete'},as_of=DATE)
    r=calc(s,c,store,decl)
    assert len(r['securities'])==3 # names ignored; correct typed ID still joins AAPL
    frame.loc[1,'Constituent ISIN']='OTHER_SHARE_CLASS'
    store=accept_constituents(store,stage_constituents([('conflict.csv',frame.to_csv(index=False).encode())],selected_parent='VGT'),c,decisions={'completeness':'complete'},as_of=DATE)
    r=calc(s,c,store,decl)
    assert 'identity_conflict' in {i['code'] for i in r['issues']}
    assert not any(x['identifiers']['ticker']==['AAPL'] for x in r['securities'])
    assert D(r['reconciliation']['unresolved_identity_amount'])==D('1927.5')
    assert D(r['reconciliation']['residual'])==0


def test_unresolved_mapping_and_invalid_replacement_preserve_last_valid_metadata():
    s,p,c,store,decl=fixture(); before=deepcopy(store)
    for d in (changed('Weight','garbage'), changed('Constituent Ticker','',row=1)):
        if d['records'][1]['fields'].get('ticker')=='':
            d['records'][1]['fields']['isin']=''
        with pytest.raises(ValueError):
            accept_constituents(store,d,c,decisions={'completeness':'complete'},as_of=DATE)
        assert store==before
        assert D(nvda(calc(s,c,store,decl))['known_total'])==D('1722.5')


def test_empty_portfolio_and_zero_equity_do_not_invent_percentage():
    r=calc({'positions':[],'totals':{'equity':0}}, {},{}, {})
    assert not r['securities'] and D(r['reconciliation']['residual'])==0
    s,p,c,store,decl=fixture(); s['totals']['equity']=0
    r=calc(s,c,store,decl)
    assert all(x['equity_fraction'] is None for x in r['securities'])


def test_copilot_explanation_has_only_deterministic_structured_exposure():
    s,p,c,store,decl=fixture(); r=calc(s,c,store,decl)
    context=build_copilot_context(p,s,{'lookthrough':r})
    explanation=_explanation_result({},context)
    assert D(nvda(explanation['analytics']['lookthrough'])['known_total'])==D('1722.5')
    assert all('raw' not in e for x in explanation['analytics']['lookthrough']['securities'] for e in x['evidence'])
    scenario=dict(context,scenario={'scenario_state':s,'scenario_analytics':{}})
    assert _explanation_result({'scope':'latest_scenario'},scenario)['analytics']['lookthrough']=={}


def test_same_names_with_different_verified_ids_remain_separate_share_classes():
    s,p,c,store,decl=fixture()
    frame=pd.read_csv(EXAMPLES/'synthetic_vgt.csv',dtype=str)
    frame.loc[1,'Constituent Name']='Apple'
    frame.loc[1,'Constituent Ticker']='AAPL_CLASS_B'
    frame.loc[1,'Constituent ISIN']='SEPARATE_SHARE_CLASS'
    store=accept_constituents(store,stage_constituents([('class.csv',frame.to_csv(index=False).encode())],selected_parent='VGT'),c,decisions={'completeness':'complete'},as_of=DATE)
    r=calc(s,c,store,decl)
    apples=[x for x in r['securities'] if x['name']=='Apple']
    assert len(apples)==2 and {D(a['known_total']) for a in apples}=={D('1087.5'),D(840)}
    assert D(r['reconciliation']['residual'])==0


def test_explicit_alias_matches_direct_and_mapping_change_is_unavailable():
    s,p,c,store,decl=fixture()
    d=changed('Constituent Ticker','NVDA.O')
    rid=d['records'][0]['record_id']
    decisions={'completeness':'complete','identity_mappings':{rid:{'target':'NVDA','reason':'Verified same ISIN; listing alias'}}}
    store=accept_constituents(store,d,c,decisions=decisions,as_of=DATE)
    r=calc(s,c,store,decl)
    assert D(nvda(r)['known_total'])==D('1722.5')
    assert any(e['identity_status']=='explicit reviewed mapping' for e in nvda(r)['evidence'])
    c.pop('NVDA')
    r=calc(s,c,store,decl)
    assert r['funds'][0]['status']=='unsupported or unresolved composition'
    assert not r['known_is_lower_bound']


def test_router_allows_known_indirect_only_security_but_never_trading_it():
    from portfolio_analytics.ai.router import validate_route
    s,p,c,store,decl=fixture(); r=calc(s,c,store,decl)
    ctx=build_copilot_context(p,s,{'lookthrough':r})
    route=validate_route({'intent':'explain','fields':['lookthrough'],'ticker':'AAPL'},ctx)
    assert route['ticker']=='AAPL' and route['scope']=='accepted'
    with pytest.raises(ValueError):
        validate_route({'intent':'lookup','fields':['quantity'],'ticker':'AAPL'},ctx)
    with pytest.raises(ValueError):
        validate_route({'intent':'explain','fields':['lookthrough'],'ticker':'UNKNOWN'},ctx)
