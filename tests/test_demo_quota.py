"""No paid calls: real persistent storage, signed ingress and mocked SDK."""
import json
import multiprocessing
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest
import streamlit as st
from portfolio_analytics.security import ai_access as access, demo_quota as demo
from portfolio_analytics.ai import copilot
from tests.test_discover import upload_app, button, workspace


def configure(monkeypatch, tmp_path):
    path = tmp_path/'private'/'budget.sqlite'
    access.initialise_budget(path)
    settings = {'AI_ACCESS_POLICY':'public_demo','AI_DEPLOYMENT_ARCHITECTURE':'single_host',
        'AI_BUDGET_DB_PATH':str(path),'AI_DEMO_ID':'blackrock-demo',
        'AI_DEMO_END_EPOCH':str(int(time.time())+86400*7),
        'AI_PROXY_SIGNING_KEY':'01'*32,'AI_IP_HASH_KEY':'02'*32,
        'AI_PROXY_VERIFIED':'signed_nginx_single_host','AI_ALLOWED_MODEL':copilot.DEFAULT_MODEL}
    for key,value in settings.items(): monkeypatch.setenv(key,value)
    demo.provision_demo(path)
    return path, demo.sign_ip('203.0.113.42')


def submit(headers):
    return demo.authorised_demo_question(headers,True,'Explain my portfolio')


def attempt():
    return copilot._provider_response('dummy-not-a-credential',model=copilot.DEFAULT_MODEL,
        instructions='explain',input='synthetic evidence',max_output_tokens=10)


def mock_client(monkeypatch, fail=False):
    calls=[]
    class Client:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        @property
        def responses(self): return self
        def create(self,**kwargs):
            calls.append(kwargs)
            if fail: raise TimeoutError('private-data-and-key-must-not-leak')
            return SimpleNamespace(output_text='Synthetic explanation')
    monkeypatch.setattr(copilot,'_client',lambda *a:Client())
    return calls


def test_five_questions_two_provider_calls_each_then_sixth_denied(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path);calls=mock_client(monkeypatch)
    for expected in (4,3,2,1,0):
        with submit(headers): attempt(); attempt()
        assert demo.remaining(headers)==expected
    with pytest.raises(access.AIUnavailable,match='five AI questions'):
        with submit(headers): attempt()
    assert len(calls)==10
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM demo_questions').fetchone()[0]==5
        assert db.execute('SELECT COUNT(*),SUM(active) FROM reservations').fetchone()==(10,0)
        assert '203.0.113.42' not in '\n'.join(db.iterdump())


def test_restart_session_clear_chat_and_day_change_never_reset(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path)
    for _ in range(5):
        with submit(headers): pass
    now=time.time();monkeypatch.setattr(demo.time,'time',lambda:now+86400)
    assert demo.remaining(demo.sign_ip('203.0.113.42'))==0
    assert demo.remaining(demo.sign_ip('203.0.113.43'))==5
    assert demo.remaining(demo.sign_ip('::ffff:203.0.113.42'))==0
    a=demo.sign_ip('2001:db8::1'); b=demo.sign_ip('2001:0db8:0000:0000:0000:0000:0000:0001')
    assert demo.verified_actor(a)==demo.verified_actor(b)


@pytest.mark.parametrize('kind',['missing','xff','forged','changed_ip','duplicate','old','future','scope','malformed'])
def test_spoofed_or_expired_ingress_denied_before_provider(monkeypatch,tmp_path,kind):
    path,headers=configure(monkeypatch,tmp_path)
    monkeypatch.setattr(copilot,'_client',lambda *a:pytest.fail('Provider must not be reached'))
    if kind=='missing':headers={}
    elif kind=='xff':headers={'X-Forwarded-For':'203.0.113.42'}
    elif kind=='forged':headers['X-Demo-Signature']='0'*64
    elif kind=='changed_ip':headers['X-Demo-IP']='203.0.113.43'
    elif kind=='old':headers=demo.sign_ip('203.0.113.42',int(time.time())-13*3600)
    elif kind=='future':headers=demo.sign_ip('203.0.113.42',int(time.time())+60)
    elif kind=='scope':headers['X-Demo-IP']='fe80::1%eth0'
    elif kind=='malformed':headers['X-Demo-IP']='203.0.113.42,203.0.113.43'
    else:
        original = headers.copy()
        class Duplicate:
            def get_all(self,name):return [original[name],original[name]]
        headers=Duplicate()
    with pytest.raises(access.AIUnavailable):
        with submit(headers):attempt()
    with sqlite3.connect(path) as db:assert db.execute('SELECT COUNT(*) FROM demo_questions').fetchone()[0]==0


