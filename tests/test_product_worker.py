from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import func, select

from memory_bank.assets import LocalObjectStorage
from memory_bank.db import (
    AssetStatus,
    AudioAsset,
    ConsentGrant,
    ConsentStatus,
    Family,
    InterviewSession,
    InterviewStatus,
    JobStatus,
    MediaJob,
    MediaJobType,
    MemoryProject,
    ProjectStatus,
    TranscriptSegment,
    User,
    create_database_engine,
    create_session_factory,
    initialize_database,
    session_scope,
    sqlite_url,
)
from memory_bank.product_worker import run_once
from memory_bank.providers import MockASRProvider, MockTTSProvider


def _database(tmp_path: Path):
    engine = create_database_engine(sqlite_url(tmp_path / "worker.sqlite3"))
    initialize_database(engine)
    return engine, create_session_factory(engine)


def _seed_transcription(session_factory, storage: LocalObjectStorage) -> dict[str, str]:
    speech = MockTTSProvider().synthesize("用于工作进程测试的采访音频")
    stored = storage.put_bytes("families/f1/projects/p1/interview.wav", speech.audio, content_type="audio/wav")
    with session_scope(session_factory) as session:
        user = User(
            email="owner@example.com",
            email_normalized="owner@example.com",
            display_name="项目所有者",
        )
        family = Family(name="林家", created_by=user)
        project = MemoryProject(
            family=family,
            created_by=user,
            title="外婆的夏天",
            subject_name="外婆",
            topic="童年暑假",
            status=ProjectStatus.ACTIVE,
            current_consent_version=1,
            language="zh-CN",
        )
        consent = ConsentGrant(
            project=project,
            version=1,
            status=ConsentStatus.ACTIVE,
            granted_by=user,
            granted_by_name="外婆本人",
            purpose="家庭记忆整理",
            scopes=["audio", "transcription"],
        )
        interview = InterviewSession(
            project=project,
            started_by=user,
            status=InterviewStatus.ACTIVE,
            current_round=1,
        )
        asset = AudioAsset(
            project=project,
            interview_session=interview,
            uploaded_by=user,
            storage_key=stored.key,
            original_filename="interview.wav",
            content_type=stored.media_type,
            byte_size=stored.size_bytes,
            checksum_sha256=stored.sha256,
            status=AssetStatus.READY,
            consent_version=1,
        )
        job = MediaJob(
            project=project,
            requested_by=user,
            input_audio_asset=asset,
            job_type=MediaJobType.TRANSCRIPTION,
            status=JobStatus.QUEUED,
            consent_version=1,
            idempotency_key="transcribe:asset-1:v1",
            payload={"language": "zh-CN"},
        )
        session.add_all([consent, job])
        session.flush()
        return {
            "project_id": project.id,
            "consent_id": consent.id,
            "asset_id": asset.id,
            "job_id": job.id,
        }


def test_run_once_transcribes_and_is_idempotent(tmp_path: Path):
    engine, session_factory = _database(tmp_path)
    storage = LocalObjectStorage(tmp_path / "objects")
    ids = _seed_transcription(session_factory, storage)
    try:
        outcome = run_once(
            session_factory,
            storage,
            MockASRProvider("傍晚的蝉声最大，我们把西瓜放在井水里。"),
            worker_id="worker-a",
        )
        assert outcome is not None
        assert outcome.status == JobStatus.SUCCEEDED
        assert outcome.segments_written == 1

        with session_scope(session_factory) as session:
            job = session.get(MediaJob, ids["job_id"])
            asset = session.get(AudioAsset, ids["asset_id"])
            segment = session.scalar(
                select(TranscriptSegment).where(TranscriptSegment.audio_asset_id == ids["asset_id"])
            )
            assert job is not None and job.attempts == 1
            assert job.result["transcript"].startswith("傍晚的蝉声")
            assert asset is not None and asset.status == AssetStatus.READY
            assert asset.duration_ms and asset.duration_ms > 0
            assert segment is not None and segment.consent_version == 1

            # Simulate an operator retrying the same durable job. The unique
            # asset/index segment must be reused instead of inserted twice.
            job.status = JobStatus.QUEUED
            job.finished_at = None

        retried = run_once(
            session_factory,
            storage,
            MockASRProvider("傍晚的蝉声最大，我们把西瓜放在井水里。"),
            worker_id="worker-b",
        )
        assert retried is not None
        assert retried.status == JobStatus.SUCCEEDED
        assert retried.segments_written == 0
        with session_scope(session_factory) as session:
            count = session.scalar(
                select(func.count()).select_from(TranscriptSegment).where(
                    TranscriptSegment.audio_asset_id == ids["asset_id"]
                )
            )
            assert count == 1
    finally:
        engine.dispose()


