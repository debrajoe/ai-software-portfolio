from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
import sqlite3,threading
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from council_chamber.app import create_app
from council_chamber.auth import Principal
from council_chamber.config import Settings

class Verifier:
    def verify(self,token):
        if token=='owner':return Principal('owner-1','Owner','owner',True)
        if token in {'alice','bob','outsider'}:return Principal(token,token.title(),'agent_member',False)
        raise HTTPException(401,'Invalid token')

def auth(who='owner'):return {'Authorization':'Bearer '+who}
def post(client,room,body='synthetic',who='owner',client_id=None):
    data={'body':body}
    if client_id is not None:data['client_message_id']=client_id
    return client.post(f'/v1/chambers/{room}/messages',headers=auth(who),json=data)
def history(client,room,who='owner',**params):
    return client.get(f'/v1/chambers/{room}/messages',headers=auth(who),params=params)
def enroll(client,room,who):
    r=client.post(f'/v1/chambers/{room}/participants',headers=auth(),json={'subject':who,'display_name':who.title(),'role':'agent_member','kind':'agent'})
    assert r.status_code==201

def snapshot(path):
    with sqlite3.connect(path) as c:
        return {'messages':c.execute('SELECT public_id,chamber_id,participant_id,body,client_message_id FROM messages ORDER BY id').fetchall(),
                'allowed_writes':c.execute("SELECT COUNT(*) FROM audit_events WHERE action='message.create' AND outcome='allowed'").fetchone()[0]}

@pytest.fixture
def context(tmp_path):
    settings=Settings(str(tmp_path/'boundary.db'),'https://issuer','api','https://issuer/certs','owner-1')
    with TestClient(create_app(settings,Verifier())) as client:
        rooms=[]
        for name in ['A','B']:
            r=client.post('/v1/chambers',headers=auth(),json={'name':name});assert r.status_code==201;rooms.append(r.json()['id'])
        enroll(client,rooms[0],'alice');enroll(client,rooms[1],'bob')
        yield SimpleNamespace(client=client,settings=settings,a=rooms[0],b=rooms[1])

@pytest.mark.parametrize('who,wrong_room',[('alice','b'),('bob','a'),('outsider','a'),('outsider','b')])
def test_membership_never_transfers_between_chambers(context,who,wrong_room):
    c=context;post(c.client,c.a,'A-private');post(c.client,c.b,'B-private')
    before=snapshot(c.settings.database_path);room=getattr(c,wrong_room)
    read=history(c.client,room,who)
    write=post(c.client,room,'intrusion',who)
    assert read.status_code==write.status_code==404
    assert read.json()==write.json()=={'detail':'Chamber not found'}
    assert snapshot(c.settings.database_path)==before
    assert 'private' not in read.text

@pytest.mark.parametrize('who,room_attr,expected',[('alice','a','A-private'),('bob','b','B-private')])
def test_member_reads_only_enrolled_room(context,who,room_attr,expected):
    c=context;post(c.client,c.a,'A-private');post(c.client,c.b,'B-private')
    r=history(c.client,getattr(c,room_attr),who);assert r.status_code==200
    assert [m['body'] for m in r.json()['messages']]==[expected]

def test_one_subject_in_two_chambers_keeps_distinct_membership_and_messages(context):
    c=context;enroll(c.client,c.b,'alice')
    first=post(c.client,c.a,'one','alice','same-id');second=post(c.client,c.b,'two','alice','same-id')
    assert first.status_code==second.status_code==201
    assert first.json()['participant']['id']!=second.json()['participant']['id']
    assert [m['body'] for m in history(c.client,c.a,'alice').json()['messages']]==['one']
    assert [m['body'] for m in history(c.client,c.b,'alice').json()['messages']]==['two']

def test_cross_chamber_cursor_does_not_reveal_or_grant_access(context):
    c=context;a1=post(c.client,c.a,'a1').json();b1=post(c.client,c.b,'b1').json();a2=post(c.client,c.a,'a2').json()
    r=history(c.client,c.a,'alice',after=b1['sequence'])
    assert [m['id'] for m in r.json()['messages']]==[a2['id']]
    assert history(c.client,c.b,'alice',after=a1['sequence']).status_code==404

