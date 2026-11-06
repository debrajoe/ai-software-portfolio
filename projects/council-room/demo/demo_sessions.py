"""Disposable in-process demonstration with synthetic signed tokens and local JWKS.
No listeners, real identities, Keycloak changes, or external connections.
"""
import json
from pathlib import Path
import tempfile
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from council_chamber.app import create_app
from council_chamber.auth import OIDCTokenVerifier
from council_chamber.config import Settings
import sqlite3

def require(value,message):
    if not value:raise RuntimeError(message)

def demonstrate():
    with tempfile.TemporaryDirectory(prefix='olympus-003A-') as directory:
        database=Path(directory)/'synthetic.db'
        settings=Settings(str(database),'https://synthetic.invalid/realm','council-api','https://synthetic.invalid/jwks','synthetic-owner',oidc_role_client='council-api',oidc_authorized_party='council-local-demo')
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='synthetic',use='sig',alg='RS256')
        verifier=OIDCTokenVerifier(settings)
        verifier.jwks.fetch_data=lambda:{'keys':[jwk]}
        def headers(subject,role='agent_member',mfa=False):
            stamp=int(time.time())
            claims={'iss':settings.oidc_issuer,'aud':settings.oidc_audience,'sub':subject,'iat':stamp,'exp':stamp+120,'typ':'Bearer','azp':'council-local-demo','amr':['pwd','webauthn'] if mfa else [],'resource_access':{'council-api':{'roles':[role]}}}
            return {'Authorization':'Bearer '+jwt.encode(claims,key,algorithm='RS256',headers={'kid':'synthetic'})}
        owner=headers('synthetic-owner','owner',True)
        statuses=[]
        def checked(response,status):
            require(response.status_code==status,'Unexpected synthetic demonstration response');statuses.append(status);return response.json()
        with TestClient(create_app(settings,verifier)) as client:
            chamber=checked(client.post('/v1/chambers',headers=owner,json={'name':'Synthetic council'}),201)['id']
            base=f'/v1/chambers/{chamber}'
            checked(client.post(base+'/messages',headers=owner,json={'body':'Pre-session synthetic history'}),201)
            for subject in ['synthetic-jupiter','synthetic-hecate']:
                checked(client.post(base+'/participants',headers=owner,json={'subject':subject,'display_name':subject,'role':'agent_member','kind':'agent'}),201)
            session=checked(client.post(base+'/sessions',headers=owner,json={'name':'Synthetic meeting'}),201)['id']
            url=base+'/sessions/'+session
            for subject in ['synthetic-jupiter','synthetic-hecate']:
                checked(client.post(url+'/messages',headers=headers(subject),json={'body':'Synthetic contribution from '+subject}),201)
            checked(client.post(url+'/close',headers=headers('synthetic-jupiter')),403)
            checked(client.post(url+'/close',headers=headers('synthetic-owner','agent_member')),403)
            checked(client.post(url+'/close',headers=headers('synthetic-owner','owner')),403)
            checked(client.get(url+'/messages',headers=headers('synthetic-outsider')),404)
            checked(client.post(url+'/close',headers=owner),200)
            checked(client.post(url+'/messages',headers=headers('synthetic-jupiter'),json={'body':'Rejected after close'}),409)
            history=checked(client.get(url+'/messages',headers=headers('synthetic-hecate')),200)
            require(len(history['messages'])==2,'Session history mismatch')
            allhistory=checked(client.get(base+'/messages',headers=owner),200)
            require(len(allhistory['messages'])==3 and allhistory['messages'][0]['session_id'] is None,'Legacy history missing')
        db=sqlite3.connect(database)
        try:require(db.execute("SELECT count(*) FROM audit_events WHERE outcome='denied'").fetchone()[0]==5,'Persistent denial audit mismatch')
        finally:db.close()
        return {'result':'PASS','mode':'in-process HTTP ASGI demo; synthetic RSA-signed access tokens and injected local JWKS, not genuine Keycloak authentication','synthetic_members':2,'session_messages':2,'legacy_messages_preserved':1,'persistent_denials':5,'checks_http_statuses':statuses,'production_listeners_started':False,'real_database_accessed':False,'temporary_database_removed_on_return':True}

if __name__=='__main__':print(json.dumps(demonstrate(),indent=2))
