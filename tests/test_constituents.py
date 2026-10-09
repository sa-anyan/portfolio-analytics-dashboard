"""Deterministic input metadata, exact decimal arithmetic, and source isolation."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from portfolio_analytics.input_engine.constituents import (
    identity_catalog, stage_constituents, validate_constituents, accept_constituents, saved_coverage,
)
from portfolio_analytics.input_engine.parser import parse_upload
from portfolio_analytics.core.portfolio_state import build_portfolio_state

EXAMPLES = Path(__file__).resolve().parents[1]/'examples'/'constituents'
DATE = '2026-10-09'


def daniel():
    parsed = parse_upload((EXAMPLES/'synthetic_daniel_portfolio.csv').read_bytes(), 'portfolio.csv')
    state = build_portfolio_state(parsed)
    return state, parsed, identity_catalog(state, parsed=parsed)


def draft(name='synthetic_voo.csv', parent='VOO'):
    return stage_constituents([(name, (EXAMPLES/name).read_bytes())], selected_parent=parent)


def check(d, catalog=None, decisions=None):
    return validate_constituents(d, catalog or daniel()[2], decisions={'completeness':'complete'} if decisions is None else decisions, as_of=DATE)


def csv(rows):
    return pd.DataFrame(rows).to_csv(index=False).encode()


def changed(field, value, *, row=0):
    data=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str,keep_default_na=False)
    data.loc[row,field]=value
    return stage_constituents([('edited.csv',data.to_csv(index=False).encode())],selected_parent='VOO')


def test_daniel_fund_dates_weights_direct_mapping_and_canonical_state_isolation():
    state, parsed, catalog = daniel()
    before = deepcopy(state)
    store={}
    for parent in ('VOO','VGT'):
        d=draft(f'synthetic_{parent.lower()}.csv',parent)
        original=deepcopy(d)
        checked=check(d,catalog)
        assert checked['ready'] and checked['coverage']['completeness']=='reported_complete'
        assert Decimal(checked['coverage']['reported_weight'])==Decimal('1')
        nvda=next(r for r in checked['records'] if r['identifiers']['ticker']==['NVDA'])
        assert nvda['matched_direct_security']=='NVDA'
        assert Decimal(nvda['weight'])==Decimal('0.065' if parent=='VOO' else '0.20')
        assert nvda['as_of']==DATE and nvda['reported_parent_identifiers']['ticker']==[parent]
        assert checked['coverage']['unreported_weight']=='0'
        store=accept_constituents(store,d,catalog,decisions={'completeness':'complete'},as_of=DATE)
        assert d==original
    assert state==before and len(state['positions'])==3 and state['totals']['equity']==6300
    assert set(store)=={'VOO','VGT'} and len(store['VOO']['records'])==3 and len(store['VGT']['records'])==3
    assert store['VOO']['records'][0]['source_fingerprint']
    assert 'look-through is not calculated' in store['VOO']['scope']


def test_top_ten_is_partial_and_is_never_scaled_to_100():
    c=check(draft('synthetic_voo_top_ten.csv'))
    assert c['ready'] and c['coverage']['completeness']=='partial'
    assert c['coverage']['constituent_count']==10
    assert c['coverage']['reported_weight']=='0.530'
    assert c['coverage']['unreported_weight']=='0.470'
    assert any(i['code']=='completeness_conflict' for i in c['issues'])
    assert Decimal(c['records'][0]['weight'])==Decimal('0.15')
    c=check(draft(),decisions={})
    assert c['ready'] and c['coverage']['completeness']=='unverified'


@pytest.mark.parametrize('value',['','garbage','-1','101','NaN','Infinity'])
def test_invalid_weight_replacement_never_replaces_accepted_metadata(value):
    catalog=daniel()[2]
    store=accept_constituents({},draft(),catalog,decisions={'completeness':'complete'},as_of=DATE)
    before=deepcopy(store)
    d=changed('Weight',value)
    c=check(d)
    assert not c['ready'] and any(i['code']=='weight_invalid' for i in c['issues'])
    assert c['coverage']['known_weight_coverage'] is None and c['coverage']['unreported_weight'] is None
    with pytest.raises(ValueError):
        accept_constituents(store,d,catalog,as_of=DATE)
    assert store==before


def test_weight_formats_zero_decimal_percent_suffix_and_total_overweight():
    d=changed('Weight Format','decimal')
    assert not check(d)['ready']
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame['Weight']=['0.065','0.435','0.500']
    frame['Weight Format']='decimal'
    c=check(stage_constituents([('decimal.csv',frame.to_csv(index=False).encode())],selected_parent='VOO'))
    assert c['ready'] and c['coverage']['reported_weight']=='1.000'
    frame['Weight']=['6.5%','43.5%','50%']
    frame=frame.drop(columns='Weight Format')
    c=check(stage_constituents([('suffix.csv',frame.to_csv(index=False).encode())],selected_parent='VOO'))
    assert c['ready'] and c['coverage']['reported_weight']=='1.000'
    frame['Weight']=['6.5','43.5','50']
    c=check(stage_constituents([('bare.csv',frame.to_csv(index=False).encode())],selected_parent='VOO'))
    assert not c['ready']
    c=check(stage_constituents([('bare.csv',frame.to_csv(index=False).encode())],selected_parent='VOO'),decisions={'weight_format':'percentage'})
    assert c['ready']
    assert any(i['code']=='weights_exceed_100' for i in check(changed('Weight','50'))['issues'])
    assert check(changed('Weight','0'))['ready']


def test_duplicate_rows_require_explicit_exclusion_with_reason():
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv')
    d=stage_constituents([('duplicate.csv',pd.concat([frame,frame.iloc[:1]]).to_csv(index=False).encode())],selected_parent='VOO')
    c=check(d)
    assert not c['ready'] and any(i['code']=='duplicate_record' for i in c['issues'])
    rid=d['records'][-1]['record_id']
    decisions={'completeness':'complete','excluded_records':{rid:'Same NVDA holding repeated in export.'}}
    c=check(d,decisions=decisions)
    assert c['ready'] and len(c['records'])==3 and c['coverage']['reported_weight']=='1.000'
    assert len(d['records'])==4 and d['records'][-1]['raw']
    assert not check(d,decisions={'excluded_records':{rid:''}})['ready']


def test_alias_and_conflicting_identifiers_require_evidence_not_names():
    d=changed('Constituent Ticker','NVDA.O')
    c=check(d)
    assert not c['ready'] and any(i['code']=='identity_conflict' for i in c['issues'])
    rid=d['records'][0]['record_id']
    decisions={'identity_mappings':{rid:{'target':'NVDA','reason':'Same US67066G1040 ISIN; provider uses a listing suffix.'}}}
    c=check(d,decisions=decisions)
    assert c['ready'] and c['records'][0]['identifiers']['ticker']==['NVDA.O']
    assert c['records'][0]['resolved_identifiers']['ticker']==['NVDA']
    assert c['records'][0]['identity_status']=='explicit reviewed mapping'
    d=changed('Constituent ISIN','US92204A7028')
    assert any(i['code']=='identity_ambiguous' for i in check(d)['issues'])
    d=changed('Constituent Ticker','FAKE')
    d=deepcopy(d); d['records'][0]['fields']['isin']=''
    c=check(d)
    assert c['ready'] and c['records'][0]['matched_direct_security'] is None  # Same Nvidia name never creates a match.


@pytest.mark.parametrize('value',['','garbage','01/02/2026','2026-02-30','2026-10-10'])
def test_missing_invalid_ambiguous_or_future_dates_block(value):
    d=changed('As Of Date',value)
    c=check(d)
    assert not c['ready']


def test_multiple_dates_sources_and_renamed_file_copies_require_snapshot_selection():
    d=changed('As Of Date','2026-09-30')
    c=check(d)
    assert not c['ready'] and any(i['code']=='snapshot_conflict' for i in c['issues'])
    sid=next(s for s in c['snapshots'] if s.endswith('@2026-10-09'))
    c=check(d,decisions={'snapshot_id':sid,'snapshot_reason':'Use the October snapshot only.'})
    assert c['ready'] and len(c['records'])==2 and c['coverage']['reported_weight']=='0.935'
    data=(EXAMPLES/'synthetic_voo.csv').read_bytes()
    d=stage_constituents([('a.csv',data),('renamed.csv',data)],selected_parent='VOO')
    assert d['files'][0]['fingerprint']==d['files'][1]['fingerprint']
    c=check(d)
    assert not c['ready']
    c=check(d,decisions={'snapshot_id':c['snapshots'][0],'snapshot_reason':'Retain one copy; repeated bytes.'})
    assert c['ready'] and len(c['records'])==3


def test_parent_assignment_identifiers_missing_ids_unpriced_parent_and_classification():
    d=changed('Parent Fund','VGT')
    assert not check(d)['ready'] and any(i['code']=='parent_mismatch' for i in check(d)['issues'])
    d=changed('Parent Fund','')
    assert any(i['code']=='parent_ambiguous' for i in check(d)['issues'])
    c=check(d,decisions={'parent_assignment':{'target':'VOO','reason':'Broker export omits parent column; explicitly linked to selected fund.'}})
    assert c['ready']
    d=changed('Constituent Ticker','')
    d['records'][0]['fields']['isin']=''
    assert not check(d)['ready']
    c=check(d,decisions={'identity_mappings':{d['records'][0]['record_id']:{'target':'NVDA','reason':'Confirmed identifier against source.'}}})
    assert c['ready']
    state,parsed,catalog=daniel()
    catalog['VOO']['asset_class']=''
    assert not check(draft(),catalog)['ready']
    assert check(draft(),catalog,{'fund_confirmed':True,'fund_reason':'User identified the held VOO fund.'})['ready']


def test_source_missing_stale_cash_derivative_foreign_and_replacement_history():
    d=changed('Source','')
    assert check(d)['ready'] and any(i['code']=='source_unknown' for i in check(d)['issues'])
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame['As Of Date']='2026-01-01'
    frame['Constituent Ticker']=['CASH:EUR','OPTION:NVDA:202612','BOND:0007']
    frame['Constituent ISIN']=''
    frame['Asset Class']=['Cash','Derivative','Bond']
    frame['Currency']=['EUR','USD','GBP']
    d=stage_constituents([('non-equity.csv',frame.to_csv(index=False).encode())],selected_parent='VOO')
    c=check(d)
    assert c['ready'] and any(i['code']=='snapshot_stale' for i in c['issues'])
    assert {r['currency'] for r in c['records']}=={'EUR','GBP','USD'}
    assert len([i for i in c['issues'] if i['code']=='non_equity'])==3
    store=accept_constituents({},draft(),daniel()[2],as_of=DATE)
    new=accept_constituents(store,d,daniel()[2],as_of=DATE)
    assert len(new['VOO']['history'])==1 and store['VOO']['records'][0]['identifiers']['ticker']==['NVDA']
    assert saved_coverage(store['VOO'],daniel()[2],as_of='2027-01-10')['stale']
    catalog=daniel()[2]; catalog.pop('VOO')
    assert not saved_coverage(store['VOO'],catalog,as_of=DATE)['parent_current']


def test_xlsx_identity_strings_and_malformed_empty_or_ambiguous_columns():
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame.loc[1,'Constituent Ticker']=''
    frame.loc[1,'Constituent ISIN']=''
    frame['Security ID']=['00123','0007','00999']
    buff=BytesIO()
    frame.to_excel(buff,index=False)
    d=stage_constituents([('ids.xlsx',buff.getvalue())],selected_parent='VOO')
    c=check(d)
    assert c['ready'] and c['records'][1]['identifiers']['security_id']==['0007']
    for data in (b'',b'Weight,As Of Date\n',b'Weight,Weight,As Of Date\n10,20,2026-10-09\n',b'Name,Amount\nFoo,2\n'):
        assert not check(stage_constituents([('bad.csv',data)],selected_parent='VOO'))['ready']


def test_parent_isin_mapping_incomplete_identity_and_partial_100_are_unknown():
    data=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    data=data.drop(columns='Parent Fund')
    data['Parent ISIN']='US9229083632'
    d=stage_constituents([('isin-only.csv',data.to_csv(index=False).encode())],selected_parent='VOO')
    c=check(d)
    assert c['ready'] and all(r['parent_mapping']=='exact identifier' for r in c['records'])
    c=check(d,decisions={'completeness':'partial'})
    assert c['ready'] and c['coverage']['completeness']=='partial'
    assert c['coverage']['known_weight_coverage'] is None and c['coverage']['unreported_weight'] is None
    catalog=daniel()[2]
    saved=accept_constituents({},d,catalog,as_of=DATE)
    catalog['VOO']['identifiers']['isin']=[]
    assert not saved_coverage(saved['VOO'],catalog,as_of=DATE)['parent_current']


def test_explicit_source_exclusion_and_consolidated_catalog_only_uses_retained_partitions():
    d=stage_constituents([('bad.csv',b''),('good.csv',(EXAMPLES/'synthetic_voo.csv').read_bytes())],selected_parent='VOO')
    assert not check(d)['ready']
    c=check(d,decisions={'excluded_sources':{d['files'][0]['source_id']:'Empty file selected accidentally.'}})
    assert c['ready']
    assert len(d['files'])==2
    state,parsed,_=daniel()
    state['consolidation']={'records':[{'part_id':'retained'}]}
    batch={'files':[{'parts':[{'part_id':'excluded','parsed':{'user_dataset':{'records':[{'Ticker':'VOO','ISIN':'WRONG'}]}}},
                            {'part_id':'retained','parsed':{'user_dataset':{'records':[{'Ticker':'VOO','ISIN':'US9229083632'}]}}}]}]}
    catalog=identity_catalog(state,accepted_batch=batch)
    assert catalog['VOO']['identifiers']['isin']==['US9229083632']
    d=draft()
    assert not check(d,decisions={'identity_mappings':{'unknown':{'target':'NVDA','reason':'Invalid stale decision.'}}})['ready']


def test_typed_cusip_and_sedol_identifiers_preserve_leading_zeroes():
    frame=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    frame['Constituent Ticker']=''
    frame['Constituent ISIN']=''
    frame['CUSIP']=['0007','00123','00999']
    frame['SEDOL']=['007','001','009']
    d=stage_constituents([('typed-ids.csv',frame.to_csv(index=False).encode())],selected_parent='VOO')
    c=check(d)
    assert c['ready'] and c['records'][0]['identifiers']['cusip']==['0007']
    assert c['records'][0]['identifiers']['sedol']==['007']
    catalog=daniel()[2]
    catalog['NVDA']['identifiers']['cusip']=['0007']
    assert check(d,catalog)['records'][0]['matched_direct_security']=='NVDA'


def test_exact_high_precision_weight_checks_never_round_overweight_to_100():
    d=changed('Weight','100.00000000000000000000000000001')
    c=check(d)
    assert not c['ready'] and any(i['code']=='weight_invalid' for i in c['issues'])
    data=pd.read_csv(EXAMPLES/'synthetic_voo.csv',dtype=str)
    data['Weight']=['0.5','0.5','0.000000000000000000000000000001']
    data['Weight Format']='decimal'
    d=stage_constituents([('precision.csv',data.to_csv(index=False).encode())],selected_parent='VOO')
    c=check(d)
    assert not c['ready'] and any(i['code']=='weights_exceed_100' for i in c['issues'])
