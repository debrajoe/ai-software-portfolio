from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib.util
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from council_chamber.app import create_app
from council_chamber.auth import Principal
from council_chamber.config import Settings
from council_chamber.database import Database, SCHEMA
from council_chamber.service import CouncilService

OWNER=Principal('owner','Owner','owner',True)
AGENT=Principal('agent','Synthetic member','agent_member',False)
PRINCIPALS={'owner':OWNER,'agent':AGENT,
 'no-mfa':Principal('owner','Owner','owner',False),
 'owner-agent':Principal('owner','Owner','agent_member',False),
 'wrong-owner':Principal('wrong','Wrong','owner',True),
 'outsider':Principal('outsider','Outsider','agent_member',False)}
class SyntheticVerifier:
    def verify(self,token):
        if token not in PRINCIPALS:raise HTTPException(401,'Invalid token')
        return PRINCIPALS[token]
def auth(token='owner'):return {'Authorization':'Bearer '+token}
@pytest.fixture
def env(tmp_path):
    path=tmp_path/'sessions.db'
    client=TestClient(create_app(Settings(str(path),'https://synthetic.invalid','test','https://synthetic.invalid/jwks','owner'),SyntheticVerifier()))
    with client:
        chamber=client.post('/v1/chambers',headers=auth(),json={'name':'Synthetic council'}).json()['id']
        client.post(f'/v1/chambers/{chamber}/participants',headers=auth(),json={'subject':'agent','display_name':'Synthetic member','role':'agent_member','kind':'agent'})
        yield client,path,chamber

def base(chamber):return f'/v1/chambers/{chamber}'
def opened(client,chamber):
    r=client.post(base(chamber)+'/sessions',headers=auth(),json={'name':'Meeting'})
    assert r.status_code==201
    return r.json()['id']
def sessionpath(chamber,sid):return base(chamber)+'/sessions/'+sid

def test_workflow_legacy_history_sender_and_persistence(env):
    c,path,ch=env
    old=c.post(base(ch)+'/messages',headers=auth(),json={'body':'Legacy history'}).json()
    sid=opened(c,ch);url=sessionpath(ch,sid)
    r=c.post(url+'/messages',headers=auth('agent'),json={'body':'Session message','participant_id':'spoof'})
    assert r.status_code==201 and r.json()['session_id']==sid
    assert r.json()['participant']['display_name']=='Synthetic member'
    assert c.post(url+'/close',headers=auth()).json()['closed_at']
    assert c.post(url+'/messages',headers=auth('agent'),json={'body':'Too late'}).status_code==409
    history=c.get(url+'/messages',headers=auth('agent')).json()['messages']
    assert [m['body'] for m in history]==['Session message']
    allmessages=c.get(base(ch)+'/messages',headers=auth('agent')).json()['messages']
    assert [m['session_id'] for m in allmessages]==[None,sid]
    assert allmessages[0]['id']==old['id']
    Database(str(path))
    assert c.get(base(ch)+'/sessions',headers=auth('agent')).json()['sessions'][0]['closed_at']

@pytest.mark.parametrize('token',['agent','no-mfa','owner-agent','wrong-owner','outsider','invalid'])
@pytest.mark.parametrize('operation',['open','close'])
def test_owner_mfa_controls(env,token,operation):
    c,path,ch=env;sid=opened(c,ch)
    r=c.post(base(ch)+'/sessions' if operation=='open' else sessionpath(ch,sid)+'/close',headers=auth(token),json={'name':'Denied'})
    assert r.status_code in (401,403)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==1
        assert db.execute('SELECT closed_at FROM sessions').fetchone()[0] is None
        event=db.execute("SELECT actor_subject,resource_id,context_json FROM audit_events WHERE outcome='denied'").fetchone()
        assert event and (event[0]=='unauthenticated' or event[0].startswith('sha256:'))
        assert event[1].startswith('sha256:') and 'Denied' not in event[2]

