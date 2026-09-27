from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .config import private_dir


def enrollment_state_dir(home: Path, hub_url: str, credentials: dict) -> Path:
    identity = json.dumps([hub_url, credentials["agent_id"], credentials["owner_id"]])
    scope = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return home / "enrollments" / scope


def sanitize(value, secrets: tuple[str, ...] = ()):
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(r"token|password|secret|api.?key", k, re.I)
                else sanitize(v, secrets)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
        return value if len(value) <= 16000 else value[:16000] + "\n[TRUNCATED]"
    return value


class State:
    def __init__(self, home: Path, secrets: tuple[str, ...] = ()):
        private_dir(home)
        self.secrets = secrets
        self.db = sqlite3.connect(home / "state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, owner TEXT, project TEXT, runtime TEXT, provider_id TEXT);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS controls (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)

    def close(self):
        self.db.close()

    def meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def claim_id(self) -> str:
        identifier = self.meta("claim_id")
        if not identifier:
            identifier = str(uuid.uuid4())
            self.set_meta("claim_id", identifier)
        return identifier

    def clear_claim(self):
        with self.db:
            self.db.execute("DELETE FROM meta WHERE key='claim_id'")

    def start(self, job: dict) -> bool:
        with self.db:
            result = self.db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?, 'running', ?)", (job["id"], time.time())
            )
        return result.rowcount == 1

    def emit(self, job_id: str, kind: str, data: dict):
        self._event(job_id, kind, data)
        self.db.commit()

    def _event(self, job_id: str, kind: str, data: dict):
        event = {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "type": kind,
            "at": time.time(),
            "data": sanitize(data, self.secrets),
        }
        self.db.execute(
            "INSERT INTO events(id,payload) VALUES (?,?)",
            (event["id"], json.dumps(event, ensure_ascii=False)),
        )

    def finish(self, job_id: str, status: str, data: dict):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status=?,updated=? WHERE id=?", (status, time.time(), job_id)
            )
            self._event(job_id, status, data)

    def recover(self):
        rows = self.db.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
        for row in rows:
            self.finish(row[0], "interrupted", {"reason": "agent_restarted", "retry_safe": False})

    def pending(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT seq,payload FROM events ORDER BY seq LIMIT ?", (limit,)
        ).fetchall()
        return [{**json.loads(row[1]), "sequence": row[0]} for row in rows]

    def acknowledge(self, identifiers: list[str]):
        with self.db:
            self.db.executemany("DELETE FROM events WHERE id=?", [(i,) for i in identifiers])

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def session(self, identifier: str, owner: str, project: str, runtime: str) -> str | None:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
        if row and (row["owner"], row["project"], row["runtime"]) != (owner, project, runtime):
            raise ValueError("Session belongs to another owner, project or runtime")
        return row["provider_id"] if row else None

    def save_session(self, identifier: str, owner: str, project: str, runtime: str, provider: str):
        self.session(identifier, owner, project, runtime)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)",
                (identifier, owner, project, runtime, provider),
            )

    def control_seen(self, identifier: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM controls WHERE id=?", (identifier,)).fetchone()
            is not None
        )

    def mark_control(self, identifier: str):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO controls VALUES (?)", (identifier,))

    def recent_jobs(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT 20")
        ]