@pytest.mark.parametrize('setting,value',[('AI_ACCESS_POLICY','disabled'),('AI_PROXY_VERIFIED',''),
    ('AI_DEPLOYMENT_ARCHITECTURE','multi_host'),('AI_PROXY_SIGNING_KEY','ab'),
    ('AI_IP_HASH_KEY','01'*32),('AI_DEMO_END_EPOCH','1')])
def test_incomplete_or_disabled_configuration_denies(monkeypatch,tmp_path,setting,value):
    path,headers=configure(monkeypatch,tmp_path);monkeypatch.setenv(setting,value)
    monkeypatch.setattr(copilot,'_client',lambda *a:pytest.fail('No SDK access'))
    with pytest.raises(access.AIUnavailable):
        with submit(headers):attempt()


@pytest.mark.parametrize('setting,value',[('AI_DEMO_ID','new-demo'),('AI_IP_HASH_KEY','03'*32),('AI_PROXY_SIGNING_KEY','04'*32)])
def test_operator_key_or_demo_change_cannot_reset_allowance(monkeypatch,tmp_path,setting,value):
    path,_=configure(monkeypatch,tmp_path);monkeypatch.setenv(setting,value)
    with pytest.raises(access.AIUnavailable,match='configuration changed'):
        demo.remaining(demo.sign_ip('203.0.113.42'))


def test_storage_failure_and_permissions_fail_closed(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path)
    path.chmod(0o644)
    with pytest.raises(access.AIUnavailable):demo.remaining(headers)
    path.chmod(0o600);path.write_bytes(b'corrupted')
    with pytest.raises(access.AIUnavailable):
        with submit(headers):pass
    path.unlink()
    with pytest.raises(access.AIUnavailable):demo.remaining(headers)
    assert not path.exists()


def _worker(env,queue):
    os.environ.update(env)
    try:
        with submit(demo.sign_ip('203.0.113.42')):pass
        queue.put(True)
    except access.AIUnavailable:queue.put(False)


def test_separate_processes_atomically_admit_only_five(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path)
    env={k:v for k,v in os.environ.items() if k.startswith('AI_')}
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
    processes=[ctx.Process(target=_worker,args=(env,queue)) for _ in range(8)]
    for process in processes:process.start()
    admitted=[queue.get(timeout=30) for _ in processes]
    for process in processes:process.join(timeout=10);assert process.exitcode==0
    assert sum(admitted)==5 and demo.remaining(headers)==0


def test_failures_count_one_question_and_one_attempt_no_retry(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path);calls=mock_client(monkeypatch,fail=True)
    with pytest.raises(access.AIUnavailable) as error:
        with submit(headers):attempt()
    assert 'private-data' not in str(error.value)
    assert len(calls)==1 and demo.remaining(headers)==4


def test_only_questions_can_call_provider_maximum_two_attempts(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path);calls=mock_client(monkeypatch)
    with pytest.raises(access.AIUnavailable):attempt()
    with submit(headers):
        attempt();attempt()
        with pytest.raises(access.AIUnavailable,match='two provider'):attempt()
    assert len(calls)==2 and demo.remaining(headers)==4


@pytest.mark.parametrize('setting,value',[('AI_DEMO_TOTAL_REQUESTS','1'),('AI_DEMO_TOTAL_TOKENS','600'),('AI_GLOBAL_MONTHLY_REQUESTS','1')])
def test_shared_demo_lifetime_and_existing_monthly_limits(monkeypatch,tmp_path,setting,value):
    path,headers=configure(monkeypatch,tmp_path);monkeypatch.setenv(setting,value)
    with submit(headers),access.reserve(50,10):pass
    with pytest.raises(access.AIUnavailable,match='exhausted'):
        with submit(demo.sign_ip('203.0.113.43')),access.reserve(50,10):pass


def test_lifetime_global_ceiling_survives_month_rollover(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path);monkeypatch.setenv('AI_DEMO_TOTAL_REQUESTS','1')
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO reservations VALUES ('old','someone',0,'2000-01-01','2000-01',600,0)")
    with pytest.raises(access.AIUnavailable,match='demonstration AI allowance'):
        with submit(headers),access.reserve(1,10):pass


def test_question_and_consent_validation_do_not_debit(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path)
    for consent,question in [(False,'hello'),(True,''),(True,'x'*1001)]:
        with pytest.raises(access.AIUnavailable):
            with demo.authorised_demo_question(headers,consent,question):pass
    assert demo.remaining(headers)==5


