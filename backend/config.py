"""后端配置。

演示版用 SQLite 单文件库；生产部署时只需替换 DATABASE_URL 与对象存储实现，
接口契约不变（见 docs/TECH_PLAN.md 第 9 节）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    objects_dir: Path
    database_url: str
    jwt_secret: str
    jwt_issuer: str
    access_token_minutes: int
    max_upload_bytes: int
    host: str
    port: int

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("MEMORY_BANK_DATA_DIR", BASE_DIR / ".data")).resolve()
    objects_dir = data_dir / "objects"
    data_dir.mkdir(parents=True, exist_ok=True)
    objects_dir.mkdir(parents=True, exist_ok=True)
    database_url = os.getenv("DATABASE_URL", f"sqlite+pysqlite:///{data_dir / 'app.db'}")
    return Settings(
        data_dir=data_dir,
        objects_dir=objects_dir,
        database_url=database_url,
        # 演示用固定密钥；生产必须通过环境变量注入随机值
        jwt_secret=os.getenv("JWT_SECRET", "memory-bank-demo-secret-do-not-use-in-production"),
        jwt_issuer=os.getenv("JWT_ISSUER", "memory-bank"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "720")),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8787")),
    )


settings = load_settings()
