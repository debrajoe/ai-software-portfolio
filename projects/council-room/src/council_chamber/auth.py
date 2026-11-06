from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from .config import Settings

MAX_ACCESS_TOKEN_LIFETIME_SECONDS = 180


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    role: str
    mfa_authenticated: bool


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class OIDCTokenVerifier:
    """Verifies externally issued OIDC access tokens; it never issues credentials."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = PyJWKClient(settings.oidc_jwks_url)

    def verify(self, token: str) -> Principal:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            # Keycloak issues integer NumericDates. Do not silently coerce strings,
            # floats, or bools before enforcing this fixed local lifetime policy.
            issued_at, expires_at = claims["iat"], claims["exp"]
            if type(issued_at) is not int or type(expires_at) is not int:
                raise jwt.InvalidTokenError("Access-token timestamps must be integers")
            if not 0 < expires_at - issued_at <= MAX_ACCESS_TOKEN_LIFETIME_SECONDS:
                raise jwt.InvalidTokenError("Access-token lifetime is outside policy")
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        # Keycloak marks access tokens as Bearer; ID/refresh tokens are not API credentials.
        if claims.get("typ") != "Bearer":
            raise HTTPException(status_code=401, detail="Bearer access token required")
        if self.settings.oidc_authorized_party and claims.get("azp") != self.settings.oidc_authorized_party:
            raise HTTPException(status_code=403, detail="Unrecognized authorized client")
        if not isinstance(claims["sub"], str) or not claims["sub"].strip():
            raise HTTPException(status_code=401, detail="Invalid token subject")

        role = claims.get("role")
        if self.settings.oidc_role_client:
            resources = claims.get("resource_access")
            resource = resources.get(self.settings.oidc_role_client) if isinstance(resources, dict) else None
            roles = resource.get("roles") if isinstance(resource, dict) else None
            # Do not choose an elevated role from a conflicting collection or trust a fallback claim.
            if not isinstance(roles, list) or len(roles) != 1 or not isinstance(roles[0], str):
                raise HTTPException(status_code=403, detail="Exactly one Council role required")
            role = roles[0]
        if role not in {"owner", "agent_member"}:
            raise HTTPException(status_code=403, detail="Unrecognized principal role")

        amr = claims.get("amr")
        # This integration requires successful password AND WebAuthn executions.
        # ACR=1 is Keycloak's normal login context, not proof of MFA. Never hard-code
        # an MFA claim with a protocol mapper. Malformed claims fail closed.
        mfa = (isinstance(amr, list) and all(isinstance(item, str) for item in amr)
               and {"pwd", "webauthn"}.issubset(amr))
        if self.settings.owner_mfa_acr:
            mfa = mfa and claims.get("acr") == self.settings.owner_mfa_acr
        principal = Principal(
            subject=str(claims["sub"]),
            display_name=str(claims.get("name") or claims["sub"]),
            role=role,
            mfa_authenticated=mfa,
        )
        if principal.subject == self.settings.owner_subject and principal.role != "owner":
            raise HTTPException(status_code=403, detail="Owner identity requires owner role and MFA")
        if principal.role == "owner":
            if principal.subject != self.settings.owner_subject:
                raise HTTPException(status_code=403, detail="Owner identity is not authorized")
            if not principal.mfa_authenticated:
                raise HTTPException(status_code=403, detail="Owner MFA is required")
        return principal

