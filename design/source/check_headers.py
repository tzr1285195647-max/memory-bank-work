"""核对各屏顶栏配色，确认 DESIGN_SPEC.md 中“两套顶栏”的说法。"""

from __future__ import annotations

import re
from pathlib import Path

outline = Path(r"D:\记忆银行\design\source\node-outline.txt").read_text(encoding="utf-8")
screens = re.split(r"\n(?=    FRAME )", outline)

print("各屏顶栏（390x92）配色：")
for block in screens:
    header = re.search(r'FRAME "([^"]+)"', block)
    if not header:
        continue
    bar = re.search(r'ROUNDED_RECTANGLE "[^"]*" \[390x92[^\]]*\] fill=(\S+)', block)
    nav = "有" if re.search(r'\[390x76', block) else "无"
    print(f"  {header.group(1):<12} 顶栏={bar.group(1) if bar else '(无)':<10} 底部导航={nav}")
