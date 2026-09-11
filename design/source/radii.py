"""从生成的 SVG 中提取真实圆角值，核对 DESIGN_SPEC.md 的描述。"""

from __future__ import annotations

import collections
import re
from pathlib import Path

svg_dir = Path(r"D:\记忆银行\design\svg")
counter: collections.Counter[str] = collections.Counter()

for svg in sorted(svg_dir.glob("*.svg")):
    text = svg.read_text(encoding="utf-8")
    for width, height, radius in re.findall(
        r'<rect[^>]*width="([\d.]+)" height="([\d.]+)" rx="([\d.]+)"', text
    ):
        key = f"w={width:<6} h={height:<6} rx={radius}"
        counter[key] += 1

print("矩形尺寸与圆角组合（出现次数）：")
for key, count in sorted(counter.items()):
    print(f"  {key}   ×{count}")