def test_streamlit_navigation_and_five_question_user_journey(monkeypatch,tmp_path):
    path,headers=configure(monkeypatch,tmp_path);monkeypatch.setenv('OPENAI_API_KEY','dummy-not-a-credential')
    monkeypatch.setattr(st,'context',SimpleNamespace(headers=headers))
    calls=mock_client(monkeypatch)
    monkeypatch.setattr(copilot,'parse_route_text',lambda *a:{'intent':'explain'})
    monkeypatch.setattr(copilot,'execute_route',lambda *a,**k:{'kind':'explain','result':{'equity':100},'scenario':None})
    app,_=upload_app(monkeypatch,{'NVDA':85,'MSFT':15})
    accepted=app.session_state['portfolio_state']
    workspace(app,'Deep Analytics');workspace(app,'Portfolio dashboard')
    assert not calls and demo.remaining(headers)==5
    next(c for c in app.checkbox if c.key=='ai_submission_consent').check().run()
    for used in range(1,6):
        next(t for t in app.text_area if t.key=='copilot_question').set_value('Explain my portfolio').run()
        button(app,'Ask Copilot').click().run(timeout=20)
        assert not app.exception and demo.remaining(headers)==5-used
    assert len(calls)==10 and button(app,'Ask Copilot').disabled
    assert any('five AI questions' in i.value for i in app.info)
    button(app,'Clear chat').click().run()
    assert demo.remaining(headers)==0 and app.session_state['portfolio_state']==accepted


def test_compact_provider_evidence_preserves_numbers_and_full_execution_context(monkeypatch,tmp_path):
    from copy import deepcopy
    from tests.test_copilot_router import _engine_objects
    parsed,state,analytics,context=_engine_objects()
    context['analytics']['trust_diagnostics']={'valuation_status':'valid','risk_status':'unavailable','issues':['synthetic missing history']}
    original=deepcopy(context)
    wire=copilot._provider_evidence(context)
    assert context==original
    assert wire['portfolio_state']['totals']==context['portfolio_state']['totals']
    assert wire['analytics']['trust_diagnostics']==context['analytics']['trust_diagnostics']
    assert wire['analytics']['deep_findings']==context['analytics']['deep_findings']
    assert 'series' not in wire['analytics'] and 'chart_series_not_submitted' in wire['analytics']
    assert len(json.dumps(wire,default=str).encode()) < len(json.dumps(context,default=str).encode())


def test_full_copilot_routes_explains_and_scenarios_with_mocked_provider(monkeypatch,tmp_path):
    from tests.test_copilot_router import _engine_objects
    path,headers=configure(monkeypatch,tmp_path)
    parsed,state,analytics,_=_engine_objects();calls=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @property
        def responses(self):return self
        def create(self,**kwargs):
            calls.append(kwargs)
            if 'intent router' in kwargs['instructions']:
                return SimpleNamespace(output_text=json.dumps({'intent':'scenario','actions':[{'action':'price_shock','ticker':'GLD','value':-15}]}))
            return SimpleNamespace(output_text='Synthetic scenario explanation: GLD falls 15%.')
    monkeypatch.setattr(copilot,'_client',lambda *a:Client())
    before=json.dumps(state,default=str)
    with submit(headers):
        result=copilot.ask_copilot('What if GLD falls 15%?',parsed=parsed,state=state,analytics=analytics,api_key='dummy')
    assert len(calls)==2 and demo.remaining(headers)==4
    assert result['scenario']['comparison']['equity_change']==pytest.approx(-300)
    assert json.dumps(state,default=str)==before
    assert all(len((c['instructions']+c['input']).encode())<=32000 for c in calls)


def test_private_http_signer_uses_only_fixed_peer_header(monkeypatch,tmp_path):
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from scripts.demo_ingress_signer import Signer
    configure(monkeypatch,tmp_path)
    server=HTTPServer(('127.0.0.1',0),Signer)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/sign'
    try:
        with urlopen(Request(url,headers={'X-Ingress-Peer':'203.0.113.42','X-Forwarded-For':'attacker'}),timeout=2) as response:
            assert response.status==204
            assert demo.verified_actor(response.headers)==demo.verified_actor(demo.sign_ip('203.0.113.42'))
        with pytest.raises(HTTPError):urlopen(Request(url,headers={'X-Forwarded-For':'203.0.113.42'}),timeout=2)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
