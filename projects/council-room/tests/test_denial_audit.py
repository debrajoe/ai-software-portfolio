from __future__ import annotations
import hashlib,json,sqlite3
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from council_chamber.app import create_app
from council_chamber.auth import Principal
from council_chamber.config import Settings
from council_chamber.database import Database
from council_chamber.service import CouncilService

OWNER=Principal('owner-1','Private Owner Name','owner',True)
OUTSIDER=Principal('outsider-secret-subject','Private Display Name','agent_member',False)
class Verifier:
    def verify(self,token):
        if token=='owner':return OWNER
        if token=='outsider':return OUTSIDER
        if token=='owner-as-agent':return Principal('owner-1','private','agent_member',False)
        if token=='owner-no-mfa':raise HTTPException(403,'Owner MFA is required')
        raise HTTPException(401,'Invalid token',headers={'WWW-Authenticate':'Bearer'})

def headers(token):return {'Authorization':'Bearer '+token}
def digest(value):return 'sha256:'+hashlib.sha256(value.encode()).hexdigest()
def rows(path):
    with sqlite3.connect(path) as con:
        con.row_factory=sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM audit_events WHERE outcome='denied' ORDER BY id")]

@pytest.fixture
def context(tmp_path):
    settings=Settings(str(tmp_path/'audit.db'),'https://issuer','api','https://issuer/certs','owner-1')
    with TestClient(create_app(settings,Verifier())) as client:
        room=client.post('/v1/chambers',headers=headers('owner'),json={'name':'private room name'}).json()['id']
        yield client,settings,room

def denied_request(client,room,method,token='outsider'):
    kw={'headers':headers(token)} if token is not None else {}
    if method=='POST':kw['json']={'body':'PRIVATE MESSAGE MUST NEVER APPEAR','client_message_id':'PRIVATE CLIENT ID'}
    return client.request(method,f'/v1/chambers/{room}/messages',**kw)

@pytest.mark.parametrize('method,action',[('GET','message.list'),('POST','message.create')])
def test_membership_denial_is_persisted_without_sensitive_metadata(context,method,action):
    client,settings,room=context
    response=denied_request(client,room,method)
    assert response.status_code==404 and response.json()=={'detail':'Chamber not found'}
    event,=rows(settings.database_path)
    assert event['action']==action and event['resource_type']=='chamber'
    assert event['actor_subject']==digest(OUTSIDER.subject) and event['resource_id']==digest(room)
    assert json.loads(event['context_json'])=={'status_code':404,'reason':'access_denied'}
    serialized=json.dumps(event)
    for secret in ['PRIVATE MESSAGE','PRIVATE CLIENT ID',OUTSIDER.subject,OUTSIDER.display_name,room,'private room name','Bearer outsider']:
        assert secret not in serialized

@pytest.mark.parametrize('method',['GET','POST'])
@pytest.mark.parametrize('token,status',[('owner-as-agent',403),('owner-no-mfa',403),('invalid-secret-bearer',401),(None,401)])
def test_authentication_and_owner_denials_are_audited(context,method,token,status):
    client,settings,room=context
    response=denied_request(client,room,method,token)
    assert response.status_code==status
    event,=rows(settings.database_path)
    assert json.loads(event['context_json'])['status_code']==status
    assert event['actor_subject']==(digest(OWNER.subject) if token=='owner-as-agent' else 'unauthenticated')
    if token=='invalid-secret-bearer':assert response.headers['WWW-Authenticate']=='Bearer'
    assert (token or 'missing-authorization') not in json.dumps(event)

@pytest.mark.parametrize('method',['GET','POST'])
def test_nonexistent_and_existing_chambers_are_indistinguishable(context,method):
    client,settings,room=context
    first=denied_request(client,room,method)
    missing=denied_request(client,'does-not-exist',method)
    assert first.status_code==missing.status_code==404
    assert first.content==missing.content
    assert len(rows(settings.database_path))==2