@pytest.mark.parametrize('route',['list','read','write','close'])
def test_cross_chamber_session_id_is_hidden(env,route):
    c,path,ch=env;sid=opened(c,ch)
    other=c.post('/v1/chambers',headers=auth(),json={'name':'Other'}).json()['id']
    url=sessionpath(other,sid)
    if route=='read':r=c.get(url+'/messages',headers=auth())
    elif route=='write':r=c.post(url+'/messages',headers=auth(),json={'body':'Cross chamber'})
    elif route=='close':r=c.post(url+'/close',headers=auth())
    else:r=c.get(base(other)+'/sessions',headers=auth('agent'))
    assert r.status_code==404

@pytest.mark.parametrize('token',['outsider','no-mfa','owner-agent','wrong-owner','invalid'])
@pytest.mark.parametrize('write',[False,True])
def test_session_history_and_post_membership(env,token,write):
    c,path,ch=env;sid=opened(c,ch);url=sessionpath(ch,sid)+'/messages'
    r=c.post(url,headers=auth(token),json={'body':'Denied'}) if write else c.get(url,headers=auth(token))
    assert r.status_code in (401,403,404)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM audit_events WHERE outcome='denied'").fetchone()[0]==1

def test_open_uniqueness_close_conflicts_and_reopen(env):
    c,path,ch=env;sid=opened(c,ch)
    assert c.post(base(ch)+'/sessions',headers=auth(),json={'name':'Second'}).status_code==409
    assert c.post(sessionpath(ch,sid)+'/close',headers=auth()).status_code==200
    assert c.post(sessionpath(ch,sid)+'/close',headers=auth()).status_code==409
    new=opened(c,ch);assert new!=sid
    assert c.post(sessionpath(ch,sid)+'/messages',headers=auth(),json={'body':'Still closed'}).status_code==409
    assert c.post(sessionpath(ch,new)+'/messages',headers=auth(),json={'body':'New meeting'}).status_code==201

def test_pagination_and_duplicate_scope(env):
    c,path,ch=env;sid=opened(c,ch);url=sessionpath(ch,sid)+'/messages'
    for i in range(3):assert c.post(url,headers=auth('agent'),json={'body':str(i),'client_message_id':str(i)}).status_code==201
    assert c.post(url,headers=auth('agent'),json={'body':'Retry','client_message_id':'0'}).status_code==409
    first=c.get(url,headers=auth('agent'),params={'limit':2}).json()
    rest=c.get(url,headers=auth('agent'),params={'after':first['next_after']}).json()
    assert [m['body'] for m in first['messages']+rest['messages']]==['0','1','2']
    assert c.get(url,headers=auth(),params={'after':2**63}).status_code==422
    assert c.get(base(ch)+'/sessions',headers=auth(),params={'offset':2**63}).status_code==422

def test_inactive_or_changed_role_member(env):
    c,path,ch=env;sid=opened(c,ch)
    with sqlite3.connect(path) as db:db.execute("UPDATE users SET active=0 WHERE subject='agent'")
    assert c.get(sessionpath(ch,sid)+'/messages',headers=auth('agent')).status_code==404

def test_legacy_schema_migrates_idempotently_with_backup_restore(tmp_path):
    path=tmp_path/'legacy.db';backup=tmp_path/'backup.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO users VALUES ('u','owner','Owner','owner',1,'t')")
        db.execute("INSERT INTO chambers VALUES ('c','Legacy','u','t')")
        db.execute("INSERT INTO participants VALUES ('p','c','u','Owner','human','t')")
        db.execute("INSERT INTO messages VALUES (1,'m','c','p','Old text','retry','t')")
        db.commit()
        with sqlite3.connect(backup) as dest:db.backup(dest)
    Database(str(path));Database(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
        assert db.execute('SELECT body,session_id FROM messages').fetchone()==('Old text',None)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    with sqlite3.connect(backup) as source,sqlite3.connect(path) as dest:source.backup(dest)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==0
        assert 'session_id' not in [r[1] for r in db.execute('PRAGMA table_info(messages)')]
        assert db.execute('SELECT body FROM messages').fetchone()[0]=='Old text'

def test_unsupported_migration_refused(tmp_path):
    p=tmp_path/'future.db'
    with sqlite3.connect(p) as db:db.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError,match='Unsupported'):Database(str(p))

