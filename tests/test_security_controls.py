"""No real paid calls: boundary, durable multiprocess and upload abuse checks."""
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest
from openpyxl import Workbook

from portfolio_analytics.security import ai_access as access
from portfolio_analytics.security.uploads import MAX_FILE_BYTES, MAX_ROWS, validate_batch, spreadsheet_safe
from portfolio_analytics.security.session import synchronise_owner
from portfolio_analytics.input_engine.parser import read_portfolio_file, parse_dataframe
from portfolio_analytics.input_engine.batch import ingest_files, append_files
from portfolio_analytics.ai import copilot
from tests.test_lookthrough import fixture, calc


class User(dict):
    is_logged_in=True


def configure(monkeypatch, tmp_path):
    # OIDC is simulated. Ledger tests exercise real storage, not actual token verification.
    monkeypatch.setattr(access, 'verify_auth_stack', lambda: None)
    path=tmp_path/'budget'/'requests.sqlite'; access.initialise_budget(path)
    for key,val in {'AI_ACCESS_POLICY':'controlled_oidc','AI_DEPLOYMENT_ARCHITECTURE':'single_host',
        'AI_BUDGET_DB_PATH':str(path),'AI_OIDC_ISSUER':'https://issuer.example',
        'AI_ALLOWED_SUBJECTS':'alice,bob','AI_ALLOWED_MODEL':copilot.DEFAULT_MODEL}.items():
        monkeypatch.setenv(key,val)
    return path, User(iss='https://issuer.example',sub='alice',exp=9999999999)


def paid(**extra):
    return copilot._provider_response('fake-server-key',model=copilot.DEFAULT_MODEL,
        instructions='test',input='test',max_output_tokens=10,**extra)


def test_anonymous_and_policy_reset_never_reach_provider(monkeypatch,tmp_path):
    monkeypatch.setattr(copilot,'_client',lambda *a: pytest.fail('No provider authorised'))
    monkeypatch.delenv('AI_ACCESS_POLICY',raising=False)
    for _ in range(15):
        with pytest.raises(access.AIUnavailable): paid()
    path,user=configure(monkeypatch,tmp_path)
    for invalid in (SimpleNamespace(is_logged_in=False),User(iss='https://attacker',sub='alice',exp=9999999999),
                    User(iss='https://issuer.example',sub='eve',exp=9999999999),
                    User(iss='https://issuer.example',sub='alice',exp=1)):
        with pytest.raises(access.AIUnavailable):
            with access.authorised_request(invalid,True): paid()
    with pytest.raises(access.AIUnavailable):
        with access.authorised_request(user,False): paid()
    with pytest.raises(access.AIUnavailable): paid()
    monkeypatch.setenv('AI_DEPLOYMENT_ARCHITECTURE','multi_host')
    with pytest.raises(access.AIUnavailable):
        with access.authorised_request(user,True): paid()


def test_daily_quota_failure_budget_restart_and_user_isolation(monkeypatch,tmp_path):
    path,user=configure(monkeypatch,tmp_path);monkeypatch.setenv('AI_USER_DAILY_REQUESTS','2')
    for _ in range(2):
        with access.authorised_request(user,True), access.reserve(100,10): pass
    # A new authorisation context (another session/process/restart) cannot reset the ledger.
    with pytest.raises(access.AIUnavailable,match='daily'):
        with access.authorised_request(user,True), access.reserve(100,10): pass
    with access.authorised_request(User(iss=user['iss'],sub='bob',exp=user['exp']),True), access.reserve(100,10): pass
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*),SUM(active) FROM reservations').fetchone()==(3,0)


@pytest.mark.parametrize('setting,value', [('AI_GLOBAL_MONTHLY_REQUESTS','1'),('AI_GLOBAL_MONTHLY_TOKENS','600'),('AI_USER_DAILY_TOKENS','600')])
def test_shared_request_and_token_exhaustion(monkeypatch,tmp_path,setting,value):
    path,user=configure(monkeypatch,tmp_path);monkeypatch.setenv(setting,value)
    with access.authorised_request(user,True), access.reserve(50,10): pass
    with pytest.raises(access.AIUnavailable,match='exhausted'):
        with access.authorised_request(user,True), access.reserve(50,10): pass


