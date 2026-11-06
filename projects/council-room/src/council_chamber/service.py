from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from .auth import Principal
from .database import Database


def now() -> str:
    return datetime.now(UTC).isoformat()


class CouncilService:
    def __init__(self, database: Database, owner_subject: str, max_message_chars: int = 20_000):
        self.database = database
        self.owner_subject = owner_subject
        self.max_message_chars = max_message_chars

    @staticmethod
    def _audit(connection, actor_subject, action, resource_type, resource_id, outcome, context=None):
        connection.execute(
            "INSERT INTO audit_events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()), actor_subject, action, resource_type, resource_id,
                outcome, json.dumps(context or {}, separators=(",", ":")), now(),
            ),
        )

    def _require_owner(self, principal: Principal) -> None:
        if (principal.subject != self.owner_subject or principal.role != "owner"
                or not principal.mfa_authenticated):
            raise HTTPException(status_code=403, detail="Owner with MFA required")

    def audit_message_denial(self, actor: str, resource: str, reading: bool, status_code: int) -> None:
        """Persist bounded, pseudonymous metadata after the failed operation rolled back."""
        with self.database.transaction() as connection:
            self._audit(
                connection, actor,
                "message.list" if reading else "message.create", "chamber", resource, "denied",
                {"status_code": status_code, "reason": "access_denied"},
            )

    @staticmethod
    def _upsert_user(connection, principal: Principal) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM users WHERE subject = ?", (principal.subject,)).fetchone()
        if row:
            if not row["active"] or row["role"] != principal.role:
                raise HTTPException(status_code=403, detail="Principal account is not authorized")
            connection.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (principal.display_name, row["id"]),
            )
            return connection.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        user_id = str(uuid4())
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, 1, ?)",
            (user_id, principal.subject, principal.display_name, principal.role, now()),
        )
        return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def _membership(self, connection, chamber_id: str, principal: Principal) -> sqlite3.Row:
        if principal.subject == self.owner_subject and (
            principal.role != "owner" or not principal.mfa_authenticated
        ):
            raise HTTPException(status_code=403, detail="Owner identity requires owner role and MFA")
        row = connection.execute(
            """SELECT p.*, u.role AS stored_role FROM participants p JOIN users u ON u.id = p.user_id
               WHERE p.chamber_id = ? AND u.subject = ? AND u.active = 1""",
            (chamber_id, principal.subject),
        ).fetchone()
        if not row or row["stored_role"] != principal.role:
            raise HTTPException(status_code=404, detail="Chamber not found")
        return row

    def list_chambers(self, principal: Principal) -> dict:
        with self.database.transaction() as connection:
            if principal.subject == self.owner_subject and (principal.role != "owner" or not principal.mfa_authenticated):
                raise HTTPException(status_code=403, detail="Owner identity requires owner role and MFA")
            rows = connection.execute("""SELECT c.* FROM chambers c
                JOIN participants p ON p.chamber_id=c.id JOIN users u ON u.id=p.user_id
                WHERE u.subject=? AND u.role=? AND u.active=1 ORDER BY c.created_at, c.id""",
                (principal.subject, principal.role)).fetchall()
            self._audit(connection, principal.subject, "chamber.list", "account", principal.subject, "allowed")
            return {"chambers": [dict(row) for row in rows]}

    def create_chamber(self, principal: Principal, name: str) -> dict:
        self._require_owner(principal)
        chamber_id, participant_id, timestamp = str(uuid4()), str(uuid4()), now()
        with self.database.transaction() as connection:
            user = self._upsert_user(connection, principal)
            connection.execute(
                "INSERT INTO chambers VALUES (?, ?, ?, ?)",
                (chamber_id, name, user["id"], timestamp),
            )
            connection.execute(
                "INSERT INTO participants VALUES (?, ?, ?, ?, 'human', ?)",
                (participant_id, chamber_id, user["id"], principal.display_name, timestamp),
            )
            self._audit(connection, principal.subject, "chamber.create", "chamber", chamber_id, "allowed")
        return {"id": chamber_id, "name": name, "created_at": timestamp}

    def add_participant(self, principal: Principal, chamber_id: str, subject: str,
                        display_name: str, role: str, kind: str) -> dict:
        self._require_owner(principal)
        timestamp, participant_id = now(), str(uuid4())
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            invited = Principal(subject, display_name, role, False)
            user = self._upsert_user(connection, invited)
            try:
                connection.execute(
                    "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?)",
                    (participant_id, chamber_id, user["id"], display_name, kind, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Participant already exists") from exc
            self._audit(
                connection, principal.subject, "participant.add", "chamber", chamber_id,
                "allowed", {"participant_subject": subject, "participant_kind": kind},
            )
        return {"id": participant_id, "subject": subject, "display_name": display_name, "kind": kind}

    def audit_session_denial(self, actor, resource, action, status_code):
        with self.database.transaction() as connection:
            self._audit(connection, actor, action, "chamber", resource, "denied",
                        {"status_code": status_code, "reason": "access_denied"})

    @staticmethod
    def _session(connection, chamber_id, session_id):
        row = connection.execute("SELECT * FROM sessions WHERE id=? AND chamber_id=?",
                                 (session_id, chamber_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return row

    def open_session(self, principal, chamber_id, name):
        self._require_owner(principal)
        session_id, timestamp = str(uuid4()), now()
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            if connection.execute("SELECT 1 FROM sessions WHERE chamber_id=? AND closed_at IS NULL", (chamber_id,)).fetchone():
                raise HTTPException(status_code=409, detail="Chamber already has an open session")
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, NULL)",
                               (session_id, chamber_id, name, timestamp))
            self._audit(connection, principal.subject, "session.open", "session", session_id, "allowed")
            return dict(self._session(connection, chamber_id, session_id))

    def get_session(self, principal, chamber_id, session_id):
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            row = self._session(connection, chamber_id, session_id)
            self._audit(connection, principal.subject, "session.read", "session", session_id, "allowed")
            return dict(row)

    def close_session(self, principal, chamber_id, session_id):
        self._require_owner(principal)
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            session = self._session(connection, chamber_id, session_id)
            if session["closed_at"] is not None:
                raise HTTPException(status_code=409, detail="Session is closed")
            connection.execute("UPDATE sessions SET closed_at=? WHERE id=?", (now(), session_id))
            self._audit(connection, principal.subject, "session.close", "session", session_id, "allowed")
            return dict(self._session(connection, chamber_id, session_id))

    def list_sessions(self, principal, chamber_id, after, limit):
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            rows = connection.execute("SELECT * FROM sessions WHERE chamber_id=? ORDER BY opened_at, id LIMIT ? OFFSET ?",
                                      (chamber_id, limit, after)).fetchall()
            self._audit(connection, principal.subject, "session.list", "chamber", chamber_id, "allowed")
            return {"sessions": [dict(row) for row in rows], "next_offset": after + len(rows)}

    def send_message(self, principal: Principal, chamber_id: str, body: str,
                     client_message_id: str | None, session_id: str | None = None) -> dict:
        body = body.strip()
        if not body:
            raise HTTPException(status_code=422, detail="Message body cannot be empty")
        if len(body) > self.max_message_chars:
            raise HTTPException(status_code=413, detail="Message body is too large")
        with self.database.transaction() as connection:
            participant = self._membership(connection, chamber_id, principal)
            if session_id is not None:
                session = self._session(connection, chamber_id, session_id)
                if session["closed_at"] is not None:
                    raise HTTPException(status_code=409, detail="Session is closed")
            public_id, timestamp = str(uuid4()), now()
            try:
                cursor = connection.execute(
                    """INSERT INTO messages(public_id, chamber_id, participant_id, body, client_message_id, created_at, session_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (public_id, chamber_id, participant["id"], body, client_message_id, timestamp, session_id),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Duplicate client_message_id") from exc
            message_id = cursor.lastrowid
            self._audit(connection, principal.subject, "message.create", "message", public_id, "allowed",
                        {"chamber_id": chamber_id})
        return {
            "session_id": session_id,
            "id": public_id,
            "sequence": message_id,
            "participant": {"id": participant["id"], "display_name": participant["display_name"], "kind": participant["kind"]},
            "body": body,
            "created_at": timestamp,
        }

    def list_messages(self, principal: Principal, chamber_id: str, after: int, limit: int, session_id: str | None = None) -> dict:
        with self.database.transaction() as connection:
            self._membership(connection, chamber_id, principal)
            if session_id is not None:
                self._session(connection, chamber_id, session_id)
            rows = connection.execute(
                """SELECT m.session_id, m.public_id, m.id AS sequence, m.body, m.created_at,
                          p.id AS participant_id, p.display_name, p.kind
                   FROM messages m JOIN participants p ON p.id = m.participant_id
                   WHERE m.chamber_id = ? AND m.id > ? AND (? IS NULL OR m.session_id = ?) ORDER BY m.id ASC LIMIT ?""",
                (chamber_id, after, session_id, session_id, limit),
            ).fetchall()
            self._audit(connection, principal.subject, "message.list", "chamber", chamber_id, "allowed",
                        {"after": after, "limit": limit})
        messages = [
            {
                "session_id": row["session_id"],
                "id": row["public_id"], "sequence": row["sequence"],
                "participant": {"id": row["participant_id"], "display_name": row["display_name"], "kind": row["kind"]},
                "body": row["body"], "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {"messages": messages, "next_after": messages[-1]["sequence"] if messages else after}

