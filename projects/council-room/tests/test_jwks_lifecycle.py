from __future__ import annotations
from datetime import datetime,timezone
from types import SimpleNamespace
from pathlib import Path
import json,sys,time
import jwt,pytest
import jwt.api_jwk,jwt.api_jwt,jwt.jwk_set_cache
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from council_chamber.auth import OIDCTokenVerifier
from council_chamber.config import Settings
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local'))
from jwks_probe import JWKSProbe

class Clock:
    tick=0.0
    def __init__(self):self.epoch=int(time.time())
    def monotonic(self):return self.tick
    def seconds(self):return self.epoch+int(self.tick)

@pytest.fixture
def clock(monkeypatch):
    clock=Clock()
    # Replace the module references, not the global time module used by threads.
    monotonic=SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(jwt.api_jwk,'time',monotonic)
    monkeypatch.setattr(jwt.jwk_set_cache,'time',monotonic)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromtimestamp(clock.epoch+clock.tick,tz=tz)
    monkeypatch.setattr(jwt.api_jwt,'datetime',FrozenDateTime)
    return clock

@pytest.fixture
def keys():return [rsa.generate_private_key(public_exponent=65537,key_size=2048) for _ in range(2)]
def public(key,kid):
    value=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));value.update(kid=kid,alg='RS256',use='sig');return value
def token(key,kid,clock,ttl=180,**changes):
    claims={'iss':'http://localhost/realms/proof','aud':'council-api','sub':'owner-1','iat':clock.seconds(),'exp':clock.seconds()+ttl,'typ':'Bearer','azp':'council-local-demo','resource_access':{'council-api':{'roles':['owner']}},'amr':['pwd','webauthn']}
    claims.update(changes);return jwt.encode(claims,key,algorithm='RS256',headers={'kid':kid})
def verifier(url,tmp_path):
    return OIDCTokenVerifier(Settings(str(tmp_path/'unused.db'),'http://localhost/realms/proof','council-api',url,'owner-1',oidc_role_client='council-api',oidc_authorized_party='council-local-demo'))
def denied(v,t):
    with pytest.raises(HTTPException) as e:v.verify(t)
    assert e.value.status_code==401
    assert e.value.detail=='Invalid or expired access token'
    assert e.value.headers=={'WWW-Authenticate':'Bearer'}


def test_default_cache_policy_is_bounded_and_no_per_key_lru(tmp_path):
    v=verifier('http://127.0.0.1:1/certs',tmp_path)
    assert v.jwks.jwk_set_cache.lifespan==300
    assert v.jwks.timeout==30
    assert not hasattr(v.jwks.get_signing_key,'cache_info')

def test_first_fetch_and_warm_cache_reuse(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);t=token(keys[0],'old',clock)
        assert v.verify(t).mfa_authenticated;assert provider.requests==1
        assert v.verify(t).subject=='owner-1';assert provider.requests==1

def test_new_kid_forces_refresh_before_cache_expiry(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);old=token(keys[0],'old',clock);v.verify(old)
        provider.payload={'keys':[public(keys[0],'old'),public(keys[1],'new')]}
        assert v.verify(token(keys[1],'new',clock)).subject=='owner-1'
        assert provider.requests==2
        v.verify(old);assert provider.requests==2

def test_removed_key_warm_cache_accepts_but_cold_cache_rejects(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        warm=verifier(provider.url,tmp_path);old=token(keys[0],'old',clock);warm.verify(old)
        provider.payload={'keys':[public(keys[1],'new')]}
        assert warm.verify(old).subject=='owner-1'
        denied(verifier(provider.url,tmp_path),old)

def test_ttl_refresh_removes_old_key_and_new_tokens_still_work(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);v.verify(token(keys[0],'old',clock))
        provider.payload={'keys':[public(keys[1],'new')]}
        clock.tick=300.0
        # A newly minted synthetic old-key token illustrates compromised-key scope.
        assert v.verify(token(keys[0],'old',clock)).subject=='owner-1';assert provider.requests==1
        clock.tick=300.001
        denied(v,token(keys[0],'old',clock))
        assert provider.requests==3 # TTL fetch followed by unknown-kid refresh.
        assert v.verify(token(keys[1],'new',clock)).subject=='owner-1'

def test_new_kid_refresh_evicts_removed_old_key_immediately(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);old=token(keys[0],'old',clock);v.verify(old)
        provider.payload={'keys':[public(keys[1],'new')]}
        assert v.verify(token(keys[1],'new',clock)).subject=='owner-1'
        denied(v,old)

def test_reusing_kid_does_not_refresh_on_signature_failure(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'same')]}) as provider:
        v=verifier(provider.url,tmp_path);v.verify(token(keys[0],'same',clock))
        provider.payload={'keys':[public(keys[1],'same')]}
        denied(v,token(keys[1],'same',clock));assert provider.requests==1
        clock.tick=301
        assert v.verify(token(keys[1],'same',clock)).subject=='owner-1';assert provider.requests==2