def test_database_rejects_cross_chamber_message_binding(env):
    c,path,ch=env;sid=opened(c,ch)
    other=c.post('/v1/chambers',headers=auth(),json={'name':'Other'}).json()['id']
    message=c.post(base(other)+'/messages',headers=auth(),json={'body':'Other'}).json()
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE messages SET session_id=? WHERE public_id=?',(sid,message['id']))

def test_close_post_serializes_and_later_posts_reject(env):
    c,path,ch=env;sid=opened(c,ch)
    service=CouncilService(Database(str(path)),'owner')
    def post():
        try:return service.send_message(AGENT,ch,'Concurrent',None,sid)
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        write=pool.submit(post);close=pool.submit(service.close_session,OWNER,ch,sid)
        result=write.result();assert close.result()['closed_at']
    assert isinstance(result,dict) or result==409
    with pytest.raises(HTTPException) as error:service.send_message(AGENT,ch,'Later',None,sid)
    assert error.value.status_code==409

def test_concurrent_open_creates_only_one_session(env):
    c,path,ch=env;service=CouncilService(Database(str(path)),'owner')
    def op():
        try:return service.open_session(OWNER,ch,'Concurrent')
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:op(),range(2)))
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results

def test_signed_token_synthetic_workflow():
    spec=importlib.util.spec_from_file_location('demo_sessions',Path(__file__).resolve().parents[1]/'demo/demo_sessions.py')
    demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
    assert demo.demonstrate()['result']=='PASS'

def test_session_denial_audit_survives_rollback(env):
    c,path,ch=env;sid=opened(c,ch)
    c.post(sessionpath(ch,sid)+'/close',headers=auth())
    with sqlite3.connect(path) as db:before=db.execute('SELECT count(*) FROM messages').fetchone()[0]
    response=c.post(sessionpath(ch,sid)+'/messages',headers=auth('agent'),json={'body':'Sensitive synthetic text'})
    assert response.status_code==409
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==before
        row=db.execute("SELECT * FROM audit_events WHERE outcome='denied'").fetchone()
        assert row and 'Sensitive synthetic text' not in str(row) and 'Bearer' not in str(row)

def test_migration_failure_rolls_back_additions(tmp_path):
    path=tmp_path/'conflict.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('CREATE TABLE sessions(conflict TEXT)')
    with pytest.raises(sqlite3.OperationalError):Database(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==0
        assert 'session_id' not in [r[1] for r in db.execute('PRAGMA table_info(messages)')]

def test_session_audit_storage_failure_preserves_denial(env,monkeypatch,caplog):
    c,path,ch=env;sid=opened(c,ch)
    def fail(*args):raise sqlite3.OperationalError('private database failure')
    monkeypatch.setattr(CouncilService,'audit_session_denial',fail)
    response=c.post(sessionpath(ch,sid)+'/close',headers=auth('agent'))
    assert response.status_code==403 and response.json()=={'detail':'Owner with MFA required'}
    assert 'Denied-message audit persistence failed' in caplog.text
    assert 'private database failure' not in caplog.text

def test_body_cannot_silently_select_closed_session(env):
    c,path,ch=env;sid=opened(c,ch)
    c.post(sessionpath(ch,sid)+'/close',headers=auth())
    r=c.post(base(ch)+'/messages',headers=auth('agent'),json={'body':'Wrong route','session_id':sid})
    assert r.status_code==422
    with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==0
