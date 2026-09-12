"""工程自检：校验 app.json / 各页面配置引用的文件是否存在，以及组件四件套是否齐全。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
problems: list[str] = []
checked = 0


def require(rel: str, why: str) -> None:
    global checked
    checked += 1
    if not (ROOT / rel).exists():
        problems.append(f"缺失 {rel}  （{why}）")


app = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))

print("=== 1. 页面文件 ===")
for page in app["pages"]:
    base = ROOT / page
    for ext in (".js", ".json", ".wxml", ".wxss"):
        require(f"{page}{ext}", f"页面 {page}")
    print(f"  {page:<32} {'OK' if base.with_suffix('.js').exists() else '缺失'}")

print("\n=== 2. tabBar 图标 ===")
for item in app.get("tabBar", {}).get("list", []):
    for key in ("iconPath", "selectedIconPath"):
        require(item[key], f"tabBar「{item['text']}」")
        print(f"  {item[key]:<34} {'OK' if (ROOT / item[key]).exists() else '缺失'}")

print("\n=== 3. 组件四件套 ===")
component_dirs = sorted((ROOT / "components").iterdir())
for comp in component_dirs:
    if not comp.is_dir():
        continue
    for ext in (".js", ".json", ".wxml", ".wxss"):
        require(f"components/{comp.name}/index{ext}", f"组件 {comp.name}")
    print(f"  components/{comp.name:<16} {'OK' if (comp / 'index.js').exists() else '缺失'}")

print("\n=== 4. 各页面 usingComponents 指向 ===")
for page in app["pages"]:
    cfg_path = ROOT / f"{page}.json"
    if not cfg_path.exists():
        continue
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for name, path in cfg.get("usingComponents", {}).items():
        target = (cfg_path.parent / path).resolve()
        checked += 1
        if not (target.with_suffix(".js")).exists():
            problems.append(f"{page}.json 引用的组件不存在: {name} -> {path}")
    if cfg.get("usingComponents"):
        print(f"  {page:<32} {list(cfg['usingComponents'].keys())}")

print("\n=== 5. 必需文件 ===")
for rel in ("app.js", "app.json", "app.wxss", "sitemap.json", "project.config.json"):
    require(rel, "工程必需")
    print(f"  {rel:<24} {'OK' if (ROOT / rel).exists() else '缺失'}")

print("\n" + "=" * 66)
print(f"检查项 {checked} 个，问题 {len(problems)} 个")
for item in problems:
    print(f"  ✗ {item}")
sys.exit(1 if problems else 0)