@pytest.mark.parametrize('mode',['unavailable','malformed','timeout'])
def test_cold_outage_fails_closed_and_recovers(keys,clock,tmp_path,mode):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        provider.mode=mode;v=verifier(provider.url,tmp_path)
        if mode=='timeout':v.jwks.timeout=.05 # Accelerated mechanism test; runtime default remains 30s.
        t=token(keys[0],'old',clock);denied(v,t)
        assert v.jwks.jwk_set_cache.get() is None
        provider.mode='ok'
        assert v.verify(t).subject=='owner-1'

@pytest.mark.parametrize('mode',['unavailable','malformed','timeout'])
def test_warm_cache_works_during_outage_then_expiry_fails_closed(keys,clock,tmp_path,mode):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);v.verify(token(keys[0],'old',clock))
        provider.mode=mode
        if mode=='timeout':v.jwks.timeout=.05
        clock.tick=100;assert v.verify(token(keys[0],'old',clock)).subject=='owner-1';assert provider.requests==1
        clock.tick=301;denied(v,token(keys[0],'old',clock))
        provider.mode='ok';assert v.verify(token(keys[0],'old',clock)).subject=='owner-1'

def test_connection_refusal_cold_fails_warm_cache_still_works(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        url=provider.url;v=verifier(url,tmp_path);t=token(keys[0],'old',clock);v.verify(t);provider.close()
        assert v.verify(t).subject=='owner-1';denied(verifier(url,tmp_path),t)

def test_failed_unknown_kid_refresh_clears_entire_warm_cache(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);old=token(keys[0],'old',clock);v.verify(old)
        provider.mode='unavailable';denied(v,token(keys[1],'unknown',clock))
        assert v.jwks.jwk_set_cache.get() is None
        denied(v,old) # Not stale-cache fallback: unknown-kid failure affects availability.
        provider.mode='ok';assert v.verify(old).subject=='owner-1'

def test_unknown_kid_request_counts_are_not_throttled(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);unknown=token(keys[1],'unknown',clock)
        denied(v,unknown);assert provider.requests==2
        denied(v,unknown);assert provider.requests==3

@pytest.mark.parametrize('payload',[{'keys':[]},{'unexpected':'schema'},{'keys':[{'kty':'RSA','kid':'broken'}]}, {'keys':[{**{'kty':'oct','k':'eHh4'},'kid':'old','use':'enc'}]}])
def test_invalid_keysets_never_fall_back_to_an_unverified_token(keys,clock,tmp_path,payload):
    with JWKSProbe(payload) as provider:denied(verifier(provider.url,tmp_path),token(keys[0],'old',clock))

def test_access_token_expires_exactly_at_exp_despite_cached_key(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);t=token(keys[0],'old',clock,ttl=180);v.verify(t)
        clock.tick=179.999;assert v.verify(t).subject=='owner-1'
        clock.tick=180;denied(v,t);assert provider.requests==1

def test_signed_lifetime_above_180_seconds_is_now_rejected(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);t=token(keys[0],'old',clock,ttl=3600)
        denied(v,t)
        clock.tick=181;denied(v,t)

def test_live_key_remains_accepted_after_ttl_when_still_published(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old'),public(keys[1],'new')]}) as provider:
        v=verifier(provider.url,tmp_path);v.verify(token(keys[0],'old',clock))
        clock.tick=301;assert v.verify(token(keys[0],'old',clock)).subject=='owner-1';assert provider.requests==2

def test_signed_nbf_is_enforced_even_though_remote_realm_not_before_is_not_polled(keys,clock,tmp_path):
    with JWKSProbe({'keys':[public(keys[0],'old')]}) as provider:
        v=verifier(provider.url,tmp_path);t=token(keys[0],'old',clock,nbf=clock.seconds()+10)
        denied(v,t)
        clock.tick=10
        assert v.verify(t).subject=='owner-1'
