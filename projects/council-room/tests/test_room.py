import base64
import json
from pathlib import Path
import time
from urllib.parse import parse_qs,urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from council_chamber.app import create_app
from council_chamber.auth import OIDCTokenVerifier,Principal
from council_chamber.config import Settings
from council_chamber.database import Database
from council_chamber.service import CouncilService
from council_chamber.room import create_room,ORIGIN,COOKIE

@pytest.fixture
def room(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    settings=Settings(str(tmp_path/'room.db'),'http://localhost:8080/realms/synthetic','council-api','http://localhost:8080/certs','owner',oidc_role_client='council-api',oidc_authorized_party='council-local-demo')
    verifier=OIDCTokenVerifier(settings)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='test',alg='RS256',use='sig');verifier.jwks.fetch_data=lambda:{'keys':[jwk]}
    backend=TestClient(create_app(settings,verifier))
    service=CouncilService(Database(settings.database_path),'owner');owner=Principal('owner','Synthetic owner','owner',True)
    chamber=service.create_chamber(owner,'Synthetic council')['id']
    service.add_participant(owner,chamber,'agent','Synthetic member','agent_member','agent')
    now=[time.time()];calls=[]
    def transport(request):
        calls.append((request.method,request.url.path))
        if request.url.path.endswith('/token'):
            code=parse_qs(request.content.decode()).get('code',[''])[0]
            if code=='exchange-fail':return httpx.Response(400,json={'error':'invalid_grant'})
            stamp=int(time.time());role='owner' if code in ('owner','no-mfa','owner-agent') else 'agent_member'
            if code=='owner-agent':role='agent_member'
            subject='owner' if code in ('owner','no-mfa','owner-agent') else code
            claims={'iss':settings.oidc_issuer,'aud':'council-api','sub':subject,'name':subject,'iat':stamp,'exp':stamp+120,'typ':'Bearer','azp':'council-local-demo','amr':['pwd','webauthn'] if code=='owner' else [],'resource_access':{'council-api':{'roles':[role]}}}
            token=jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test'})
            return httpx.Response(200,json={'access_token':token,'refresh_token':'not-retained'})
        result=backend.request(request.method,request.url.path+('?' +request.url.query.decode() if request.url.query else ''),headers=dict(request.headers),content=request.content)
        return httpx.Response(result.status_code,json=result.json())
    http=httpx.Client(transport=httpx.MockTransport(transport))
    app=create_room(settings.oidc_issuer,http=http,clock=lambda:now[0])
    with TestClient(app,base_url=ORIGIN) as client:
        yield client,chamber,now,calls,app
    http.close();backend.close()

def login(client,code='owner'):
    response=client.get('/signin',follow_redirects=False)
    query=parse_qs(urlparse(response.headers['location']).query)
    return client.get('/callback',params={'state':query['state'][0],'code':code},follow_redirects=False)
def csrf(client):return {'Origin':ORIGIN,'X-CSRF-Token':client.get('/api/me').json()['csrf']}

def test_screen_headers_and_pkce(room):
    c,ch,now,calls,app=room
    response=c.get('/')
    assert response.status_code==200 and 'Your council.' in response.text
    assert response.headers['cache-control']=='no-store' and "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert c.get('/room.js').status_code==200 and c.get('/room.css').status_code==200
    response=c.get('/signin',follow_redirects=False);query=parse_qs(urlparse(response.headers['location']).query)
    assert query['code_challenge_method']==['S256'] and query['redirect_uri']==[ORIGIN+'/callback']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']

@pytest.mark.parametrize('path',['/api/me','/api/v1/chambers'])
def test_anonymous_cannot_read(room,path):
    assert room[0].get(path).status_code==401

