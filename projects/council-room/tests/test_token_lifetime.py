from __future__ import annotations
from types import SimpleNamespace
import json
import jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa,ec
from fastapi import HTTPException
from fastapi.testclient import TestClient
from council_chamber.auth import OIDCTokenVerifier
from council_chamber.app import create_app
from council_chamber.config import Settings
from test_jwks_lifecycle import clock

@pytest.fixture(params=['RS256','ES256'])
def context(request,clock,tmp_path):
    algorithm=request.param
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048) if algorithm=='RS256' else ec.generate_private_key(ec.SECP256R1())
    jwk_class=jwt.algorithms.RSAAlgorithm if algorithm=='RS256' else jwt.algorithms.ECAlgorithm
    jwk=json.loads(jwk_class.to_jwk(private.public_key()));jwk.update(kid='lifetime-key',alg=algorithm,use='sig')
    settings=Settings(str(tmp_path/'lifetime.db'),'http://localhost/realms/lifetime','council-api','http://localhost/certs','owner-1',oidc_role_client='council-api',oidc_authorized_party='council-local-demo')
    verifier=OIDCTokenVerifier(settings);verifier.jwks.fetch_data=lambda:{'keys':[jwk]}
    def sign(lifetime=180,role='owner',changes=None,remove=()):
        claims={'iss':settings.oidc_issuer,'aud':'council-api','sub':'owner-1' if role=='owner' else 'agent-1','iat':clock.seconds(),'exp':clock.seconds()+lifetime,'typ':'Bearer','azp':'council-local-demo','resource_access':{'council-api':{'roles':[role]}},'amr':['pwd','webauthn'] if role=='owner' else []}
        claims.update(changes or {})
        for name in remove:claims.pop(name,None)
        return jwt.encode(claims,private,algorithm=algorithm,headers={'kid':'lifetime-key'})
    return SimpleNamespace(settings=settings,verifier=verifier,sign=sign,clock=clock)

def denied(context,token):
    with pytest.raises(HTTPException) as e:context.verifier.verify(token)
    assert e.value.status_code==401 and e.value.detail=='Invalid or expired access token'
    assert e.value.headers=={'WWW-Authenticate':'Bearer'}

@pytest.mark.parametrize('role',['owner','agent_member'])
@pytest.mark.parametrize('lifetime',[1,179,180])
def test_valid_positive_lifetime_up_to_180_is_accepted(context,role,lifetime):
    p=context.verifier.verify(context.sign(lifetime,role));assert p.role==role

@pytest.mark.parametrize('role',['owner','agent_member'])
@pytest.mark.parametrize('lifetime',[181,3600])
def test_overlong_signed_token_is_rejected_even_with_valid_cached_key(context,role,lifetime):
    context.verifier.verify(context.sign(180,role))
    denied(context,context.sign(lifetime,role))

@pytest.mark.parametrize('lifetime',[0,-1])
def test_equal_or_reversed_exp_iat_is_rejected(context,lifetime):denied(context,context.sign(lifetime))

@pytest.mark.parametrize('field',['iat','exp'])
@pytest.mark.parametrize('kind',['string','float','boolean','null','array','object'])
def test_dates_are_json_integers_without_coercion(context,field,kind):
    if kind=='boolean':
        # Make True a temporally valid 1-second NumericDate so this specifically
        # detects bool-as-int coercion instead of merely testing expired tokens.
        context.clock.epoch=1 if field=='iat' else 0
    value=context.clock.seconds()+(180 if field=='exp' else 0)
    malformed={'string':str(value),'float':float(value),'boolean':True,'null':None,'array':[value],'object':{'value':value}}[kind]
    denied(context,context.sign(changes={field:malformed}))

@pytest.mark.parametrize('field',['iat','exp'])
def test_required_timestamp_cannot_be_omitted(context,field):denied(context,context.sign(remove=[field]))

def test_backdated_issue_time_does_not_bypass_duration_cap(context):
    now=context.clock.seconds()
    denied(context,context.sign(changes={'iat':now-1,'exp':now+180}))
    assert context.verifier.verify(context.sign(changes={'iat':now-1,'exp':now+179})).subject=='owner-1'

def test_expiry_stays_strict_even_for_policy_compliant_token(context):
    t=context.sign(180);context.clock.tick=179.999
    assert context.verifier.verify(t).subject=='owner-1'
    context.clock.tick=180;denied(context,t)

def test_http_rejects_overlong_token_without_mutation_and_audits_message_denial(context):
    import sqlite3
    with TestClient(create_app(context.settings,context.verifier)) as client:
        good={'Authorization':'Bearer '+context.sign(180)}
        room=client.post('/v1/chambers',headers=good,json={'name':'synthetic'}).json()['id']
        bad={'Authorization':'Bearer '+context.sign(181)}
        assert client.get(f'/v1/chambers/{room}/messages',headers=bad).status_code==401
        assert client.post(f'/v1/chambers/{room}/messages',headers=bad,json={'body':'must not persist'}).status_code==401
        assert client.post('/v1/chambers',headers=bad,json={'name':'must not persist'}).status_code==401
    with sqlite3.connect(context.settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM chambers').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE outcome='denied'").fetchone()[0]==2
