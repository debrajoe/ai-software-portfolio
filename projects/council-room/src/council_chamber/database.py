from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  subject TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner', 'agent_member')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chambers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS participants (
  id TEXT PRIMARY KEY,
  chamber_id TEXT NOT NULL REFERENCES chambers(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  display_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('human', 'agent')),
  created_at TEXT NOT NULL,
  UNIQUE(chamber_id, user_id),
  UNIQUE(chamber_id, display_name)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id TEXT NOT NULL UNIQUE,
  chamber_id TEXT NOT NULL REFERENCES chambers(id) ON DELETE CASCADE,
  participant_id TEXT NOT NULL REFERENCES participants(id),
  body TEXT NOT NULL CHECK (length(body) > 0),
  client_message_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(chamber_id, participant_id, client_message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_order
  ON messages(chamber_id, id);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  actor_subject TEXT NOT NULL,
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  context_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        connection=self.connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported database schema version")
            connection.executescript(SCHEMA)
            # One transaction: simultaneous initializers recheck after acquiring the lock.
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute("PRAGMA user_version").fetchone()[0] == 0:
                    connection.execute("""CREATE TABLE sessions (
                        id TEXT PRIMARY KEY,
                        chamber_id TEXT NOT NULL REFERENCES chambers(id),
                        name TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        closed_at TEXT,
                        UNIQUE(id, chamber_id)
                    )""")
                    connection.execute("CREATE UNIQUE INDEX one_open_session ON sessions(chamber_id) WHERE closed_at IS NULL")
                    connection.execute("ALTER TABLE messages ADD COLUMN session_id TEXT REFERENCES sessions(id)")
                    connection.execute("CREATE INDEX idx_session_messages ON messages(session_id, id)")
                    # Enforce chamber binding even for direct database inserts/updates.
                    for event in ("INSERT", "UPDATE"):
                        connection.execute(f"""CREATE TRIGGER session_chamber_{event.lower()}
                            BEFORE {event} ON messages WHEN NEW.session_id IS NOT NULL
                            AND NOT EXISTS (SELECT 1 FROM sessions WHERE id=NEW.session_id
                                            AND chamber_id=NEW.chamber_id)
                            BEGIN SELECT RAISE(ABORT, 'Session chamber mismatch'); END""")
                    connection.execute("PRAGMA user_version = 1")
                if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                    connection.execute("""CREATE TABLE ai_requests (
                        session_id TEXT NOT NULL,
                        chamber_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','cancelled')),
                        created_at INTEGER NOT NULL,
                        message_id TEXT,
                        PRIMARY KEY(session_id, request_id),
                        FOREIGN KEY(session_id, chamber_id) REFERENCES sessions(id, chamber_id)
                    )""")
                    connection.execute("CREATE UNIQUE INDEX one_pending_ai ON ai_requests(session_id) WHERE status='pending'")
                    connection.execute("PRAGMA user_version = 2")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

