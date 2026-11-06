# OLYMPUS Council Room

OLYMPUS Council Room is a local AI-agent collaboration prototype. It combines a FastAPI service, SQLite persistence, OIDC access-token verification, owner-controlled meeting workflows, a browser interface, and optional AI participant adapters.

## What It Demonstrates

- Identity-bound participants and owner-controlled council spaces
- Short-lived access tokens and MFA-aware owner authorization
- Meeting history, membership checks, and audit events
- Server-side provider configuration with disabled-by-default AI adapters
- Structured role prompts that treat meeting content as untrusted data
- Synthetic tests for authorization, session behavior, token policy, audit denials, and browser workflows

## Repository Layout

- `src/council_chamber/` - application, authentication, persistence, provider adapters, and browser assets
- `tests/` - selected Python and JavaScript tests using synthetic identities and data
- `demo/demo_sessions.py` - in-process demonstration using synthetic signed tokens and temporary storage
- `docs/` - boundaries and recorded verification evidence from the local release package

## Run the Core Tests

Use Python 3.12 or newer in an isolated virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[test]
.\.venv\Scripts\python -m pytest
```

The browser workflow tests also use Node.js for the included `.mjs` test files.

## Important Boundaries

This portfolio copy contains no live credentials, private databases, runtime state, or personal conversation history. It is a local prototype and is not presented as production-ready, publicly deployable, or fully security-verified.

## My Contributions

I defined the collaboration roles and meeting workflows, established requirements for authentication, identity binding, access controls, and audit logging, tested owner sign-in and AI participant behavior, reviewed test evidence, documented limitations, and directed iterative implementation with AI-assisted engineering tools.

