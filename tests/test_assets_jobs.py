from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from memory_bank.assets import (
    AssetTooLargeError,
    InvalidObjectKeyError,
    LocalObjectStorage,
    MediaTypeMismatchError,
    UnsupportedMediaTypeError,
)
from memory_bank.jobs import (
    ConsentGateRequiredError,
    InMemoryJobRepository,
    InvalidJobTransitionError,
    JobOwnershipError,
    JobQueue,
    JobStatus,
    SQLiteJobRepository,
    StaleConsentError,
    retry_delay_seconds,
)
from memory_bank.providers import (
    ASRProvider,
    ChatMessage,
    LLMProvider,
    MockASRProvider,
    MockLLMProvider,
    MockTTSProvider,
    ObjectStorage,
    TTSProvider,
)


def test_local_storage_streams_validates_hashes_and_deletes(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path / "objects", max_bytes=1_024, chunk_size=3)
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image-body"

    stored = storage.put_stream(
        "families/f1/assets/photo.png",
        (png[index : index + 2] for index in range(0, len(png), 2)),
        content_type="image/png; charset=binary",
    )

    assert isinstance(storage, ObjectStorage)
    assert stored.size_bytes == len(png)
    assert stored.sha256 == hashlib.sha256(png).hexdigest()
    assert stored.media_type == "image/png"
    assert storage.read_bytes(stored.key) == png
    assert storage.stat(stored.key) == stored
    assert storage.delete(stored.key) is True
    assert storage.delete(stored.key) is False
    assert not storage.exists(stored.key)


@pytest.mark.parametrize(
    "key",
    [
        "../outside.png",
        "family/../../outside.png",
        "/absolute.png",
        r"C:\outside.png",
        ".tmp/x.png",
        "assets/NUL.png",
        "assets/photo.png:secret",
    ],
)
def test_local_storage_rejects_path_traversal_and_reserved_keys(tmp_path: Path, key: str):
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(InvalidObjectKeyError):
        storage.put_bytes(key, b"\x89PNG\r\n\x1a\nbody", content_type="image/png")


def test_local_storage_rejects_oversize_and_cleans_temporary_file(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path / "objects", max_bytes=12, chunk_size=4)
    with pytest.raises(AssetTooLargeError):
        storage.put_stream(
            "assets/too-big.png",
            io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 20),
            content_type="image/png",
        )
    assert not storage.exists("assets/too-big.png")
    assert list((tmp_path / "objects" / ".tmp").iterdir()) == []


def test_local_storage_checks_whitelist_and_signature(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path / "objects")
    png = b"\x89PNG\r\n\x1a\nbody"
    with pytest.raises(MediaTypeMismatchError):
        storage.put_bytes("assets/wrong.jpg", png, content_type="image/jpeg")
    with pytest.raises(UnsupportedMediaTypeError):
        storage.put_bytes("assets/fake.png", b"MZ executable", content_type="image/png")
    with pytest.raises(UnsupportedMediaTypeError):
        storage.put_bytes("assets/data.bin", png, content_type="application/octet-stream")


def test_mock_providers_implement_contracts_and_are_deterministic():
    llm = MockLLMProvider(responses=[{"next_question": "那时你听到了什么？"}])
    asr = MockASRProvider("院子里有一棵石榴树。")
    tts = MockTTSProvider()
    assert isinstance(llm, LLMProvider)
    assert isinstance(asr, ASRProvider)
    assert isinstance(tts, TTSProvider)

    completion = llm.complete([ChatMessage(role="user", content="继续采访")])
    assert completion.structured == {"next_question": "那时你听到了什么？"}

    speech = tts.synthesize("你好", voice="narrator")
    assert speech.audio.startswith(b"RIFF") and speech.audio[8:12] == b"WAVE"
    transcript = asr.transcribe(speech.audio, media_type=speech.media_type)
    assert transcript.text == "院子里有一棵石榴树。"
    assert transcript.segments[0].start_ms == 0
    assert transcript.segments[0].end_ms == transcript.duration_ms


def _repositories(tmp_path: Path):
    return [
        InMemoryJobRepository(),
        SQLiteJobRepository(tmp_path / "jobs.sqlite3"),
    ]


def test_job_happy_path_and_idempotency_for_both_repositories(tmp_path: Path):
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    for repository in _repositories(tmp_path):
        queue = JobQueue(repository)
        first = queue.enqueue(
            "asr.transcribe",
            {"asset_key": "audio/a.wav"},
            project_id="project-1",
            consent_version=4,
            idempotency_key="asr:asset-a:v4",
            now=now,
        )
        duplicate = queue.enqueue(
            "asr.transcribe",
            {"asset_key": "ignored-duplicate.wav"},
            project_id="project-1",
            consent_version=4,
            idempotency_key="asr:asset-a:v4",
            now=now,
        )
        assert duplicate.id == first.id

        claimed = queue.claim("worker-a", now=now)
        assert claimed is not None
        assert claimed.status == JobStatus.RUNNING
        assert claimed.attempts == 1
        completed = queue.complete(
            claimed.id,
            {"transcript": "完成"},
            worker_id="worker-a",
            current_consent_version=4,
            now=now + timedelta(seconds=1),
        )
        assert completed.status == JobStatus.SUCCEEDED
        assert completed.result == {"transcript": "完成"}