def test_unknown_budget_permissions_corruption_context_output_fail_closed(monkeypatch,tmp_path):
    path,user=configure(monkeypatch,tmp_path)
    for size,out in [(32001,10),(1,1001)]:
        with pytest.raises(access.AIUnavailable):
            with access.authorised_request(user,True), access.reserve(size,out): pass
    path.chmod(0o644)
    with pytest.raises(access.AIUnavailable):
        with access.authorised_request(user,True), access.reserve(1,10): pass
    path.chmod(0o600); path.write_bytes(b'not a database')
    with pytest.raises(access.AIUnavailable):
        with access.authorised_request(user,True), access.reserve(1,10): pass
    path.unlink()
    with pytest.raises(access.AIUnavailable):
        with access.authorised_request(user,True), access.reserve(1,10): pass
    assert not path.exists()


def _worker(env, queue):
    os.environ.update(env)
    access.verify_auth_stack = lambda: None  # No live identity provider is configured in tests.
    user=User(iss='https://issuer.example',sub='alice',exp=9999999999)
    try:
        with access.authorised_request(user,True), access.reserve(1,10): pass
        queue.put(True)
    except access.AIUnavailable:
        queue.put(False)


def test_actual_separate_processes_share_atomic_budget(monkeypatch,tmp_path):
    path,user=configure(monkeypatch,tmp_path);monkeypatch.setenv('AI_GLOBAL_MONTHLY_REQUESTS','3')
    env={k:v for k,v in os.environ.items() if k.startswith('AI_')}
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
    processes=[ctx.Process(target=_worker,args=(env,queue)) for _ in range(6)]
    for p in processes:p.start()
    results=[queue.get(timeout=30) for _ in processes]
    for p in processes:p.join(timeout=10);assert p.exitcode==0
    assert sum(results)==3
    with sqlite3.connect(path) as db: assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==3


def test_concurrency_and_crashed_attempt_not_refunded(monkeypatch,tmp_path):
    path,user=configure(monkeypatch,tmp_path);monkeypatch.setenv('AI_MAX_CONCURRENT_REQUESTS','1')
    with access.authorised_request(user,True), access.reserve(1,10):
        with pytest.raises(access.AIUnavailable,match='busy'):
            with access.reserve(1,10): pass
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO reservations VALUES ('crashed','hash',0,'old','old',100,1)")
    with pytest.raises(access.AIUnavailable,match='busy'):
        with access.authorised_request(user,True), access.reserve(1,10): pass


@pytest.mark.parametrize('failure',[TimeoutError('secret fake-server-key portfolio=123'),RuntimeError('429 rate limit'),RuntimeError('401 invalid credentials')])
def test_provider_errors_are_redacted_attempts_counted_no_retry(monkeypatch,tmp_path,failure):
    path,user=configure(monkeypatch,tmp_path);calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @property
        def responses(self):return self
        def create(self,**kwargs):calls.append(kwargs);raise failure
    monkeypatch.setattr(copilot,'_client',lambda *a: Client())
    with pytest.raises(access.AIUnavailable) as caught:
        with access.authorised_request(user,True):paid()
    assert 'fake-server-key' not in str(caught.value) and 'portfolio=123' not in str(caught.value)
    assert len(calls)==1 and calls[0]['store'] is False
    with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*),SUM(active) FROM reservations').fetchone()==(1,0)


def test_client_has_fixed_endpoint_timeout_no_retries(monkeypatch,tmp_path):
    configure(monkeypatch,tmp_path);import openai
    captured={}
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs: captured.update(kwargs))
    copilot._client('fake-server-key')
    assert captured['timeout']==20 and captured['max_retries']==0
    assert captured['base_url']=='https://api.openai.com/v1'


def test_outdated_auth_dependency_fails_closed(monkeypatch):
    monkeypatch.setattr(access, 'version', lambda name: '48.0.1' if name=='cryptography' else '1.8.0')
    with pytest.raises(access.AIUnavailable,match='patched'):
        access.verify_auth_stack()