def test_full_browser_workflow_with_signed_tokens(room):
    c,ch,now,calls,app=room
    assert login(c).status_code==303
    me=c.get('/api/me').json();assert me['can_manage_sessions'] and 'token' not in me
    rooms=c.get('/api/v1/chambers').json()['chambers'];assert [r['id'] for r in rooms]==[ch]
    headers=csrf(c);url=f'/api/v1/chambers/{ch}/sessions'
    meeting=c.post(url,headers=headers,json={'name':'Synthetic browser meeting'});assert meeting.status_code==201
    sid=meeting.json()['id'];messages=url+'/'+sid+'/messages'
    assert c.post(messages,headers=headers,json={'body':'Owner contribution','client_message_id':'1'}).status_code==201
    with TestClient(app,base_url=ORIGIN) as member:
        assert login(member,'agent').status_code==303
        assert member.get('/api/me').json()['can_manage_sessions'] is False
        assert member.post(url+'/'+sid+'/close',headers=csrf(member),json={}).status_code==403
        assert member.post(messages,headers=csrf(member),json={'body':'Member contribution'}).status_code==201
        assert len(member.get(messages).json()['messages'])==2
        assert c.post(url+'/'+sid+'/close',headers=headers,json={}).status_code==200
        assert member.post(messages,headers=csrf(member),json={'body':'Too late'}).status_code==409
        assert len(member.get(messages).json()['messages'])==2
    assert c.post('/signout',headers=headers,json={}).status_code==200
    assert c.get('/api/me').status_code==401

@pytest.mark.parametrize('headers',[{}, {'Origin':'http://evil.invalid'}, {'Origin':ORIGIN,'X-CSRF-Token':'wrong'}])
def test_csrf_rejected_before_proxy(room,headers):
    c,ch,now,calls,app=room;login(c);before=len(calls)
    assert c.post(f'/api/v1/chambers/{ch}/sessions',headers=headers,json={'name':'Forbidden'}).status_code==403
    assert len(calls)==before
    assert c.post('/signout',headers=headers,json={}).status_code==403

def test_callback_state_cookie_binding_and_replay(room):
    c,ch,now,calls,app=room
    response=c.get('/signin',follow_redirects=False);state=parse_qs(urlparse(response.headers['location']).query)['state'][0]
    assert c.get('/callback',params={'state':'wrong','code':'owner'}).status_code==400
    assert c.get('/callback',params={'state':state,'code':'owner'},follow_redirects=False).status_code==303
    assert c.get('/callback',params={'state':state,'code':'owner'}).status_code==400

def test_callback_missing_cookie_rejected(room):
    c,ch,now,calls,app=room
    response=c.get('/signin',follow_redirects=False);state=parse_qs(urlparse(response.headers['location']).query)['state'][0];c.cookies.clear()
    assert c.get('/callback',params={'state':state,'code':'owner'}).status_code==400

@pytest.mark.parametrize('code',['no-mfa','owner-agent','exchange-fail'])
def test_failed_auth_never_creates_browser_session(room,code):
    c=room[0];assert login(c,code).status_code==401
    assert c.get('/api/me').status_code==401

def test_expiry_and_session_rotation(room):
    c,ch,now,calls,app=room;login(c);first=c.cookies.get(COOKIE);login(c)
    assert c.cookies.get(COOKIE)!=first
    now[0]+=181;assert c.get('/api/me').status_code==401

def test_login_flow_expiry(room):
    c,ch,now,calls,app=room
    response=c.get('/signin',follow_redirects=False);state=parse_qs(urlparse(response.headers['location']).query)['state'][0];now[0]+=301
    assert c.get('/callback',params={'state':state,'code':'owner'}).status_code==400

def test_proxy_is_not_an_arbitrary_url_or_backend_admin_proxy(room):
    c,ch,now,calls,app=room;login(c)
    assert c.get('/api/http://evil.invalid').status_code==404
    assert c.post('/api/v1/chambers',headers=csrf(c),json={'name':'Not exposed'}).status_code==404
    assert c.get('/',headers={'Host':'evil.invalid'}).status_code==400

def test_chamber_discovery_filters_membership(room):
    c,ch,now,calls,app=room;login(c,'outsider')
    assert c.get('/api/v1/chambers').json()=={'chambers':[]}

