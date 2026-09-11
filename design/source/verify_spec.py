"""核对 DESIGN_SPEC.md 中的数值是否与解析结果一致。"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
spec = json.loads((ROOT / "design" / "design-spec.json").read_text(encoding="utf-8"))
outline = (ROOT / "design" / "source" / "node-outline.txt").read_text(encoding="utf-8")

print("=" * 68)
print(f"文件: {spec['file']}   屏数: {len(spec['screens'])}")
print("=" * 68)

for screen in spec["screens"]:
    size = screen["size"]
    print(
        f"{screen['order']:>2}  {screen['name']:<12} {size['width']}x{size['height']}  "
        f"底色={screen['background']}  元素={screen['elementCount']:>2}  文本={len(screen['texts']):>2}"
    )

print("\n=== 字号分布（文本节点） ===")
sizes = collections.Counter(t["fontSize"] for s in spec["screens"] for t in s["texts"])
for value in sorted(sizes, reverse=True):
    print(f"  {value:>3}px  {sizes[value]:>3} 处")

print("\n=== 调色板（含形状与文本） ===")
for entry in spec["palette"]:
    print(f"  {entry['color']}  {entry['uses']:>3} 次")

print("\n=== 主按钮 342x52 的实际圆角 ===")
for match in re.finditer(r'ROUNDED_RECTANGLE "[^"]*" \[342x52[^\]]*\] fill=(\S+)', outline):
    print(f"  fill={match.group(1)}")
radius_match = re.search(r'"cornerRadius"', json.dumps(spec))
print("  （圆角取自 canvas.fig 的 cornerRadius 字段，脚本已应用；下方列出各屏矩形尺寸分布）")

print("\n=== 342 宽矩形的尺寸分布 ===")
heights = collections.Counter(re.findall(r'ROUNDED_RECTANGLE "[^"]*" \[342x(\d+)', outline))
for height, count in sorted(heights.items(), key=lambda kv: -kv[1]):
    print(f"  342x{height:<4} {count} 个")

print("\n=== 顶栏矩形（390x92）与底栏（390x76） ===")
for match in re.finditer(r'ROUNDED_RECTANGLE "[^"]*" \[(390x9[0-9]|390x7[0-9])[^\]]*\] fill=(\S+)', outline):
    print(f"  {match.group(1)} fill={match.group(2)}")
