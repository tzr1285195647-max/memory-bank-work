from __future__ import annotations

"""Database-backed media worker for the product API.

The worker deliberately keeps provider calls outside database transactions.  A
short transaction claims a job, another performs preflight checks, and a final
transaction persists results only after consent has been checked again.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session

from memory_bank.db.models import (
    AssetStatus,
    AudioAsset,
    ConsentGrant,
    ConsentStatus,
    JobStatus,
    MediaJob,
    MediaJobType,
    MemoryProject,
    ProjectStatus,
    TranscriptSegment,
)
from memory_bank.providers.base import ASRProvider, ASRResult, ObjectStorage


SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    """Compact outcome returned for logging, metrics, and deterministic tests."""

    job_id: str
    status: JobStatus
    segments_written: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _WorkItem:
    job_id: str
    project_id: str
    audio_asset_id: str
    storage_key: str
    content_type: str
    expected_sha256: str
    expected_size: int
    language: str
    consent_version: int


class WorkerInputError(RuntimeError):
    """A claimed job cannot be processed because its persisted input is invalid."""


def _now() -> datetime:
    return datetime.now(UTC)


def _dialect_name(session: Session) -> str:
    return session.get_bind().dialect.name


def _for_update(statement: Select[tuple[object]], session: Session, *, skip_locked: bool = False):
    """Add row locking only where it is meaningful and supported."""

    if _dialect_name(session) == "postgresql":
        return statement.with_for_update(skip_locked=skip_locked)
    return statement


def _claim_next_job(
    session_factory: SessionFactory,
    *,
    worker_id: str,
    now: datetime,
) -> str | None:
    """Claim one queued transcription job with a lock/CAS combination.

    PostgreSQL workers use ``FOR UPDATE SKIP LOCKED``. SQLite ignores row-lock
    syntax, so the conditional update is the compare-and-swap that ensures only
    one worker can change ``queued`` to ``running``.
    """

    # A losing SQLite worker may have selected the same row just before another
    # process committed. Retry a few times so it can pick another queued row.
    for _ in range(3):
        with session_factory() as session:
            statement = (
                select(MediaJob.id, MediaJob.revision)
                .where(
                    MediaJob.job_type == MediaJobType.TRANSCRIPTION,
                    MediaJob.status == JobStatus.QUEUED,
                    MediaJob.attempts < MediaJob.max_attempts,
                    or_(MediaJob.available_at.is_(None), MediaJob.available_at <= now),
                )
                .order_by(MediaJob.available_at.asc(), MediaJob.created_at.asc(), MediaJob.id.asc())
                .limit(1)
            )
            statement = _for_update(statement, session, skip_locked=True)
            candidate = session.execute(statement).first()
            if candidate is None:
                return None
            job_id, revision = candidate

            claimed = session.execute(
                update(MediaJob)
                .where(
                    MediaJob.id == job_id,
                    MediaJob.status == JobStatus.QUEUED,
                    MediaJob.revision == revision,
                )
                .values(
                    status=JobStatus.RUNNING,
                    attempts=MediaJob.attempts + 1,
                    started_at=now,
                    finished_at=None,
                    error_code=None,
                    error_message=None,
                    lease_owner=worker_id[:120],
                    lease_expires_at=now + timedelta(minutes=15),
                    progress_percent=1,
                    revision=MediaJob.revision + 1,
                )
            )
            if claimed.rowcount == 1:
                session.commit()
                return str(job_id)
            session.rollback()
    return None


def _aware(value: datetime) -> datetime:
    # SQLite may return a naive value even for DateTime(timezone=True).
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _active_consent(
    session: Session,
    *,
    project: MemoryProject,
    consent_version: int,
    now: datetime,
) -> ConsentGrant | None:
    if project.status == ProjectStatus.REVOKED:
        return None
    if project.current_consent_version != consent_version:
        return None
    grant = session.scalar(
        select(ConsentGrant).where(
            ConsentGrant.project_id == project.id,
            ConsentGrant.version == consent_version,
            ConsentGrant.status == ConsentStatus.ACTIVE,
        )
    )
    if grant is None:
        return None
    if grant.expires_at is not None and _aware(grant.expires_at) <= now:
        return None
    return grant


def _cancel_job(
    session: Session,
    job: MediaJob,
    *,
    now: datetime,
    reason: str,
) -> WorkerRunResult:
    job.status = JobStatus.CANCELLED
    job.finished_at = now
    job.error_code = "CONSENT_INACTIVE"
    job.error_message = reason[:4_000]
    job.lease_owner = None
    job.lease_expires_at = None
    job.progress_percent = 0
    job.revision += 1
    if job.input_audio_asset is not None and job.input_audio_asset.status == AssetStatus.PROCESSING:
        job.input_audio_asset.status = AssetStatus.READY
    session.commit()
    return WorkerRunResult(job_id=job.id, status=JobStatus.CANCELLED, error=job.error_message)


def _preflight(
    session_factory: SessionFactory,
    job_id: str,
    *,
    now: datetime,
) -> _WorkItem | WorkerRunResult:
    """Validate consent and inputs before any object is read or sent to ASR."""

    with session_factory() as session:
        statement = select(MediaJob).where(MediaJob.id == job_id)
        job = session.scalar(_for_update(statement, session))
        if job is None:
            raise WorkerInputError(f"claimed media job {job_id!r} no longer exists")
        if job.status != JobStatus.RUNNING:
            return WorkerRunResult(job_id=job.id, status=job.status, error=job.error_message)
        if job.job_type != MediaJobType.TRANSCRIPTION:
            raise WorkerInputError("worker claimed a non-transcription job")

        project_statement = select(MemoryProject).where(MemoryProject.id == job.project_id)
        project = session.scalar(_for_update(project_statement, session))
        if project is None:
            raise WorkerInputError("job project no longer exists")
        if _active_consent(
            session,
            project=project,
            consent_version=job.consent_version,
            now=now,
        ) is None:
            return _cancel_job(
                session,
                job,
                now=now,
                reason="consent is revoked, expired, missing, or no longer current",
            )

        if not job.input_audio_asset_id:
            raise WorkerInputError("transcription job has no input audio asset")
        asset_statement = select(AudioAsset).where(AudioAsset.id == job.input_audio_asset_id)
        asset = session.scalar(_for_update(asset_statement, session))
        if asset is None or asset.project_id != project.id:
            raise WorkerInputError("transcription input audio asset is missing or belongs to another project")
        if asset.consent_version != job.consent_version:
            return _cancel_job(
                session,
                job,
                now=now,
                reason="audio asset was collected under a stale consent version",
            )
        if asset.interview_session_id is None:
            raise WorkerInputError("audio asset is not attached to an interview session")
        if asset.status in {AssetStatus.DELETED, AssetStatus.FAILED, AssetStatus.UPLOADING}:
            raise WorkerInputError(f"audio asset is not processable ({asset.status.value})")

        asset.status = AssetStatus.PROCESSING
        session.commit()
        return _WorkItem(
            job_id=job.id,
            project_id=project.id,
            audio_asset_id=asset.id,
            storage_key=asset.storage_key,
            content_type=asset.content_type,
            expected_sha256=asset.checksum_sha256,
            expected_size=asset.byte_size,
            language=project.language,
            consent_version=job.consent_version,
        )


def _read_verified_audio(storage: ObjectStorage, item: _WorkItem) -> bytes:
    with storage.open(item.storage_key) as stream:
        audio = stream.read()
    if len(audio) != item.expected_size:
        raise WorkerInputError("stored audio byte size does not match its database record")
    if hashlib.sha256(audio).hexdigest() != item.expected_sha256:
        raise WorkerInputError("stored audio checksum does not match its database record")
    return audio


def _validate_asr_result(result: ASRResult) -> None:
    seen: set[int] = set()
    for segment in result.segments:
        if segment.index < 0 or segment.index in seen:
            raise WorkerInputError("ASR returned duplicate or negative segment indexes")
        if segment.start_ms < 0 or segment.end_ms < segment.start_ms:
            raise WorkerInputError("ASR returned an invalid segment time range")
        if segment.confidence is not None and not 0 <= segment.confidence <= 1:
            raise WorkerInputError("ASR returned confidence outside 0..1")
        if not segment.text.strip():
            raise WorkerInputError("ASR returned an empty transcript segment")
        seen.add(segment.index)


def _persist_result(
    session_factory: SessionFactory,
    item: _WorkItem,
    result: ASRResult,
    *,
    provider_name: str,
    now: datetime,
) -> WorkerRunResult:
    """Recheck live consent, idempotently persist segments, and finish the job."""

    with session_factory() as session:
        job_statement = select(MediaJob).where(MediaJob.id == item.job_id)
        job = session.scalar(_for_update(job_statement, session))
        if job is None:
            raise WorkerInputError(f"media job {item.job_id!r} disappeared while running")

        asset_statement = select(AudioAsset).where(AudioAsset.id == item.audio_asset_id)
        asset = session.scalar(_for_update(asset_statement, session))
        project_statement = select(MemoryProject).where(MemoryProject.id == item.project_id)
        project = session.scalar(_for_update(project_statement, session))
        if asset is None or project is None:
            raise WorkerInputError("project or audio asset disappeared while transcription was running")

        # An operator may cancel a running job. Never resurrect it or commit the
        # provider result in that case.
        if job.status != JobStatus.RUNNING:
            if asset.status == AssetStatus.PROCESSING:
                asset.status = AssetStatus.READY
                session.commit()
            return WorkerRunResult(job_id=job.id, status=job.status, error=job.error_message)

        if (
            job.consent_version != item.consent_version
            or asset.consent_version != item.consent_version
            or _active_consent(
                session,
                project=project,
                consent_version=item.consent_version,
                now=now,
            )
            is None
        ):
            return _cancel_job(
                session,
                job,
                now=now,
                reason="consent changed or became inactive while transcription was running; result discarded",
            )

        existing_indexes = set(
            session.scalars(
                select(TranscriptSegment.segment_index).where(
                    TranscriptSegment.audio_asset_id == asset.id
                )
            ).all()
        )
        written = 0
        result_summary = {
            "model": result.model,
            "language": result.language,
            "duration_ms": result.duration_ms,
        }
        for segment in result.segments:
            if segment.index in existing_indexes:
                continue
            session.add(
                TranscriptSegment(
                    project_id=project.id,
                    interview_session_id=asset.interview_session_id,
                    audio_asset_id=asset.id,
                    segment_index=segment.index,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text.strip(),
                    speaker_label=segment.speaker,
                    confidence=segment.confidence,
                    language=result.language,
                    provider=provider_name[:80],
                    consent_version=item.consent_version,
                    raw_result=result_summary,
                )
            )
            written += 1

        asset.status = AssetStatus.READY
        asset.duration_ms = result.duration_ms
        job.status = JobStatus.SUCCEEDED
        job.provider = provider_name[:80]
        job.provider_job_id = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.progress_percent = 100
        job.revision += 1
        job.result = {
            "audio_asset_id": asset.id,
            "transcript": result.text,
            "segment_count": len(result.segments),
            "segments_written": written,
            **result_summary,
        }
        job.error_code = None
        job.error_message = None
        job.finished_at = now
        session.commit()
        return WorkerRunResult(job_id=job.id, status=JobStatus.SUCCEEDED, segments_written=written)


def _mark_failed(
    session_factory: SessionFactory,
    job_id: str,
    error: Exception,
    *,
    now: datetime,
) -> WorkerRunResult:
    message = str(error).strip() or error.__class__.__name__
    with session_factory() as session:
        job_statement = select(MediaJob).where(MediaJob.id == job_id)
        job = session.scalar(_for_update(job_statement, session))
        if job is None:
            return WorkerRunResult(job_id=job_id, status=JobStatus.FAILED, error=message[:4_000])
        if job.status == JobStatus.RUNNING:
            job.status = JobStatus.FAILED
            job.finished_at = now
            job.error_code = error.__class__.__name__.upper()[:100]
            job.error_message = message[:4_000]
            job.lease_owner = None
            job.lease_expires_at = None
            job.progress_percent = 0
            job.revision += 1
            if job.input_audio_asset is not None and job.input_audio_asset.status == AssetStatus.PROCESSING:
                job.input_audio_asset.status = AssetStatus.FAILED
            session.commit()
        return WorkerRunResult(job_id=job.id, status=job.status, error=job.error_message or message[:4_000])


def run_once(
    session_factory: SessionFactory,
    storage: ObjectStorage,
    asr_provider: ASRProvider,
    worker_id: str = "transcription-worker",
) -> WorkerRunResult | None:
    """Claim and process at most one queued transcription job.

    Returns ``None`` when no eligible job exists. Operational/provider failures
    are captured on the job and returned as ``FAILED`` outcomes so a long-lived
    worker loop can continue serving later jobs.
    """

    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id is required")
    claimed_at = _now()
    job_id = _claim_next_job(session_factory, worker_id=worker_id, now=claimed_at)
    if job_id is None:
        return None

    try:
        preflight = _preflight(session_factory, job_id, now=_now())
        if isinstance(preflight, WorkerRunResult):
            return preflight
        audio = _read_verified_audio(storage, preflight)
        result = asr_provider.transcribe(
            audio,
            media_type=preflight.content_type,
            language=preflight.language,
        )
        _validate_asr_result(result)
        return _persist_result(
            session_factory,
            preflight,
            result,
            provider_name=asr_provider.name,
            now=_now(),
        )
    except Exception as exc:  # the process loop must survive one bad asset/provider
        return _mark_failed(session_factory, job_id, exc, now=_now())


__all__ = ["WorkerInputError", "WorkerRunResult", "run_once"]
