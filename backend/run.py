"""本机启动入口。

用法（在仓库根目录）：
    python backend/run.py
或：
    python -m uvicorn backend.app:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from backend.config import settings  # noqa: E402


def main() -> None:
    print(f"记忆银行后端启动中：http://{settings.host}:{settings.port}")
    print(f"接口文档：http://{settings.host}:{settings.port}/docs")
    print(f"数据目录：{settings.data_dir}")
    uvicorn.run("backend.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
