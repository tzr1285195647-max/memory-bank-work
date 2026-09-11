from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ProjectNotFoundError(KeyError):
    pass


class ConsentRevokedError(PermissionError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"


class MemoryStore:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    consent_by TEXT NOT NULL,
                    consent_version INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    current_question TEXT,
                    round_index INTEGER NOT NULL DEFAULT 0,
                    max_rounds INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    round_index INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, round_index)
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, version)
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_events(project_id, id);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_project(
        self,
        *,
        subject_name: str,
        topic: str,
        consent_by: str,
        family_id: str | None,
        actor_id: str | None,
        max_rounds: int,
    ) -> dict[str, Any]:
        project_id = f"mem_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        family = family_id or f"family_{uuid.uuid4().hex[:8]}"
        actor = actor_id or f"actor_{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, family_id, actor_id, subject_name, topic, stage,
                    consent_by, consent_version, max_rounds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'interview', ?, 1, ?, ?, ?)
                """,
                (project_id, family, actor, subject_name, topic, consent_by, max_rounds, now, now),
            )
            self._log(conn, project_id, "consent.granted", {"by": consent_by, "version": 1})
        return self.get_project(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return dict(row)

    def ensure_authorized(self, project_id: str, consent_version: int) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project["revoked"] or project["consent_version"] != consent_version:
            raise ConsentRevokedError("授权已撤回或版本已失效")
        return project

    def update_stage(
        self,
        project_id: str,
        stage: str,
        *,
        current_question: str | None = None,
        round_index: int | None = None,
    ) -> None:
        assignments = ["stage = ?", "updated_at = ?"]
        values: list[Any] = [stage, utc_now()]
        if current_question is not None:
            assignments.append("current_question = ?")
            values.append(current_question)
        if round_index is not None:
            assignments.append("round_index = ?")
            values.append(round_index)
        values.append(project_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?", values)

    def save_turn(
        self,
        *,
        project_id: str,
        consent_version: int,
        round_index: int,
        question: str,
        answer: str,
        operation_id: str,
    ) -> dict[str, Any]:
        self.ensure_authorized(project_id, consent_version)
        turn_id = stable_id("turn", project_id, round_index)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO turns
                    (id, project_id, round_index, question, answer, operation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (turn_id, project_id, round_index, question, answer.strip(), operation_id, utc_now()),
            )
            conn.execute(
                "UPDATE projects SET round_index = ?, updated_at = ? WHERE id = ?",
                (round_index, utc_now(), project_id),
            )
            self._log(conn, project_id, "interview.turn_committed", {"turn_id": turn_id, "round": round_index})
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        return dict(row)

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        return dict(row)

    def list_turns(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE project_id = ? ORDER BY round_index, created_at", (project_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def save_claims(
        self,
        *,
        project_id: str,
        turn_id: str,
        consent_version: int,
        claims: list[str],
    ) -> list[str]:
        self.ensure_authorized(project_id, consent_version)
        ids: list[str] = []
        with self._connect() as conn:
            for index, text in enumerate(claims):
                claim_id = stable_id("claim", turn_id, index, text)
                ids.append(claim_id)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO claims
                        (id, project_id, turn_id, text, status, confidence, created_at)
                    VALUES (?, ?, ?, ?, 'subject_stated', 0.98, ?)
                    """,
                    (claim_id, project_id, turn_id, text, utc_now()),
                )
            self._log(conn, project_id, "evidence.claims_linked", {"turn_id": turn_id, "claim_ids": ids})
        return ids

    def list_claims(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT claims.*, turns.round_index, turns.question
                FROM claims JOIN turns ON turns.id = claims.turn_id
                WHERE claims.project_id = ?
                ORDER BY turns.round_index, claims.created_at, claims.id
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_draft(
        self,
        *,
        project_id: str,
        consent_version: int,
        content: str,
        operation_id: str,
    ) -> dict[str, Any]:
        self.ensure_authorized(project_id, consent_version)
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM drafts WHERE operation_id = ?", (operation_id,)).fetchone()
            if existing is not None:
                return dict(existing)
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            draft_id = stable_id("draft", project_id, operation_id)
            conn.execute(
                """
                INSERT INTO drafts (id, project_id, version, content, status, operation_id, created_at)
                VALUES (?, ?, ?, ?, 'pending_review', ?, ?)
                """,
                (draft_id, project_id, version, content.strip(), operation_id, utc_now()),
            )
            self._log(conn, project_id, "draft.created", {"draft_id": draft_id, "version": version})
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row)

    def latest_draft(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE project_id = ? ORDER BY version DESC LIMIT 1", (project_id,)
            ).fetchone()
        return self._row(row)

    def approve_draft(self, project_id: str, draft_id: str, consent_version: int) -> None:
        self.ensure_authorized(project_id, consent_version)
        with self._connect() as conn:
            conn.execute("UPDATE drafts SET status = 'superseded' WHERE project_id = ?", (project_id,))
            conn.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft_id,))
            self._log(conn, project_id, "review.approved", {"draft_id": draft_id})

    def save_delivery(
        self,
        *,
        project_id: str,
        consent_version: int,
        content: str,
        operation_id: str,
    ) -> dict[str, Any]:
        self.ensure_authorized(project_id, consent_version)
        delivery_id = stable_id("delivery", project_id, operation_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO deliveries (id, project_id, content, operation_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (delivery_id, project_id, content, operation_id, utc_now()),
            )
            self._log(conn, project_id, "delivery.created", {"delivery_id": delivery_id})
            row = conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        return dict(row)

    def latest_delivery(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM deliveries WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)
            ).fetchone()
        return self._row(row)

    def log_event(self, project_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._log(conn, project_id, event_type, payload)

    def list_events(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE project_id = ? ORDER BY id DESC LIMIT 100", (project_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def revoke_and_delete(self, project_id: str, reason: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        next_version = int(project["consent_version"]) + 1
        with self._connect() as conn:
            conn.execute("DELETE FROM deliveries WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM drafts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM claims WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM turns WHERE project_id = ?", (project_id,))
            conn.execute(
                """
                UPDATE projects
                SET revoked = 1, consent_version = ?, stage = 'revoked',
                    current_question = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_version, utc_now(), project_id),
            )
            self._log(
                conn,
                project_id,
                "consent.revoked_and_content_deleted",
                {"reason": reason, "invalidated_consent_version": project["consent_version"]},
            )
        return self.get_project(project_id)

    def mark_rejected(self, project_id: str) -> None:
        self.update_stage(project_id, "rejected")
        self.log_event(project_id, "review.rejected", {})

    def bundle(self, project_id: str) -> dict[str, Any]:
        return {
            "project": self.get_project(project_id),
            "turns": self.list_turns(project_id),
            "claims": self.list_claims(project_id),
            "draft": self.latest_draft(project_id),
            "delivery": self.latest_delivery(project_id),
            "events": self.list_events(project_id),
        }

    @staticmethod
    def _log(conn: sqlite3.Connection, project_id: str, event_type: str, payload: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO audit_events (project_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (project_id, event_type, json.dumps(payload, ensure_ascii=False), utc_now()),
        )

