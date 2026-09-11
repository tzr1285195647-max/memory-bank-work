from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    database_path: Path
    checkpoint_path: Path
    mode: str
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    object_storage_dir: Path | None = None
    jwt_secret: str = "dev-only-change-me-at-least-32-bytes"
    jwt_issuer: str = "memory-bank"
    access_token_minutes: int = 60
    max_upload_bytes: int = 100 * 1024 * 1024
    auth_required: bool = False


def load_settings(root_dir: Path | None = None) -> Settings:
    root = (root_dir or Path(__file__).resolve().parents[1]).resolve()
    raw_data_dir = Path(os.getenv("MEMORY_BANK_DATA_DIR", ".data"))
    data_dir = raw_data_dir if raw_data_dir.is_absolute() else root / raw_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    mode = os.getenv("MEMORY_BANK_MODE", "mock").strip().lower()
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{(data_dir / 'product.sqlite3').as_posix()}")
    storage_dir = data_dir / "objects"
    storage_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        root_dir=root,
        data_dir=data_dir,
        database_path=data_dir / "memory_bank.sqlite3",
        checkpoint_path=data_dir / "checkpoints.sqlite3",
        mode=mode,
        database_url=database_url,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        object_storage_dir=storage_dir,
        jwt_secret=os.getenv("JWT_SECRET", "dev-only-change-me-at-least-32-bytes"),
        jwt_issuer=os.getenv("JWT_ISSUER", "memory-bank"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "60")),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
        auth_required=os.getenv("AUTH_REQUIRED", "false").lower() in {"1", "true", "yes"},
    )
    if settings.mode == "production":
        if settings.jwt_secret == "dev-only-change-me-at-least-32-bytes":
            raise RuntimeError("生产模式必须通过 JWT_SECRET 设置随机密钥")
        if len(settings.jwt_secret.encode("utf-8")) < 32:
            raise RuntimeError("生产模式的 JWT_SECRET 至少需要 32 字节")
    return settings
