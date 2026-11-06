from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_url: str
    owner_subject: str
    owner_mfa_acr: str | None = None
    max_message_chars: int = 20_000
    oidc_role_client: str | None = None
    oidc_authorized_party: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        required = {
            "oidc_issuer": os.getenv("OLYMPUS_OIDC_ISSUER"),
            "oidc_audience": os.getenv("OLYMPUS_OIDC_AUDIENCE"),
            "oidc_jwks_url": os.getenv("OLYMPUS_OIDC_JWKS_URL"),
            "owner_subject": os.getenv("OLYMPUS_OWNER_SUBJECT"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            names = ", ".join(f"OLYMPUS_{name.upper()}" for name in missing)
            raise RuntimeError(
                "Council Room requires a configured OIDC/MFA provider; missing " + names
            )
        return cls(
            database_path=os.getenv("OLYMPUS_DATABASE_PATH", "council_chamber.db"),
            owner_mfa_acr=os.getenv("OLYMPUS_OWNER_MFA_ACR"),
            oidc_role_client=os.getenv("OLYMPUS_OIDC_ROLE_CLIENT"),
            oidc_authorized_party=os.getenv("OLYMPUS_OIDC_AUTHORIZED_PARTY"),
            **required,
        )

