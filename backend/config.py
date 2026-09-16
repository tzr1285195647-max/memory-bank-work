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
    allow_auto_register: bool = False
    # --- 智能体模式：默认 mock，现场演示不触发任何外网请求 ---
    agent_mode: str = "mock"
    # --- 大模型（仅 AGENT_MODE=llm 且配置密钥时启用）---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    # --- 腾讯云语音转文字（仅密钥完整时启用）---
    asr_provider: str = "tencent"
    tencentcloud_secret_id: str = ""
    tencentcloud_secret_key: str = ""
    tencent_asr_region: str = "ap-guangzhou"
    tencent_asr_engine: str = "16k_zh_en_2.0"

    @property
    def llm_enabled(self) -> bool:
        return self.agent_mode == "llm" and bool(self.llm_api_key.strip())

    @property
    def asr_enabled(self) -> bool:
        return (
            self.asr_provider == "tencent"
            and bool(self.tencentcloud_secret_id.strip())
            and bool(self.tencentcloud_secret_key.strip())
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"


def load_settings() -> Settings:
    data_dir_value = os.getenv("MEMORY_BANK_DATA_DIR", "").strip()
    data_dir = Path(data_dir_value).resolve() if data_dir_value else (BASE_DIR / ".data").resolve()
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
        allow_auto_register=os.getenv("ALLOW_AUTO_REGISTER", "0") == "1",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8787")),
        agent_mode=os.getenv("AGENT_MODE", "mock").strip().lower(),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        asr_provider=os.getenv("ASR_PROVIDER", "tencent").strip().lower(),
        tencentcloud_secret_id=os.getenv("TENCENTCLOUD_SECRET_ID", ""),
        tencentcloud_secret_key=os.getenv("TENCENTCLOUD_SECRET_KEY", ""),
        tencent_asr_region=os.getenv("TENCENT_ASR_REGION", "ap-guangzhou"),
        tencent_asr_engine=os.getenv("TENCENT_ASR_ENGINE", "16k_zh_en_2.0"),
    )


settings = load_settings()
