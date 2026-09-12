"""把 WXSS 中的 `> *` 通配选择器替换为显式选择器（WXSS 不支持 *）。

映射依据：这些容器的直接子节点分别是自定义组件 list-item / primary-button
或带类名的原生节点。同时给 components 预览页的子节点补上类名。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(r"D:\记忆银行")

# (文件, 旧选择器, 新选择器)
REPLACEMENTS = [
    ("pages/topic/index.wxss", ".topic__list > * {", ".topic__list > list-item {"),
    ("pages/stories/index.wxss", ".stories__list > * {", ".stories__list > list-item {"),
    ("pages/story-preview/index.wxss", ".preview__actions > * {", ".preview__actions > primary-button {"),
    ("pages/profile/index.wxss", ".profile__rows > * {", ".profile__rows > .profile__row {"),
    ("pages/record/index.wxss", ".record__actions > * {", ".record__actions > primary-button {"),
    ("pages/components/index.wxss", ".demo__row > * {", ".demo__row > .demo__row-item {"),
]

changed = 0
for rel, old, new in REPLACEMENTS:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"  ！未匹配: {rel}  {old}")
        continue
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    changed += 1
    print(f"  ✓ {rel}: {old.strip()} -> {new.strip()}")

# components 预览页：给 demo__row 的直接子节点加类名
wxml = ROOT / "pages/components/index.wxml"
text = wxml.read_text(encoding="utf-8")
before = text
text = text.replace('<ai-badge />', '<ai-badge class="demo__row-item" />')
text = text.replace('<ai-badge size="small" text="AI 整理" />', '<ai-badge class="demo__row-item" size="small" text="AI 整理" />')
text = text.replace(
    '<evidence-tag durationMs="{{222000}}" mode="自然整理" />',
    '<evidence-tag class="demo__row-item" durationMs="{{222000}}" mode="自然整理" />',
)
if text != before:
    wxml.write_text(text, encoding="utf-8")
    print("  ✓ pages/components/index.wxml 已为 demo__row 子节点补类名")

print(f"\n共替换 {changed} 处选择器")

print("\n=== 复查：是否还有通配选择器 ===")
remaining = 0
for path in sorted(ROOT.rglob("*.wxss")):
    if any(part in {".git", "node_modules", "design", "docs"} for part in path.parts):
        continue
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "> *" in line or re.search(r"(^|\s)\*\s*[,{]", line):
            print(f"  ✗ {path.relative_to(ROOT)}:{i}: {line.strip()}")
            remaining += 1
print("  OK 已全部清理" if not remaining else f"  仍有 {remaining} 处")
