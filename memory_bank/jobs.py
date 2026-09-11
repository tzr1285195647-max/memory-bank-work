from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


class JobError(RuntimeError):
    pass


class JobNotFoundError(JobError, KeyError):
    pass


class InvalidJobTransitionError(JobError):
    pass


class ConcurrentJobUpdateError(JobError):
    pass


class JobOwnershipError(JobError):
    pass


class ConsentGateRequiredError(JobError):
    pass


class StaleConsentError(JobError, PermissionError):
    pass


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
_ALLOWED_TRANSITIONS = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.PENDING, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("job timestamps must be timezone-aware")
    return value.astimezone(UTC)


def retry_delay_seconds(attempt: int, *, base_seconds: float = 2.0, cap_seconds: float = 300.0) -> float:
    """Deterministic exponential backoff (2, 4, 8...) for an attempt number."""
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    if base_seconds <= 0 or cap_seconds <= 0:
        raise ValueError("retry delay values must be positive")
    return min(cap_seconds, base_seconds * (2 ** (attempt - 1)))


def validate_transition(source: JobStatus, target: JobStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[source]:
        raise InvalidJobTransitionError(f"cannot transition job from {source.value} to {target.value}")


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    job_type: str
    payload: dict[str, Any]
    project_id: str
    consent_version: int
    status: JobStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    idempotency_key: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.id or not self.job_type.strip() or not self.project_id.strip():
            raise ValueError("job id, type, and project id are required")
        if self.consent_version < 1:
            raise ValueError("consent_version must be at least 1")
        if self.max_attempts < 1 or self.attempts < 0 or self.attempts > self.max_attempts:
            raise ValueError("invalid attempt counters")
        _ensure_utc(self.available_at)
        _ensure_utc(self.created_at)
        _ensure_utc(self.updated_at)
        for optional in (self.lease_expires_at, self.started_at, self.finished_at):
            if optional is not None:
                _ensure_utc(optional)
        try:
            json.dumps(self.payload, ensure_ascii=False, allow_nan=False)
            if self.result is not None:
                json.dumps(self.result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("job payload and result must be JSON-serializable") from exc

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        for field_name in (
            "available_at",
            "created_at",
            "updated_at",
            "lease_expires_at",
            "started_at",
            "finished_at",
        ):
            value = data[field_name]
            data[field_name] = value.isoformat() if value is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        values = copy.deepcopy(data)
        values["status"] = JobStatus(values["status"])
        for field_name in (
            "available_at",
            "created_at",
            "updated_at",
            "lease_expires_at",
            "started_at",
            "finished_at",
        ):
            value = values.get(field_name)
            values[field_name] = datetime.fromisoformat(value) if value else None
        return cls(**values)


@runtime_checkable
class JobRepository(Protocol):
    def add(self, job: JobRecord) -> JobRecord: ...

    def get(self, job_id: str) -> JobRecord: ...

    def find_by_idempotency_key(self, key: str) -> JobRecord | None: ...

    def list(self, *, project_id: str | None = None, status: JobStatus | None = None) -> list[JobRecord]: ...

    def save(self, job: JobRecord, *, expected_revision: int) -> JobRecord: ...

    def claim_next(self, worker_id: str, *, now: datetime, lease_seconds: int) -> JobRecord | None: ...


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _clone(job: JobRecord) -> JobRecord:
        return JobRecord.from_dict(job.to_dict())

    def add(self, job: JobRecord) -> JobRecord:
        with self._lock:
            if job.idempotency_key and job.idempotency_key in self._idempotency:
                return self._clone(self._jobs[self._idempotency[job.idempotency_key]])
            if job.id in self._jobs:
                raise ConcurrentJobUpdateError(f"job already exists: {job.id}")
            stored = self._clone(job)
            self._jobs[job.id] = stored
            if job.idempotency_key:
                self._idempotency[job.idempotency_key] = job.id
            return self._clone(stored)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            try:
                return self._clone(self._jobs[job_id])
            except KeyError as exc:
                raise JobNotFoundError(job_id) from exc

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._lock:
            job_id = self._idempotency.get(key)
            return self._clone(self._jobs[job_id]) if job_id else None

    def list(self, *, project_id: str | None = None, status: JobStatus | None = None) -> list[JobRecord]:
        with self._lock:
            jobs = [
                self._clone(job)
                for job in self._jobs.values()
                if (project_id is None or job.project_id == project_id)
                and (status is None or job.status == status)
            ]
        return sorted(jobs, key=lambda item: (item.created_at, item.id))

    def save(self, job: JobRecord, *, expected_revision: int) -> JobRecord:
        with self._lock:
            current = self._jobs.get(job.id)
            if current is None:
                raise JobNotFoundError(job.id)
            if current.revision != expected_revision:
                raise ConcurrentJobUpdateError(job.id)
            stored = replace(self._clone(job), revision=expected_revision + 1)
            self._jobs[job.id] = stored
            return self._clone(stored)

    def claim_next(self, worker_id: str, *, now: datetime, lease_seconds: int) -> JobRecord | None:
        now = _ensure_utc(now)
        with self._lock:
            candidates = sorted(
                (
                    item
                    for item in self._jobs.values()
                    if item.status == JobStatus.PENDING
                    and item.available_at <= now
                    and item.attempts < item.max_attempts
                ),
                key=lambda item: (item.available_at, item.created_at, item.id),
            )
            if not candidates:
                return None
            current = candidates[0]
            claimed = replace(
                current,
                status=JobStatus.RUNNING,
                attempts=current.attempts + 1,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=current.started_at or now,
                updated_at=now,
                revision=current.revision + 1,
            )
            self._jobs[current.id] = claimed
            return self._clone(claimed)


class SQLiteJobRepository:
    """SQLite-backed job repository safe for multiple local worker processes."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS background_jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    consent_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    available_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    result_json TEXT,
                    error TEXT,
                    started_at REAL,
                    finished_at REAL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON background_jobs(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_project
                    ON background_jobs(project_id, created_at);
                """
            )

    @staticmethod
    def _timestamp(value: datetime | None) -> float | None:
        return _ensure_utc(value).timestamp() if value is not None else None

    @staticmethod
    def _datetime(value: float | None) -> datetime | None:
        return datetime.fromtimestamp(value, UTC) if value is not None else None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            job_type=row["job_type"],
            payload=json.loads(row["payload_json"]),
            project_id=row["project_id"],
            consent_version=row["consent_version"],
            status=JobStatus(row["status"]),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            available_at=cls._datetime(row["available_at"]),  # type: ignore[arg-type]
            created_at=cls._datetime(row["created_at"]),  # type: ignore[arg-type]
            updated_at=cls._datetime(row["updated_at"]),  # type: ignore[arg-type]
            idempotency_key=row["idempotency_key"],
            lease_owner=row["lease_owner"],
            lease_expires_at=cls._datetime(row["lease_expires_at"]),
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
            error=row["error"],
            started_at=cls._datetime(row["started_at"]),
            finished_at=cls._datetime(row["finished_at"]),
            revision=row["revision"],
        )

    @classmethod
    def _values(cls, job: JobRecord) -> tuple[Any, ...]:
        return (
            job.id,
            job.job_type,
            json.dumps(job.payload, ensure_ascii=False, separators=(",", ":")),
            job.project_id,
            job.consent_version,
            job.status.value,
            job.attempts,
            job.max_attempts,
            cls._timestamp(job.available_at),
            cls._timestamp(job.created_at),
            cls._timestamp(job.updated_at),
            job.idempotency_key,
            job.lease_owner,
            cls._timestamp(job.lease_expires_at),
            json.dumps(job.result, ensure_ascii=False, separators=(",", ":")) if job.result is not None else None,
            job.error,
            cls._timestamp(job.started_at),
            cls._timestamp(job.finished_at),
            job.revision,
        )

    def add(self, job: JobRecord) -> JobRecord:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO background_jobs (
                        id, job_type, payload_json, project_id, consent_version,
                        status, attempts, max_attempts, available_at, created_at,
                        updated_at, idempotency_key, lease_owner, lease_expires_at,
                        result_json, error, started_at, finished_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._values(job),
                )
            except sqlite3.IntegrityError as exc:
                if job.idempotency_key:
                    existing = self.find_by_idempotency_key(job.idempotency_key)
                    if existing is not None:
                        return existing
                raise ConcurrentJobUpdateError(job.id) from exc
        return self.get(job.id)

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._from_row(row)

    def find_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self, *, project_id: str | None = None, status: JobStatus | None = None) -> list[JobRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            values.append(project_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM background_jobs{where} ORDER BY created_at, id", values
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def save(self, job: JobRecord, *, expected_revision: int) -> JobRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs SET
                    job_type = ?, payload_json = ?, project_id = ?, consent_version = ?,
                    status = ?, attempts = ?, max_attempts = ?, available_at = ?,
                    created_at = ?, updated_at = ?, idempotency_key = ?, lease_owner = ?,
                    lease_expires_at = ?, result_json = ?, error = ?, started_at = ?,
                    finished_at = ?, revision = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    job.job_type,
                    json.dumps(job.payload, ensure_ascii=False, separators=(",", ":")),
                    job.project_id,
                    job.consent_version,
                    job.status.value,
                    job.attempts,
                    job.max_attempts,
                    self._timestamp(job.available_at),
                    self._timestamp(job.created_at),
                    self._timestamp(job.updated_at),
                    job.idempotency_key,
                    job.lease_owner,
                    self._timestamp(job.lease_expires_at),
                    json.dumps(job.result, ensure_ascii=False, separators=(",", ":"))
                    if job.result is not None
                    else None,
                    job.error,
                    self._timestamp(job.started_at),
                    self._timestamp(job.finished_at),
                    expected_revision + 1,
                    job.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM background_jobs WHERE id = ?", (job.id,)
                ).fetchone()
                if exists is None:
                    raise JobNotFoundError(job.id)
                raise ConcurrentJobUpdateError(job.id)
        return self.get(job.id)

    def claim_next(self, worker_id: str, *, now: datetime, lease_seconds: int) -> JobRecord | None:
        now = _ensure_utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM background_jobs
                WHERE status = ? AND available_at <= ? AND attempts < max_attempts
                ORDER BY available_at, created_at, id
                LIMIT 1
                """,
                (JobStatus.PENDING.value, self._timestamp(now)),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            current = self._from_row(row)
            cursor = connection.execute(
                """
                UPDATE background_jobs SET
                    status = ?, attempts = ?, lease_owner = ?, lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?, revision = revision + 1
                WHERE id = ? AND status = ? AND revision = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    current.attempts + 1,
                    worker_id,
                    self._timestamp(now + timedelta(seconds=lease_seconds)),
                    self._timestamp(now),
                    self._timestamp(now),
                    current.id,
                    JobStatus.PENDING.value,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ConcurrentJobUpdateError(current.id)
            connection.commit()
        return self.get(current.id)


ConsentGate = Callable[[str, int], bool]


class JobQueue:
    """State-machine facade; workers should not mutate repository rows directly."""

    def __init__(
        self,
        repository: JobRepository,
        *,
        consent_gate: ConsentGate | None = None,
        base_retry_seconds: float = 2.0,
        max_retry_seconds: float = 300.0,
    ) -> None:
        if base_retry_seconds <= 0 or max_retry_seconds <= 0:
            raise ValueError("retry delay values must be positive")
        self.repository = repository
        self.consent_gate = consent_gate
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds

    def get(self, job_id: str) -> JobRecord:
        return self.repository.get(job_id)

    def list(self, *, project_id: str | None = None, status: JobStatus | None = None) -> list[JobRecord]:
        return self.repository.list(project_id=project_id, status=status)

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        project_id: str,
        consent_version: int,
        max_attempts: int = 3,
        idempotency_key: str | None = None,
        available_at: datetime | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        current_time = _ensure_utc(now or utc_now())
        if idempotency_key:
            existing = self.repository.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
        record = JobRecord(
            id=job_id or f"job_{uuid.uuid4().hex}",
            job_type=job_type,
            payload=copy.deepcopy(payload),
            project_id=project_id,
            consent_version=consent_version,
            status=JobStatus.PENDING,
            attempts=0,
            max_attempts=max_attempts,
            available_at=_ensure_utc(available_at or current_time),
            created_at=current_time,
            updated_at=current_time,
            idempotency_key=idempotency_key,
        )
        return self.repository.add(record)

    def claim(self, worker_id: str, *, lease_seconds: int = 60, now: datetime | None = None) -> JobRecord | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        return self.repository.claim_next(
            worker_id,
            now=_ensure_utc(now or utc_now()),
            lease_seconds=lease_seconds,
        )

    @staticmethod
    def _require_running(job: JobRecord, worker_id: str | None) -> None:
        if job.status != JobStatus.RUNNING:
            raise InvalidJobTransitionError(f"job is {job.status.value}, not running")
        if worker_id is not None and job.lease_owner != worker_id:
            raise JobOwnershipError(f"job is leased by {job.lease_owner!r}")

    def complete(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        worker_id: str | None = None,
        current_consent_version: int | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        current_time = _ensure_utc(now or utc_now())
        job = self.repository.get(job_id)
        self._require_running(job, worker_id)

        if self.consent_gate is not None:
            try:
                consent_is_current = bool(self.consent_gate(job.project_id, job.consent_version))
            except Exception:
                consent_is_current = False
        elif current_consent_version is not None:
            consent_is_current = current_consent_version == job.consent_version
        else:
            raise ConsentGateRequiredError("a live consent check is required before committing a job result")

        if not consent_is_current:
            cancelled = self._transition(
                job,
                JobStatus.CANCELLED,
                now=current_time,
                error="consent version is stale or revoked",
            )
            self.repository.save(cancelled, expected_revision=job.revision)
            raise StaleConsentError("job result was discarded because consent is no longer current")

        succeeded = self._transition(
            job,
            JobStatus.SUCCEEDED,
            now=current_time,
            result=copy.deepcopy(result),
            error=None,
        )
        return self.repository.save(succeeded, expected_revision=job.revision)

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        worker_id: str | None = None,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> JobRecord:
        current_time = _ensure_utc(now or utc_now())
        job = self.repository.get(job_id)
        self._require_running(job, worker_id)
        message = (error or "background job failed")[:4_000]
        if retryable and job.attempts < job.max_attempts:
            delay = retry_delay_seconds(
                job.attempts,
                base_seconds=self.base_retry_seconds,
                cap_seconds=self.max_retry_seconds,
            )
            pending = self._transition(
                job,
                JobStatus.PENDING,
                now=current_time,
                available_at=current_time + timedelta(seconds=delay),
                error=message,
            )
            return self.repository.save(pending, expected_revision=job.revision)

        failed = self._transition(job, JobStatus.FAILED, now=current_time, error=message)
        return self.repository.save(failed, expected_revision=job.revision)

    def cancel(self, job_id: str, reason: str = "cancelled", *, now: datetime | None = None) -> JobRecord:
        job = self.repository.get(job_id)
        if job.status == JobStatus.CANCELLED:
            return job
        if job.is_terminal:
            raise InvalidJobTransitionError(f"cannot cancel a {job.status.value} job")
        cancelled = self._transition(
            job,
            JobStatus.CANCELLED,
            now=_ensure_utc(now or utc_now()),
            error=reason[:4_000],
        )
        return self.repository.save(cancelled, expected_revision=job.revision)

    def cancel_project(
        self,
        project_id: str,
        reason: str = "project consent revoked",
        *,
        now: datetime | None = None,
    ) -> list[JobRecord]:
        """Cancel every non-terminal job belonging to a revoked project."""
        current_time = _ensure_utc(now or utc_now())
        cancelled: list[JobRecord] = []
        for job in self.repository.list(project_id=project_id):
            if job.is_terminal:
                continue
            try:
                cancelled.append(self.cancel(job.id, reason, now=current_time))
            except ConcurrentJobUpdateError:
                continue
        return cancelled

    def recover_expired_leases(self, *, now: datetime | None = None) -> list[JobRecord]:
        current_time = _ensure_utc(now or utc_now())
        recovered: list[JobRecord] = []
        for job in self.repository.list(status=JobStatus.RUNNING):
            if job.lease_expires_at is None or job.lease_expires_at > current_time:
                continue
            if job.attempts < job.max_attempts:
                updated = self._transition(
                    job,
                    JobStatus.PENDING,
                    now=current_time,
                    available_at=current_time,
                    error="worker lease expired",
                )
            else:
                updated = self._transition(
                    job,
                    JobStatus.FAILED,
                    now=current_time,
                    error="worker lease expired after final attempt",
                )
            try:
                recovered.append(self.repository.save(updated, expected_revision=job.revision))
            except ConcurrentJobUpdateError:
                # Another worker recovered or finished it first.
                continue
        return recovered

    @staticmethod
    def _transition(
        job: JobRecord,
        target: JobStatus,
        *,
        now: datetime,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        available_at: datetime | None = None,
    ) -> JobRecord:
        validate_transition(job.status, target)
        terminal = target in TERMINAL_STATUSES
        return replace(
            job,
            status=target,
            result=result if target == JobStatus.SUCCEEDED else None,
            error=error,
            available_at=available_at or job.available_at,
            updated_at=now,
            lease_owner=None if target != JobStatus.RUNNING else job.lease_owner,
            lease_expires_at=None if target != JobStatus.RUNNING else job.lease_expires_at,
            finished_at=now if terminal else None,
        )