def test_job_retry_backoff_and_terminal_failure(tmp_path: Path):
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    repository = SQLiteJobRepository(tmp_path / "retry.sqlite3")
    queue = JobQueue(repository, base_retry_seconds=3, max_retry_seconds=20)
    job = queue.enqueue(
        "media.generate",
        {},
        project_id="project-1",
        consent_version=1,
        max_attempts=2,
        now=now,
    )
    claimed = queue.claim("worker-a", now=now)
    retry = queue.fail(claimed.id, "temporary", worker_id="worker-a", now=now)
    assert retry.status == JobStatus.PENDING
    assert retry.available_at == now + timedelta(seconds=3)
    assert queue.claim("worker-a", now=now + timedelta(seconds=2)) is None

    second = queue.claim("worker-a", now=now + timedelta(seconds=3))
    assert second is not None and second.attempts == 2
    failed = queue.fail(second.id, "still broken", worker_id="worker-a", now=now + timedelta(seconds=4))
    assert failed.status == JobStatus.FAILED
    assert failed.finished_at == now + timedelta(seconds=4)
    assert queue.claim("worker-a", now=now + timedelta(hours=1)) is None
    assert retry_delay_seconds(1) == 2
    assert retry_delay_seconds(20, cap_seconds=30) == 30


def test_job_consent_version_gate_discards_stale_result():
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    current_versions = {"project-1": 8}
    repository = InMemoryJobRepository()
    queue = JobQueue(repository, consent_gate=lambda project_id, version: current_versions.get(project_id) == version)
    job = queue.enqueue(
        "asr.transcribe",
        {},
        project_id="project-1",
        consent_version=7,
        now=now,
    )
    queue.claim("worker-a", now=now)

    with pytest.raises(StaleConsentError):
        queue.complete(job.id, {"must": "not persist"}, worker_id="worker-a", now=now)
    discarded = repository.get(job.id)
    assert discarded.status == JobStatus.CANCELLED
    assert discarded.result is None
    assert "consent" in discarded.error


def test_job_requires_live_consent_check_and_enforces_lease_owner():
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    repository = InMemoryJobRepository()
    queue = JobQueue(repository)
    job = queue.enqueue("tts.synthesize", {}, project_id="project-1", consent_version=2, now=now)
    queue.claim("worker-a", now=now)
    with pytest.raises(JobOwnershipError):
        queue.complete(job.id, {}, worker_id="worker-b", current_consent_version=2, now=now)
    with pytest.raises(ConsentGateRequiredError):
        queue.complete(job.id, {}, worker_id="worker-a", now=now)
    assert repository.get(job.id).status == JobStatus.RUNNING


def test_job_lease_recovery_and_invalid_terminal_transition():
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    repository = InMemoryJobRepository()
    queue = JobQueue(repository)
    job = queue.enqueue(
        "asr.transcribe",
        {},
        project_id="project-1",
        consent_version=1,
        max_attempts=2,
        now=now,
    )
    queue.claim("dead-worker", lease_seconds=5, now=now)
    assert queue.recover_expired_leases(now=now + timedelta(seconds=4)) == []
    recovered = queue.recover_expired_leases(now=now + timedelta(seconds=5))
    assert recovered[0].status == JobStatus.PENDING
    reclaimed = queue.claim("worker-b", now=now + timedelta(seconds=5))
    finished = queue.complete(
        reclaimed.id,
        {},
        worker_id="worker-b",
        current_consent_version=1,
        now=now + timedelta(seconds=6),
    )
    assert finished.status == JobStatus.SUCCEEDED
    with pytest.raises(InvalidJobTransitionError):
        queue.cancel(job.id)


def test_sqlite_job_survives_repository_restart(tmp_path: Path):
    database = tmp_path / "durable.sqlite3"
    now = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    first = JobQueue(SQLiteJobRepository(database))
    created = first.enqueue(
        "asr.transcribe",
        {"asset": "a.wav"},
        project_id="project-1",
        consent_version=3,
        now=now,
    )

    restarted = JobQueue(SQLiteJobRepository(database))
    restored = restarted.repository.get(created.id)
    assert restored.to_dict() == created.to_dict()
    assert restarted.claim("worker-after-restart", now=now).id == created.id