def test_cookie_contains_only_opaque_session_id(room):
    c=room[0];response=login(c);cookie=c.cookies.get(COOKIE)
    assert len(cookie)>=40 and '.' not in cookie and 'HttpOnly' in response.headers['set-cookie']
    assert 'access_token' not in response.text and 'refresh_token' not in response.text

def test_message_payload_limit(room):
    c,ch,now,calls,app=room;login(c)
    assert c.post(f'/api/v1/chambers/{ch}/sessions',headers=csrf(c),content=b'x'*100001).status_code==413

def test_no_external_services_configuration():
    with pytest.raises(RuntimeError):create_room('https://external.invalid/realm')

def test_selected_meeting_details_remain_available_beyond_list_page(room):
    c,ch,now,calls,app=room;login(c);headers=csrf(c);url=f'/api/v1/chambers/{ch}/sessions'
    selected=c.post(url,headers=headers,json={'name':'Meeting'}).json()['id']
    assert c.get(url+'/'+selected).json()['closed_at'] is None
    c.post(url+'/'+selected+'/close',headers=headers,json={})
    assert c.get(url+'/'+selected).json()['closed_at']
    with TestClient(app,base_url=ORIGIN) as outsider:
        login(outsider,'outsider');assert outsider.get(url+'/'+selected).status_code==404

def test_cross_chamber_screen_route_does_not_disclose_session(room):
    c,ch,now,calls,app=room;login(c);headers=csrf(c)
    url=f'/api/v1/chambers/{ch}/sessions'
    sid=c.post(url,headers=headers,json={'name':'Private'}).json()['id']
    assert c.get('/api/v1/chambers/not-a-member/sessions/'+sid).status_code==404

def test_hecate_invitation_proxy_has_csrf_and_is_disabled_without_key(room):
    from uuid import uuid4
    c,ch,now,calls,app=room;login(c);headers=csrf(c)
    sessions=f'/api/v1/chambers/{ch}/sessions'
    sid=c.post(sessions,headers=headers,json={'name':'Invitation test'}).json()['id']
    invitation=sessions+'/'+sid+'/hecate'
    assert c.post(invitation,json={'request_id':str(uuid4())}).status_code==403
    assert c.post(invitation,headers=headers,json={'request_id':str(uuid4())}).status_code==503
    assert c.post(invitation,headers=headers,json={'request_id':'not-a-uuid'}).status_code==422

def test_mercury_proxy_requires_csrf_and_is_disabled_without_provider(room):
    from uuid import uuid4
    c,ch,now,calls,app=room;login(c);headers=csrf(c)
    sessions=f'/api/v1/chambers/{ch}/sessions'
    sid=c.post(sessions,headers=headers,json={'name':'Mercury test'}).json()['id']
    path=sessions+'/'+sid+'/mercury'
    assert c.post(path,json={'request_id':str(uuid4())}).status_code==403
    assert c.post(path,headers=headers,json={'request_id':str(uuid4())}).status_code==503

def test_jupiter_proxy_requires_csrf_and_is_disabled_without_provider(room):
    from uuid import uuid4
    c,ch,now,calls,app=room;login(c);headers=csrf(c)
    sessions=f'/api/v1/chambers/{ch}/sessions'
    sid=c.post(sessions,headers=headers,json={'name':'Jupiter test'}).json()['id']
    path=sessions+'/'+sid+'/jupiter'
    assert c.post(path,json={'request_id':str(uuid4())}).status_code==403
    assert c.post(path,headers=headers,json={'request_id':str(uuid4())}).status_code==503

def test_daedalus_proxy_requires_csrf_and_is_disabled_without_provider(room):
    from uuid import uuid4
    c,ch,now,calls,app=room;login(c);headers=csrf(c)
    sessions=f'/api/v1/chambers/{ch}/sessions'
    sid=c.post(sessions,headers=headers,json={'name':'Daedalus test'}).json()['id']
    path=sessions+'/'+sid+'/daedalus'
    assert c.post(path,json={'request_id':str(uuid4())}).status_code==403
    assert c.post(path,headers=headers,json={'request_id':str(uuid4())}).status_code==503
