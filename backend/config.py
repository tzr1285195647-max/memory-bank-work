"""后端配置。

演示版用 SQLite 单文件库；生产部署时只需替换 DATABASE_URL 与对象存储实现，
接口契约不变（见 docs/TECH_PLAN.md 第 9 节）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# 从仓库根目录的 .env 读取配置（该文件已在 .gitignore 中，不会进版本库）。
# 这样 API Key 不必写进代码、不必进 shell 历史，也不必出现在任何对话里。
load_dotenv(PROJECT_ROOT / ".env", override=False)


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
    project_root: Path = PROJECT_ROOT
    # --- 大模型（留空则使用确定性 Mock 智能体，离线可跑）---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.strip())

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
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


settings = load_settings()