@pytest.mark.parametrize('method',['GET','POST'])
def test_denial_survives_actual_transaction_rollback_and_reopen(context,monkeypatch,method):
    client,settings,room=context
    def mutate_then_deny(self,connection,chamber_id,principal):
        connection.execute("UPDATE chambers SET name='SHOULD ROLL BACK' WHERE id=?",(chamber_id,))
        raise HTTPException(404,'Chamber not found')
    monkeypatch.setattr(CouncilService,'_membership',mutate_then_deny)
    response=denied_request(client,room,method)
    assert response.status_code==404
    # Fresh application and connection prove the denial event was committed to disk.
    Database(settings.database_path)
    assert len(rows(settings.database_path))==1
    with sqlite3.connect(settings.database_path) as con:
        assert con.execute('SELECT name FROM chambers WHERE id=?',(room,)).fetchone()[0]=='private room name'
        assert con.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0

@pytest.mark.parametrize('method',['GET','POST'])
def test_untrusted_path_headers_and_payload_do_not_enter_audit(context,method):
    client,settings,_=context
    payload='secret-'+('A'*10000)
    response=client.request(method,f'/v1/chambers/{payload}/messages?secret_query=DO_NOT_LOG',headers={**headers('outsider'),'X-Forwarded-For':'DO_NOT_LOG'},**({'json':{'body':'DO_NOT_LOG'}} if method=='POST' else {}))
    assert response.status_code==404
    event,=rows(settings.database_path)
    assert event['resource_id']==digest(payload)
    assert 'DO_NOT_LOG' not in json.dumps(event) and payload not in json.dumps(event)
    assert len(event['actor_subject'])==len(event['resource_id'])==71

@pytest.mark.parametrize('method',['GET','POST'])
def test_audit_storage_failure_keeps_denial_and_emits_only_fixed_warning(context,monkeypatch,caplog,method):
    client,settings,room=context
    def unavailable(*args):raise sqlite3.OperationalError('PRIVATE SQL ERROR MUST NOT LEAK')
    monkeypatch.setattr(CouncilService,'audit_message_denial',unavailable)
    response=denied_request(client,room,method)
    assert response.status_code==404 and response.json()=={'detail':'Chamber not found'}
    assert len(rows(settings.database_path))==0
    assert 'Denied-message audit persistence failed' in caplog.text
    assert 'PRIVATE SQL ERROR' not in caplog.text and OUTSIDER.subject not in caplog.text

def test_success_and_validation_errors_not_mislabeled_as_denials(context):
    client,settings,room=context
    assert client.get(f'/v1/chambers/{room}/messages',headers=headers('owner')).status_code==200
    assert client.post(f'/v1/chambers/{room}/messages',headers=headers('owner'),json={'body':'allowed'}).status_code==201
    assert client.post(f'/v1/chambers/{room}/messages',headers=headers('owner'),json={'body':' '}).status_code==422
    assert client.get(f'/v1/chambers/{room}/messages?limit=0',headers=headers('owner')).status_code==422
    assert client.get('/unknown').status_code==404
    assert client.post('/v1/chambers',headers=headers('owner-no-mfa'),json={'name':'denied'}).status_code==403
    assert rows(settings.database_path)==[]
    with sqlite3.connect(settings.database_path) as con:
        assert con.execute("SELECT COUNT(*) FROM audit_events WHERE outcome='allowed'").fetchone()[0]==3

def test_deactivated_member_cannot_access_and_denial_is_recorded(context):
    client,settings,room=context
    assert client.post(f'/v1/chambers/{room}/participants',headers=headers('owner'),json={'subject':OUTSIDER.subject,'display_name':'Agent','role':'agent_member','kind':'agent'}).status_code==201
    with sqlite3.connect(settings.database_path) as con:con.execute('UPDATE users SET active=0 WHERE subject=?',(OUTSIDER.subject,))
    assert denied_request(client,room,'GET').status_code==404
    assert denied_request(client,room,'POST').status_code==404
    assert len(rows(settings.database_path))==2
