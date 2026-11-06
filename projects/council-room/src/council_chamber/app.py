from __future__ import annotations

from typing import Annotated
from uuid import UUID
import hashlib
import logging
import sqlite3

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import OIDCTokenVerifier, Principal, TokenVerifier
from .config import Settings
from .database import Database
from .service import CouncilService
from .hecate import HecateCouncil, Gemini
from .mercury import MercuryCouncil, mercury_provider
from .jupiter import JupiterCouncil, CodexJupiter
from .daedalus import DaedalusCouncil, CodexDaedalus
from .activity import DesktopActivity

logger = logging.getLogger(__name__)


class ChamberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ParticipantCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern="^(owner|agent_member)$")
    kind: str = Field(pattern="^(human|agent)$")


class HecateInvitation(BaseModel):
    request_id: UUID


class MessageCreate(BaseModel):
    # Session association comes only from the authorized route, never an ignored body ID.
    session_id: None = None
    body: str
    client_message_id: str | None = Field(default=None, max_length=100)


def create_app(settings: Settings, verifier: TokenVerifier | None = None, hecate_provider=None, mercury_provider_instance=None, jupiter_provider_instance=None, daedalus_provider_instance=None, daedalus_activity=None) -> FastAPI:
    database = Database(settings.database_path)
    service = CouncilService(database, settings.owner_subject, settings.max_message_chars)
    hecate = HecateCouncil(service, hecate_provider)
    mercury = MercuryCouncil(service, mercury_provider_instance)
    jupiter = JupiterCouncil(service, jupiter_provider_instance)
    daedalus = DaedalusCouncil(service, daedalus_provider_instance, activity=daedalus_activity)
    token_verifier = verifier or OIDCTokenVerifier(settings)
    app = FastAPI(title="OLYMPUS Council Room", version="0.3.13")

    @app.exception_handler(StarletteHTTPException)
    async def audited_http_exception(request: Request, exc: StarletteHTTPException):
        route = request.scope.get("route")
        route_path = getattr(route, "path", "") or ""
        is_session = route_path.startswith("/v1/chambers/{chamber_id}/sessions")
        if is_session and exc.status_code in {401, 403, 404, 409}:
            principal = getattr(request.state, "audit_principal", None)
            actor = ("sha256:" + hashlib.sha256(principal.subject.encode()).hexdigest()
                     if isinstance(principal, Principal) else "unauthenticated")
            resource = "sha256:" + hashlib.sha256(request.path_params["chamber_id"].encode()).hexdigest()
            action = ("message.list" if request.method == "GET" else "message.create") if route_path.endswith("/messages") else ("session.close" if route_path.endswith("/close") else ("session.read" if route_path.endswith("/{session_id}") else "session.list") if request.method == "GET" else "session.open")
            if route_path.endswith(("/hecate", "/mercury", "/jupiter", "/daedalus")):
                action = route_path.rsplit("/", 1)[1] + ".request"
            try:
                await run_in_threadpool(service.audit_session_denial, actor, resource, action, exc.status_code)
            except sqlite3.Error:
                logger.error("Denied-message audit persistence failed")
        if (getattr(route, "path", None) == "/v1/chambers/{chamber_id}/messages"
                and request.method in {"GET", "POST"}
                and exc.status_code in {401, 403, 404}):
            # Exception handlers run after the service transaction has unwound.
            # Persist separately so rollback cannot erase the denial event.
            principal = getattr(request.state, "audit_principal", None)
            actor = ("sha256:" + hashlib.sha256(principal.subject.encode()).hexdigest()
                     if isinstance(principal, Principal) else "unauthenticated")
            resource = "sha256:" + hashlib.sha256(
                request.path_params["chamber_id"].encode()
            ).hexdigest()
            try:
                await run_in_threadpool(
                    service.audit_message_denial, actor, resource,
                    request.method == "GET", exc.status_code,
                )
            except sqlite3.Error:
                # A storage outage cannot grant access or disclose chamber existence.
                # Do not log exceptions, request headers, paths, bodies, or claims.
                logger.error("Denied-message audit persistence failed")
        return await http_exception_handler(request, exc)

    def current_principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Bearer access token required")
        principal = token_verifier.verify(authorization[7:])
        request.state.audit_principal = principal
        return principal

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/v1/me")
    def identity(principal: Principal = Depends(current_principal)):
        return {"display_name": principal.display_name, "role": principal.role,
                "can_manage_sessions": principal.subject == settings.owner_subject
                    and principal.role == "owner" and principal.mfa_authenticated}

    @app.get("/v1/chambers")
    def chambers(principal: Principal = Depends(current_principal)):
        return service.list_chambers(principal)

    @app.post("/v1/chambers", status_code=201)
    def create_chamber(payload: ChamberCreate, principal: Principal = Depends(current_principal)):
        return service.create_chamber(principal, payload.name)

    @app.post("/v1/chambers/{chamber_id}/participants", status_code=201)
    def add_participant(chamber_id: str, payload: ParticipantCreate,
                        principal: Principal = Depends(current_principal)):
        return service.add_participant(
            principal, chamber_id, payload.subject, payload.display_name, payload.role, payload.kind
        )

    @app.post("/v1/chambers/{chamber_id}/messages", status_code=201)
    def send_message(chamber_id: str, payload: MessageCreate,
                     principal: Principal = Depends(current_principal)):
        return service.send_message(principal, chamber_id, payload.body, payload.client_message_id)

    @app.get("/v1/chambers/{chamber_id}/messages")
    def list_messages(chamber_id: str, principal: Principal = Depends(current_principal),
                      after: int = Query(default=0, ge=0, le=2**63 - 1),
                      limit: int = Query(default=100, ge=1, le=500)):
        return service.list_messages(principal, chamber_id, after, limit)

    @app.post("/v1/chambers/{chamber_id}/sessions", status_code=201)
    def open_session(chamber_id: str, payload: ChamberCreate, principal: Principal = Depends(current_principal)):
        return service.open_session(principal, chamber_id, payload.name)

    @app.get("/v1/chambers/{chamber_id}/sessions")
    def list_sessions(chamber_id: str, principal: Principal = Depends(current_principal),
                      offset: int = Query(default=0, ge=0, le=2**63-1),
                      limit: int = Query(default=100, ge=1, le=500)):
        return service.list_sessions(principal, chamber_id, offset, limit)

    @app.get("/v1/chambers/{chamber_id}/sessions/{session_id}")
    def session_details(chamber_id: str, session_id: str, principal: Principal = Depends(current_principal)):
        return service.get_session(principal, chamber_id, session_id)

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/close")
    def close_session(chamber_id: str, session_id: str, principal: Principal = Depends(current_principal)):
        return service.close_session(principal, chamber_id, session_id)

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/messages", status_code=201)
    def session_send(chamber_id: str, session_id: str, payload: MessageCreate,
                     principal: Principal = Depends(current_principal)):
        return service.send_message(principal, chamber_id, payload.body, payload.client_message_id, session_id)

    @app.get("/v1/chambers/{chamber_id}/sessions/{session_id}/messages")
    def session_history(chamber_id: str, session_id: str, principal: Principal = Depends(current_principal),
                        after: int = Query(default=0, ge=0, le=2**63-1),
                        limit: int = Query(default=100, ge=1, le=500)):
        return service.list_messages(principal, chamber_id, after, limit, session_id)

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/hecate")
    def invite_hecate(chamber_id: str, session_id: str, payload: HecateInvitation,
                      principal: Principal = Depends(current_principal)):
        return hecate.ask(principal, chamber_id, session_id, str(payload.request_id))

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/mercury")
    def invite_mercury(chamber_id: str, session_id: str, payload: HecateInvitation,
                       principal: Principal = Depends(current_principal)):
        return mercury.ask(principal, chamber_id, session_id, str(payload.request_id))

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/jupiter")
    def invite_jupiter(chamber_id: str, session_id: str, payload: HecateInvitation,
                       principal: Principal = Depends(current_principal)):
        return jupiter.ask(principal, chamber_id, session_id, str(payload.request_id))

    @app.post("/v1/chambers/{chamber_id}/sessions/{session_id}/daedalus")
    def invite_daedalus(chamber_id: str, session_id: str, payload: HecateInvitation,
                       principal: Principal = Depends(current_principal)):
        return daedalus.ask(principal, chamber_id, session_id, str(payload.request_id))

    return app


def runtime_app() -> FastAPI:
    import os
    key_file = os.environ.get("OLYMPUS_GEMINI_KEY_FILE")
    provider = Gemini(key_file) if key_file else None
    codex_binary=os.environ.get("OLYMPUS_CODEX_BINARY")
    codex_workdir=os.environ.get("OLYMPUS_CODEX_WORKDIR")
    activity_path=os.environ.get("OLYMPUS_DAE_ACTIVITY_FILE")
    return create_app(Settings.from_env(), hecate_provider=provider,
                      daedalus_activity=DesktopActivity(activity_path) if activity_path else None,
                      daedalus_provider_instance=CodexDaedalus(codex_binary,codex_workdir) if codex_binary and codex_workdir else None,
                      jupiter_provider_instance=CodexJupiter(codex_binary,codex_workdir) if codex_binary and codex_workdir else None,
                      mercury_provider_instance=mercury_provider(key_file) if key_file else None)