class _CountingASR(MockASRProvider):
    def __init__(self) -> None:
        super().__init__("不应该被调用")
        self.calls = 0

    def transcribe(self, *args, **kwargs):
        self.calls += 1
        return super().transcribe(*args, **kwargs)


def test_revoked_consent_cancels_before_reading_or_transcribing(tmp_path: Path):
    engine, session_factory = _database(tmp_path)
    storage = LocalObjectStorage(tmp_path / "objects")
    ids = _seed_transcription(session_factory, storage)
    provider = _CountingASR()
    try:
        with session_scope(session_factory) as session:
            consent = session.get(ConsentGrant, ids["consent_id"])
            consent.status = ConsentStatus.REVOKED

        outcome = run_once(session_factory, storage, provider)
        assert outcome is not None and outcome.status == JobStatus.CANCELLED
        assert provider.calls == 0
        with session_scope(session_factory) as session:
            assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0
            job = session.get(MediaJob, ids["job_id"])
            assert job is not None and job.error_code == "CONSENT_INACTIVE"
    finally:
        engine.dispose()


class _RevokingASR(MockASRProvider):
    def __init__(self, session_factory, consent_id: str) -> None:
        super().__init__("这份结果必须在授权撤销后被丢弃。")
        self.session_factory = session_factory
        self.consent_id = consent_id

    def transcribe(self, *args, **kwargs):
        result = super().transcribe(*args, **kwargs)
        with session_scope(self.session_factory) as session:
            consent = session.get(ConsentGrant, self.consent_id)
            consent.status = ConsentStatus.REVOKED
        return result


def test_consent_revoked_during_asr_discards_uncommitted_result(tmp_path: Path):
    engine, session_factory = _database(tmp_path)
    storage = LocalObjectStorage(tmp_path / "objects")
    ids = _seed_transcription(session_factory, storage)
    try:
        outcome = run_once(
            session_factory,
            storage,
            _RevokingASR(session_factory, ids["consent_id"]),
        )
        assert outcome is not None and outcome.status == JobStatus.CANCELLED
        with session_scope(session_factory) as session:
            assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0
            asset = session.get(AudioAsset, ids["asset_id"])
            assert asset is not None and asset.status == AssetStatus.READY
    finally:
        engine.dispose()


class _FailingASR(MockASRProvider):
    def transcribe(self, *args, **kwargs):
        raise RuntimeError("provider unavailable")


def test_provider_error_marks_job_and_asset_failed(tmp_path: Path):
    engine, session_factory = _database(tmp_path)
    storage = LocalObjectStorage(tmp_path / "objects")
    ids = _seed_transcription(session_factory, storage)
    try:
        outcome = run_once(session_factory, storage, _FailingASR())
        assert outcome is not None and outcome.status == JobStatus.FAILED
        assert outcome.error == "provider unavailable"
        with session_scope(session_factory) as session:
            job = session.get(MediaJob, ids["job_id"])
            asset = session.get(AudioAsset, ids["asset_id"])
            assert job is not None and job.error_code == "RUNTIMEERROR"
            assert job.error_message == "provider unavailable"
            assert asset is not None and asset.status == AssetStatus.FAILED
            assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0
    finally:
        engine.dispose()


class _BlockingASR(MockASRProvider):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__("并发领取只允许调用一次。")
        self.started = started
        self.release = release
        self.calls = 0

    def transcribe(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=5), "test timed out waiting to release ASR"
        return super().transcribe(*args, **kwargs)


def test_sqlite_claim_prevents_two_workers_from_processing_same_job(tmp_path: Path):
    engine, session_factory = _database(tmp_path)
    storage = LocalObjectStorage(tmp_path / "objects")
    _seed_transcription(session_factory, storage)
    started = threading.Event()
    release = threading.Event()
    provider = _BlockingASR(started, release)
    outcomes = []

    thread = threading.Thread(
        target=lambda: outcomes.append(run_once(session_factory, storage, provider, worker_id="worker-a")),
        daemon=True,
    )
    try:
        thread.start()
        assert started.wait(timeout=5), "first worker did not reach ASR"
        second = run_once(session_factory, storage, provider, worker_id="worker-b")
        assert second is None
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert provider.calls == 1
        assert outcomes and outcomes[0] is not None
        assert outcomes[0].status == JobStatus.SUCCEEDED
    finally:
        release.set()
        thread.join(timeout=5)
        engine.dispose()
