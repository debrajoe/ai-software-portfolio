"""Loopback conference-room BFF. No browser tokens or persistent login storage."""
from dataclasses import dataclass
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, PlainTextResponse

ORIGIN = 'http://localhost:8765'
STATIC = Path(__file__).with_name('room_static')
COOKIE = 'olympus_hecate_room'
LOGIN_COOKIE = 'olympus_hecate_login'

def create_room(issuer: str, backend='http://127.0.0.1:18000', http=None, clock=time.time, origin=ORIGIN):
    if origin not in ('http://localhost:8765','http://localhost:8766'):
        raise RuntimeError('Unsupported localhost screen origin')
    for address in (issuer, backend):
        parsed=urlparse(address)
        if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError('Room services must use plain HTTP loopback URLs')
    http=http or httpx.Client(trust_env=False, timeout=55, follow_redirects=False)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    sessions={};pending={}
    def prune():
        for store in (sessions,pending):
            for key in list(store):
                if store[key]['expires']<=clock():del store[key]
    def error(status,message):return JSONResponse({'detail':message},status_code=status)
    def clear(response):
        response.delete_cookie(COOKIE,path='/',httponly=True,samesite='lax')
        return response
    def session(request):
        prune();return sessions.get(request.cookies.get(COOKIE,''))
    @app.middleware('http')
    async def protections(request,call_next):
        if request.headers.get('host')!=urlparse(origin).netloc:return error(400,'Open '+origin)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response
    @app.get('/')
    def index():return FileResponse(STATIC/'index.html')
    @app.get('/room.js')
    def javascript():return FileResponse(STATIC/'room.js',media_type='text/javascript')
    @app.get('/turn-workflow.mjs')
    def workflow():return FileResponse(STATIC/'turn-workflow.mjs',media_type='text/javascript')
    @app.get('/room.css')
    def stylesheet():return FileResponse(STATIC/'room.css',media_type='text/css')
    @app.get('/signin')
    def signin():
        prune()
        if len(pending)>=128:return error(429,'Too many sign-in attempts; try again shortly')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        pending[state]={'verifier':verifier,'expires':clock()+300}
        response=RedirectResponse(issuer+'/protocol/openid-connect/auth?'+urlencode({'client_id':'council-local-demo','redirect_uri':origin+'/callback','response_type':'code','scope':'openid','state':state,'code_challenge':challenge,'code_challenge_method':'S256','prompt':'login'}),status_code=303)
        response.set_cookie(LOGIN_COOKIE,state,max_age=300,httponly=True,samesite='lax',path='/')
        return response
    @app.get('/callback')
    def callback(request:Request):
        prune();state=request.query_params.get('state','');cookie=request.cookies.get(LOGIN_COOKIE,'')
        if not state or not secrets.compare_digest(state,cookie) or state not in pending:
            return error(400,'Sign-in could not be verified. Start again.')
        flow=pending.pop(state)
        code=request.query_params.get('code')
        if not code:return error(400,'Sign-in was cancelled or failed. Start again.')
        try:
            response=http.post(issuer+'/protocol/openid-connect/token',data={'grant_type':'authorization_code','client_id':'council-local-demo','redirect_uri':origin+'/callback','code':code,'code_verifier':flow['verifier']})
            response.raise_for_status();data=response.json();token=data['access_token']
            if not isinstance(token,str):return error(401,'Sign-in was not accepted')
            identity=http.get(backend+'/v1/me',headers={'Authorization':'Bearer '+token})
            identity.raise_for_status()
            # The backend is the authority for signature, timestamps, owner role and MFA.
            # Use the verified token's exp only to shorten the local browser session.
            payload=token.split('.')[1];claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
            expiry=claims['exp']
            if type(expiry) is not int or expiry<=clock():return error(401,'Sign-in expired; start again')
        except (httpx.HTTPError,KeyError,ValueError,TypeError,IndexError):
            return error(401,'Sign-in was not accepted. Use your password and registered passkey.')
        prune()
        if len(sessions)>=512:return error(429,'Room is busy; try again shortly')
        old=request.cookies.get(COOKIE,'');sessions.pop(old,None)
        sid=secrets.token_urlsafe(32)
        sessions[sid]={'token':token,'csrf':secrets.token_urlsafe(32),'expires':min(expiry,clock()+180)}
        response=RedirectResponse('/',status_code=303)
        response.delete_cookie(LOGIN_COOKIE,path='/',httponly=True,samesite='lax')
        response.set_cookie(COOKIE,sid,max_age=max(1,int(sessions[sid]['expires']-clock())),httponly=True,samesite='lax',path='/')
        return response
    @app.get('/api/me')
    def me(request:Request):
        active=session(request)
        if not active:return clear(error(401,'Sign in to enter the room'))
        try:
            response=http.get(backend+'/v1/me',headers={'Authorization':'Bearer '+active['token']})
            if response.status_code!=200:return error(response.status_code,'Sign-in is no longer valid')
            return {**response.json(),'csrf':active['csrf']}
        except httpx.HTTPError:return error(502,'The local meeting service is unavailable')
    def mutation_allowed(request,active):
        return request.headers.get('origin')==origin and secrets.compare_digest(request.headers.get('x-csrf-token',''),active['csrf'])
    @app.post('/signout')
    def signout(request:Request):
        active=session(request)
        if not active:return clear(JSONResponse({'signed_out':True}))
        if not mutation_allowed(request,active):return error(403,'Request verification failed')
        sessions.pop(request.cookies.get(COOKIE,''),None)
        return clear(JSONResponse({'signed_out':True}))
    identifier=r'[A-Za-z0-9-]{1,80}'
    paths={
        'GET':[r'v1/chambers',rf'v1/chambers/{identifier}/sessions',rf'v1/chambers/{identifier}/sessions/{identifier}/messages',rf'v1/chambers/{identifier}/sessions/{identifier}'],
        'POST':[rf'v1/chambers/{identifier}/sessions',rf'v1/chambers/{identifier}/sessions/{identifier}/close',rf'v1/chambers/{identifier}/sessions/{identifier}/messages',rf'v1/chambers/{identifier}/sessions/{identifier}/hecate',rf'v1/chambers/{identifier}/sessions/{identifier}/mercury',rf'v1/chambers/{identifier}/sessions/{identifier}/jupiter',rf'v1/chambers/{identifier}/sessions/{identifier}/daedalus']}
    @app.api_route('/api/{path:path}',methods=['GET','POST'])
    async def proxy(path:str,request:Request):
        active=session(request)
        if not active:return clear(error(401,'Your sign-in expired. Saved meeting history remains available. Sign in again to continue.'))
        if not any(re.fullmatch(pattern,path) for pattern in paths[request.method]):return error(404,'Route not available')
        if request.method=='POST' and not mutation_allowed(request,active):return error(403,'Request verification failed')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>100_000:return error(413,'Message is too large')
        try:
            response=http.request(request.method,backend+'/'+path,params=list(request.query_params.multi_items()),content=bytes(body) if request.method=='POST' else None,headers={'Authorization':'Bearer '+active['token'],'Content-Type':'application/json'})
            data=response.json()
            if response.status_code==401:
                sessions.pop(request.cookies.get(COOKIE,''),None)
                return clear(error(401,'Your sign-in expired. Saved meeting history remains available. Sign in again to continue.'))
            return JSONResponse(data,status_code=response.status_code)
        except (httpx.HTTPError,ValueError):return error(502,'The local meeting service is unavailable')
    return app

def runtime_room():
    import os
    return create_room(os.environ['OLYMPUS_OIDC_ISSUER'],backend=os.environ.get('OLYMPUS_ROOM_BACKEND','http://127.0.0.1:18001'),origin=os.environ.get('OLYMPUS_ROOM_ORIGIN','http://localhost:8766'))
