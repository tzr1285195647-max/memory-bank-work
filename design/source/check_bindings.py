"""列出所有页面/组件的 WXML 事件绑定与 JS 方法的对照表，用于排查点击无反应。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
BIND = re.compile(r'bind:?([a-zA-Z]+)\s*=\s*"([^"{}]+)"')
SKIP = {".git", "node_modules", "design", "docs"}

missing_total = 0
for wxml in sorted(ROOT.rglob("*.wxml")):
    if any(part in SKIP for part in wxml.parts):
        continue
    js = wxml.with_suffix(".js")
    text_j = js.read_text(encoding="utf-8") if js.exists() else ""
    binds = sorted({m.group(2).strip() for m in BIND.finditer(wxml.read_text(encoding="utf-8"))})
    if not binds:
        continue
    print(f"\n{wxml.relative_to(ROOT)}")
    for name in binds:
        found = re.search(rf"(^|[\s,{{]){re.escape(name)}\s*[(:]", text_j, re.MULTILINE)
        if not found:
            missing_total += 1
        print(f"   {'OK     ' if found else 'MISSING'} {name}")

print(f"\n合计缺失 {missing_total} 个事件方法")
