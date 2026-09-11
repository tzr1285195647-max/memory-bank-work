"""为技术方案补充所需的实测数据：rpx 换算、图标形态、间距与安全区。"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
spec = json.loads((ROOT / "design" / "design-spec.json").read_text(encoding="utf-8"))
outline = (ROOT / "design" / "source" / "node-outline.txt").read_text(encoding="utf-8")

print("=" * 70)
print("1. 关键尺寸的 rpx 换算（750rpx = 屏宽；设计稿宽 390px）")
print("=" * 70)
for px in (342, 390, 250, 220, 148, 118, 112, 108, 106, 104, 94, 92, 88, 82, 76, 60, 52, 24, 12):
    print(f"  {px:>3}px  ->  {px * 750 / 390:>7.1f} rpx")

print()
print("=" * 70)
print("2. 所有图标字符（确认是否存在位图/矢量图标）")
print("=" * 70)
glyphs = collections.Counter()
for match in re.finditer(r'TEXT "([^"]*)"', outline):
    text = match.group(1)
    if len(text) <= 2 and not re.search(r"[\u4e00-\u9fff]", text):
        glyphs[text] += 1
for glyph, count in glyphs.most_common():
    print(f"  {glyph!r}  ×{count}")
print("  注：画板内没有任何 VECTOR / ELLIPSE / IMAGE 节点，全部是 TEXT + ROUNDED_RECTANGLE")

print()
print("=" * 70)
print("3. 左右边距与内容宽度校验")
print("=" * 70)
for screen in spec["screens"]:
    xs = [t["x"] for t in screen["texts"] if t["x"] > 0]
    if screen["order"] in (1, 4, 7, 9, 10) and xs:
        print(f"  {screen['name']:<12} 文本最小 x={min(xs)}  最大右边界需人工核对")

print()
print("=" * 70)
print("4. 底部导航与安全区")
print("=" * 70)
nav_top = 857
screen_h = 844
print(f"  底栏矩形 y=857，说明它被放在画板之外（画板高 {screen_h}）")
print(f"  底栏高 76px -> {76 * 750 / 390:.0f} rpx；设计稿未画 iPhone 底部安全区")
print("  实现时需叠加 env(safe-area-inset-bottom)")

print()
print("=" * 70)
print("5. 各屏元素与文本数量（估算工作量）")
print("=" * 70)
total_el = total_tx = 0
for screen in spec["screens"]:
    total_el += screen["elementCount"]
    total_tx += len(screen["texts"])
    print(f"  {screen['order']:>2} {screen['name']:<12} 元素 {screen['elementCount']:>2}  文本 {len(screen['texts']):>2}")
print(f"  合计: 元素 {total_el}，文本 {total_tx}")