@pytest.mark.parametrize('page_size',[1,2,3,500])
def test_pagination_walk_is_ordered_exclusive_and_gap_safe(context,page_size):
    c=context;expected=[]
    for i in range(7):
        expected.append(post(c.client,c.a,f'A{i}').json())
        post(c.client,c.b,f'B{i}')
    cursor=0;seen=[]
    for _ in range(10):
        r=history(c.client,c.a,'alice',after=cursor,limit=page_size);assert r.status_code==200
        page=r.json();batch=page['messages']
        assert len(batch)<=page_size
        assert all(m['sequence']>cursor for m in batch)
        if not batch:
            assert page['next_after']==cursor;break
        assert page['next_after']==batch[-1]['sequence']
        cursor=page['next_after'];seen.extend(batch)
    else:pytest.fail('Pagination failed to terminate')
    assert [m['id'] for m in seen]==[m['id'] for m in expected]
    assert len({m['id'] for m in seen})==7
    assert [m['body'] for m in seen]==[f'A{i}' for i in range(7)]

def test_empty_history_keeps_requested_cursor(context):
    for cursor in [0,42,2**63-1]:
        r=history(context.client,context.a,'alice',after=cursor)
        assert r.status_code==200 and r.json()=={'messages':[],'next_after':cursor}

def test_message_inserted_between_pages_is_returned_once(context):
    c=context;first=post(c.client,c.a,'first').json();second=post(c.client,c.a,'second').json()
    page=history(c.client,c.a,after=0,limit=1).json();assert page['messages'][0]['id']==first['id']
    post(c.client,c.b,'unrelated');third=post(c.client,c.a,'third').json()
    rest=history(c.client,c.a,after=page['next_after'],limit=500).json()
    assert [m['id'] for m in rest['messages']]==[second['id'],third['id']]
    assert history(c.client,c.a,after=rest['next_after']).json()['messages']==[]

@pytest.mark.parametrize('params',[{'after':-1},{'after':'invalid'},{'after':2**63},{'after':10**100},{'limit':0},{'limit':501},{'limit':-1},{'limit':'invalid'}])
def test_invalid_pagination_is_rejected_without_changes(context,params):
    before=snapshot(context.settings.database_path)
    r=history(context.client,context.a,**params)
    assert r.status_code==422
    assert snapshot(context.settings.database_path)==before

@pytest.mark.parametrize('changed_body',[False,True])
def test_duplicate_retry_returns_conflict_without_overwriting_or_extra_audit(context,changed_body):
    c=context;first=post(c.client,c.a,'original','alice','retry-1');assert first.status_code==201
    before=snapshot(c.settings.database_path)
    duplicate=post(c.client,c.a,'replacement' if changed_body else 'original','alice','retry-1')
    assert duplicate.status_code==409 and duplicate.json()=={'detail':'Duplicate client_message_id'}
    assert snapshot(c.settings.database_path)==before
    read=history(c.client,c.a,'alice').json()['messages']
    assert len(read)==1 and read[0]['id']==first.json()['id'] and read[0]['body']=='original'

def test_duplicate_key_scoped_to_participant(context):
    c=context
    assert post(c.client,c.a,'owner message','owner','shared').status_code==201
    assert post(c.client,c.a,'alice message','alice','shared').status_code==201
    assert len(history(c.client,c.a).json()['messages'])==2

def test_duplicate_identifier_does_not_disclose_other_chamber(context):
    c=context;assert post(c.client,c.b,'private','bob','known-id').status_code==201
    assert post(c.client,c.b,'intrusion','alice','known-id').status_code==404
    assert post(c.client,c.a,'allowed','alice','known-id').status_code==201

def test_retry_conflict_survives_new_app_instance(context):
    c=context;first=post(c.client,c.a,'original','alice','persist-key');assert first.status_code==201
    with TestClient(create_app(c.settings,Verifier())) as restarted:
        assert post(restarted,c.a,'retry','alice','persist-key').status_code==409
        assert history(restarted,c.a,'alice').json()['messages'][0]['id']==first.json()['id']