@pytest.mark.parametrize('data,name',[(b'PK\x03\x04garbage','bad.xlsx'),(b'\xff\xfe','bad.csv'),(b'a,b\n1,2,3','bad.csv'),(b'a,a\n1,2','bad.csv'),(b'\x00a,b','bad.csv'),(b'a,b\n1,2','bad.xlsm'),(b'<html>fake</html>','fake.xlsx'),(b'\0'*(MAX_FILE_BYTES+1),'big.csv')])
def test_invalid_uploads_rejected(data,name):
    with pytest.raises(ValueError):read_portfolio_file(data,name)


def workbook(values):
    book=Workbook();sheet=book.active
    for row in values:sheet.append(row)
    result=BytesIO();book.save(result);return result.getvalue()


def test_workbook_active_content_and_sparse_resource_limits():
    for data in [workbook([['Ticker','Quantity'],['AAA','=1+1']]),workbook([['Ticker','Quantity'],['AAA',1]+['x']*64])]:
        with pytest.raises(ValueError):read_portfolio_file(data,'bad.xlsx')
    result=BytesIO()
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','x');z.writestr('xl/workbook.xml','x')
        z.writestr('bomb',b'0'*(26*1024*1024))
    with pytest.raises(ValueError,match='expansion'):read_portfolio_file(result.getvalue(),'bad.xlsx')
    with pytest.raises(ValueError):read_portfolio_file(b'Ticker,Quantity\n'+b'A,1\n'*(MAX_ROWS+1),'many.csv')
    with pytest.raises(ValueError):parse_dataframe(pd.DataFrame({'Ticker':['bad\nidentifier'],'Quantity':[1]}))
    with pytest.raises(ValueError):validate_batch([('a.csv',b'x')]*11)


def test_safe_exports_leave_financial_and_original_data_intact():
    frame=pd.DataFrame({'Text':['=1+1',' +SUM(A1)','@cmd','ordinary'], 'Value':[-10,0,1,2]})
    original=frame.copy(deep=True);safe=spreadsheet_safe(frame)
    assert safe.Text.tolist()==["'=1+1","' +SUM(A1)","'@cmd",'ordinary']
    assert safe.Value.tolist()==[-10,0,1,2] and frame.equals(original)


def test_constituent_inputs_keep_large_fund_composition_while_owned_book_is_bounded():
    from portfolio_analytics.input_engine.constituents import stage_constituents
    frame=pd.DataFrame({'Ticker':[f'S{i}' for i in range(500)],'Weight':[.2]*500,'As Of Date':['2026-10-09']*500})
    data=frame.to_csv(index=False).encode()
    draft=stage_constituents([('fund.csv',data)],selected_parent='VOO')
    assert len(draft['records'])==500 and not draft['files'][0]['errors']
    with pytest.raises(ValueError):parse_dataframe(pd.DataFrame({'Ticker':frame.Ticker,'Quantity':[1]*500}))


def test_session_user_switch_and_restarts_do_not_reuse_portfolio():
    state={};a=User(iss='https://issuer',sub='alice');b=User(iss='https://issuer',sub='bob')
    synchronise_owner(state,a);state.update(portfolio_state={'private':1},copilot_messages=['secret'],fund_constituents={'x':1})
    synchronise_owner(state,a);assert state['portfolio_state']
    synchronise_owner(state,b);assert set(state)=={'_workspace_owner'}
    fresh={};synchronise_owner(fresh,a);assert 'portfolio_state' not in fresh


def test_copilot_context_excludes_raw_records_and_provenance():
    state,parsed,catalog,store,decl=fixture();analytics={'lookthrough':calc(state,catalog,store,decl)}
    state['inputs']['user_dataset']={'records':[{'secret_account':'secret-marker'}]}
    state['consolidation']={'records':['secret-marker']}
    context=copilot.build_copilot_context(parsed,state,analytics)
    payload=json.dumps(context,default=str)
    assert 'secret-marker' not in payload and 'source_fingerprint' not in payload and 'normalised_dataset' not in payload
    assert context['portfolio_state']['totals']['equity']==6300
