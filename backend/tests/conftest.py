"""pytest 全局配置：强制测试使用确定性 Mock 智能体。

为什么必须这样做：
- 测试不应依赖网络，也不应消耗真实模型额度
- 真实模型输出不确定（措辞、条数都会变），无法用于断言
- `.env` 里配了 LLM_API_KEY 时，若不强制清空，测试会静默调用真模型

`load_dotenv(override=False)` 不会覆盖已存在的环境变量，因此在这里清空即可生效。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest

# Isolate storage before ANY test module can import backend.config/database.
# A test module's own environment setup is too late if another module imported them first.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="memory-bank-pytest-"))
os.environ["MEMORY_BANK_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TEST_DATA_DIR / 'test.db'}"

# 必须在任何 backend 模块被导入前清空：backend.config 在导入时就会读取环境
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MAX_RETRIES"] = "0"
os.environ["TENCENTCLOUD_SECRET_ID"] = ""
os.environ["TENCENTCLOUD_SECRET_KEY"] = ""
os.environ["ALLOW_AUTO_REGISTER"] = "1"


@pytest.fixture(scope="session", autouse=True)
def require_disposable_database():
    """Fail before fixtures write if a future import regression selects a real database."""
    from backend.database import engine
    database = Path(engine.url.database).resolve()
    assert database.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert database.parent.name.startswith("memory-bank-"), "Tests must use a disposable database"