def test_concurrent_retry_has_one_commit_and_one_conflict(context):
    c=context;gate=threading.Barrier(2)
    with TestClient(create_app(c.settings,Verifier())) as second:
        def request(client):gate.wait(timeout=5);return post(client,c.a,'same','alice','concurrent-key')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(request,c.client),pool.submit(request,second)]
            responses=[f.result(timeout=15) for f in futures]
    assert sorted(r.status_code for r in responses)==[201,409]
    stored=snapshot(c.settings.database_path)
    assert len(stored['messages'])==stored['allowed_writes']==1

def test_absent_retry_id_permits_distinct_messages(context):
    c=context
    one=post(c.client,c.a,'same','alice');two=post(c.client,c.a,'same','alice')
    assert one.status_code==two.status_code==201 and one.json()['id']!=two.json()['id']

def test_empty_retry_id_is_still_a_real_deduplication_key(context):
    c=context;assert post(c.client,c.a,'one','alice','').status_code==201
    assert post(c.client,c.a,'two','alice','').status_code==409

@pytest.mark.parametrize('length,expected',[(100,201),(101,422)])
def test_client_message_id_length_boundary(context,length,expected):
    assert post(context.client,context.a,'valid','alice','x'*length).status_code==expected

@pytest.mark.parametrize('body',['',' ','\t\r\n','\u2003\u2003'])
def test_blank_messages_rejected_without_write(context,body):
    before=snapshot(context.settings.database_path)
    r=post(context.client,context.a,body,'alice')
    assert r.status_code==422 and snapshot(context.settings.database_path)==before

@pytest.mark.parametrize('character',['x','界','😀'])
@pytest.mark.parametrize('length,expected',[(19999,201),(20000,201),(20001,413)])
def test_message_size_limit_is_characters_after_trim(context,character,length,expected):
    c=context;before=snapshot(c.settings.database_path)
    r=post(c.client,c.a,character*length,'alice')
    assert r.status_code==expected
    if expected==201:
        assert r.json()['body']==character*length
        assert history(c.client,c.a,'alice').json()['messages'][0]['body']==character*length
    else:assert snapshot(c.settings.database_path)==before

def test_whitespace_is_trimmed_before_storage_and_length_check(context):
    body=' \t'+'x'*20000+'\n '
    r=post(context.client,context.a,body,'alice');assert r.status_code==201 and r.json()['body']=='x'*20000

def test_configured_smaller_message_limit_is_enforced(context):
    c=context
    with TestClient(create_app(replace(c.settings,max_message_chars=5),Verifier())) as small:
        assert post(small,c.a,'12345','alice').status_code==201
        before=snapshot(c.settings.database_path)
        assert post(small,c.a,'123456','alice').status_code==413
        assert snapshot(c.settings.database_path)==before

@pytest.mark.parametrize('payload',[{}, {'body':None}, {'body':12}])
def test_invalid_message_payload_cannot_mutate_state(context,payload):
    c=context;before=snapshot(c.settings.database_path)
    r=c.client.post(f'/v1/chambers/{c.a}/messages',headers=auth('alice'),json=payload)
    assert r.status_code==422 and snapshot(c.settings.database_path)==before

def test_default_page_size_and_maximum_page_cap_with_501_messages(context):
    c=context;expected=[]
    for index in range(501):
        created=post(c.client,c.a,f'synthetic {index}','alice')
        assert created.status_code==201
        expected.append(created.json())
    default=history(c.client,c.a,'alice').json()
    assert [m['id'] for m in default['messages']]==[m['id'] for m in expected[:100]]
    assert default['next_after']==expected[99]['sequence']
    maximum=history(c.client,c.a,'alice',limit=500).json()
    assert [m['id'] for m in maximum['messages']]==[m['id'] for m in expected[:500]]
    assert maximum['next_after']==expected[499]['sequence']
    final=history(c.client,c.a,'alice',after=maximum['next_after'],limit=500).json()
    assert [m['id'] for m in final['messages']]==[expected[500]['id']]
