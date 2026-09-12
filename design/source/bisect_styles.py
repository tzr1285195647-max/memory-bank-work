"""二分实验辅助：在工程与 .tmp_bisect 之间搬移页面/组件样式，支持单个文件精确定位。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
TMP = ROOT / ".tmp_bisect"


def stash(kind: str | None = None) -> None:
    TMP.mkdir(exist_ok=True)
    count = 0
    for base, prefix in ((ROOT / "components", "comp"), (ROOT / "pages", "page")):
        if kind and kind != prefix:
            continue
        for path in sorted(base.glob("*/index.wxss")):
            shutil.move(str(path), str(TMP / f"{prefix}_{path.parent.name}.wxss"))
            count += 1
    print(f"已移出 {count} 个文件（kind={kind or 'all'}）")


def restore(names: list[str]) -> None:
    count = 0
    for src in sorted(TMP.glob("*.wxss")):
        prefix, folder = src.name.split("_", 1)
        folder = folder[: -len(".wxss")]
        if names and folder not in names:
            continue
        target_dir = ROOT / ("components" if prefix == "comp" else "pages") / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target_dir / "index.wxss"))
        count += 1
        print(f"  恢复 {prefix}/{folder}")
    print(f"已恢复 {count} 个文件")


def status() -> None:
    in_project = list((ROOT / "components").glob("*/index.wxss")) + list((ROOT / "pages").glob("*/index.wxss"))
    in_tmp = list(TMP.glob("*.wxss"))
    print(f"工程内 wxss: {len(in_project)}  备份内: {len(in_tmp)}")
    if in_tmp:
        print("  备份: " + ", ".join(sorted(p.name for p in in_tmp)))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    args = sys.argv[2:]
    if action == "stash":
        stash(args[0] if args else None)
    elif action == "restore":
        restore(args)
    status()
