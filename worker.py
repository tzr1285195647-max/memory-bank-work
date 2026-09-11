from __future__ import annotations

"""Standalone transcription worker process."""

import logging
import os
import socket
import time
import uuid

from memory_bank.assets import LocalObjectStorage
from memory_bank.config import load_settings
from memory_bank.db.session import create_database_engine, create_session_factory, initialize_database
from memory_bank.product_worker import run_once
from memory_bank.providers.mock import MockASRProvider


LOGGER = logging.getLogger("memory_bank.worker")


def _poll_interval() -> float:
    raw = os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1")
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("WORKER_POLL_INTERVAL_SECONDS must be a number") from exc
    if not 0.05 <= value <= 60:
        raise RuntimeError("WORKER_POLL_INTERVAL_SECONDS must be between 0.05 and 60")
    return value


def _worker_id() -> str:
    configured = os.getenv("WORKER_ID", "").strip()
    return configured or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    engine = create_database_engine(settings.database_url)
    if settings.mode != "production":
        # Local development remains one-command runnable. Production startup
        # applies reviewed Alembic migrations before launching this process.
        initialize_database(engine)
    session_factory = create_session_factory(engine)
    storage = LocalObjectStorage(
        settings.object_storage_dir or settings.data_dir / "objects",
        max_bytes=settings.max_upload_bytes,
    )
    asr_provider = MockASRProvider(os.getenv("MOCK_ASR_TRANSCRIPT", "这是一段用于本地开发的模拟转写。"))
    poll_interval = _poll_interval()
    worker_id = _worker_id()
    run_one = os.getenv("WORKER_RUN_ONCE", "false").strip().lower() in {"1", "true", "yes"}
    LOGGER.info("transcription worker started", extra={"worker_id": worker_id})
    try:
        while True:
            outcome = run_once(session_factory, storage, asr_provider, worker_id=worker_id)
            if outcome is None:
                if run_one:
                    return
                time.sleep(poll_interval)
                continue
            LOGGER.info(
                "transcription job finished: job_id=%s status=%s segments_written=%s error=%s",
                outcome.job_id,
                outcome.status.value,
                outcome.segments_written,
                outcome.error or "",
            )
            if run_one:
                return
    except KeyboardInterrupt:
        LOGGER.info("transcription worker stopped")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
