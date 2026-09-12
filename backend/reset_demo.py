"""重置演示数据：把数据库恢复成初始种子状态。

用法（**先停止后端再执行**，否则 Windows 上数据库文件被占用）：
    python backend/reset_demo.py            # 删除数据库与录音，重启后重新播种
    python backend/reset_demo.py --keep-audio   # 保留录音文件（演示前想复用已录原声）

演示前建议执行一次，避免上一次演示生成的草稿/确认记录留在库里。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重置记忆银行演示数据")
    parser.add_argument("--keep-audio", action="store_true", help="保留 objects/ 下的录音文件")
    args = parser.parse_args()

    db_path = settings.database_path
    removed: list[str] = []

    for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate.name)
            except PermissionError:
                print(f"✗ 无法删除 {candidate.name}：文件被占用。请先停止后端（Ctrl+C）再执行。")
                return 1

    if not args.keep_audio and settings.objects_dir.exists():
        shutil.rmtree(settings.objects_dir, ignore_errors=True)
        settings.objects_dir.mkdir(parents=True, exist_ok=True)
        removed.append("objects/")

    print("已重置：")
    for name in removed:
        print(f"  - {name}")
    if not removed:
        print("  （没有需要清理的文件，数据库本就是空的）")

    # 立即重新播种，省去"重启后端才生成数据"这一步
    from backend.database import init_database

    init_database()
    print("\n已重新播种演示数据：")
    print("  演示账号 13800008899 / 123456")
    print("  4 条示例故事（3 条已确认 + 1 条待确认）")
    print("\n接下来启动后端：python backend/run.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
