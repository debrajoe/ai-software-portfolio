import json,time
from dataclasses import replace
from types import SimpleNamespace
import jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from council_chamber.auth import OIDCTokenVerifier
from council_chamber.app import create_app
from council_chamber.config import Settings

@pytest.fixture
def context(tmp_path):
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    settings=Settings(str(tmp_path/'test.db'),'http://localhost:8080/realms/test','council-api','http://localhost:8080/certs','owner-1',oidc_role_client='council-api',oidc_authorized_party='council-local-demo')
    verifier=OIDCTokenVerifier(settings)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()));jwk.update(kid='test-key',use='sig',alg='RS256')
    verifier.jwks.fetch_data=lambda:{'keys':[jwk]}
    def token(changes=None,key=None):
        claims={'iss':settings.oidc_issuer,'aud':'council-api','sub':'owner-1','iat':int(time.time()),'exp':int(time.time())+120,'typ':'Bearer','azp':'council-local-demo','amr':['pwd','webauthn'],'resource_access':{'council-api':{'roles':['owner']}}}
        claims.update(changes or {})
        return jwt.encode(claims,key or private,algorithm='RS256',headers={'kid':'test-key'})
    return SimpleNamespace(settings=settings,verifier=verifier,token=token,private=private)

def test_valid_signed_owner(context):
    p=context.verifier.verify(context.token());assert p.role=='owner' and p.mfa_authenticated

@pytest.mark.parametrize('changes,status',[
    ({'iss':'http://attacker/'},401),({'aud':'other-api'},401),({'exp':1},401),
    ({'iat':int(time.time())+3600},401),({'sub':''},401),({'sub':12},401),
    ({'typ':'ID'},401),({'typ':'Refresh'},401),({'typ':None},401),
    ({'azp':'other-client'},403),({'sub':'other-owner'},403),
    ({'amr':['pwd']},403),({'amr':['webauthn']},403),({'amr':['mfa']},403),
    ({'amr':'pwd webauthn'},403),({'amr':[{}]},403),({'amr':None,'acr':'2'},403),
    ({'resource_access':{'council-api':{'roles':['owner','agent_member']}}},403),
    ({'resource_access':{'council-api':{'roles':['agent_member']}},'amr':[]},403),
    ({'resource_access':None,'role':'owner'},403),
    ({'resource_access':{'council-api':{'roles':'owner'}}},403),
    ({'resource_access':{'other-api':{'roles':['owner']}}},403),
])
def test_rejects_invalid_claims(context,changes,status):
    with pytest.raises(HTTPException) as exc:context.verifier.verify(context.token(changes))
    assert exc.value.status_code==status

def test_signature_and_unknown_kid(context):
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    with pytest.raises(HTTPException) as exc:context.verifier.verify(context.token(key=other))
    assert exc.value.status_code==401
    with pytest.raises(HTTPException):context.verifier.verify(jwt.encode({'sub':'owner-1'},other,algorithm='RS256',headers={'kid':'unknown'}))

def test_missing_required_claim(context):
    claims=jwt.decode(context.token(),options={'verify_signature':False});del claims['exp']
    with pytest.raises(HTTPException):context.verifier.verify(jwt.encode(claims,context.private,algorithm='RS256',headers={'kid':'test-key'}))

def test_unsecured_token_rejected(context):
    with pytest.raises(HTTPException):context.verifier.verify(jwt.encode({'sub':'owner-1'},'',algorithm='none'))

def test_owner_as_agent_denied_on_all_routes(context):
    with TestClient(create_app(context.settings,context.verifier)) as client:
        good={'Authorization':'Bearer '+context.token()}
        room=client.post('/v1/chambers',json={'name':'test'},headers=good).json()['id']
        bad={'Authorization':'Bearer '+context.token({'resource_access':{'council-api':{'roles':['agent_member']}},'amr':[]})}
        assert client.get(f'/v1/chambers/{room}/messages',headers=bad).status_code==403
        assert client.post(f'/v1/chambers/{room}/messages',headers=bad,json={'body':'denied'}).status_code==403
        assert client.post('/v1/chambers',headers=bad,json={'name':'denied'}).status_code==403

def test_agent_still_requires_stored_membership(context):
    with TestClient(create_app(context.settings,context.verifier)) as client:
        owner={'Authorization':'Bearer '+context.token()}
        room=client.post('/v1/chambers',json={'name':'test'},headers=owner).json()['id']
        agent={'Authorization':'Bearer '+context.token({'sub':'agent-1','amr':[],'resource_access':{'council-api':{'roles':['agent_member']}}})}
        assert client.get(f'/v1/chambers/{room}/messages',headers=agent).status_code==404
        assert client.post(f'/v1/chambers/{room}/participants',headers=owner,json={'subject':'agent-1','display_name':'Local test agent','role':'agent_member','kind':'agent'}).status_code==201
        assert client.post(f'/v1/chambers/{room}/messages',headers=agent,json={'body':'local test'}).status_code==201
        assert client.get(f'/v1/chambers/{room}/messages',headers=agent).status_code==200

def test_service_rejects_other_subject_claiming_owner(tmp_path):
    from council_chamber.auth import Principal
    class InjectedVerifier:
        def verify(self,token):return Principal('not-configured-owner','Impostor','owner',True)
    settings=Settings(str(tmp_path/'owner.db'),'https://issuer','api','https://issuer/certs','real-owner')
    with TestClient(create_app(settings,InjectedVerifier())) as client:
        assert client.post('/v1/chambers',headers={'Authorization':'Bearer injected'},json={'name':'denied'}).status_code==403
