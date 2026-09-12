"""用 tinycss2 严格解析每个 WXSS，找出会造成「编译 .wxss 文件错误」的语法问题。

检查项：
  1. 解析错误（ParseError）
  2. 非法选择器（组件选择器、伪元素等小程序不支持的形式）
  3. 属性名拼写（列出所有出现的属性，便于人工确认）
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import tinycss2

ROOT = Path(r"D:\记忆银行")
SKIP = {".git", "design", "docs", "node_modules", ".pytest_cache"}

problems = 0
properties: collections.Counter[str] = collections.Counter()
selectors_seen: collections.Counter[str] = collections.Counter()

for path in sorted(ROOT.rglob("*.wxss")):
    if any(part in SKIP for part in path.parts):
        continue
    text = path.read_text(encoding="utf-8")
    rules = tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True)
    issues: list[str] = []

    for rule in rules:
        if rule.type == "error":
            issues.append(f"第 {rule.source_line} 行 解析错误: {rule.message}")
            continue
        if rule.type == "at-rule":
            if rule.lower_at_keyword not in {"import", "media", "keyframes", "font-face"}:
                issues.append(f"第 {rule.source_line} 行 未知 @ 规则: @{rule.at_keyword}")
            continue
        if rule.type != "qualified-rule":
            continue

        for selector in tinycss2.serialize(rule.prelude).split(","):
            sel = selector.strip()
            if not sel:
                continue
            selectors_seen[sel] += 1
            # 小程序不支持的选择器形式
            if "::" in sel:
                issues.append(f"第 {rule.source_line} 行 伪元素可能不支持: {sel}")
            if "(hover)" in sel:
                issues.append(f"第 {rule.source_line} 行 不支持的选择器: {sel}")

        for declaration in tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True):
            if declaration.type == "error":
                issues.append(f"第 {declaration.source_line} 行 声明错误: {declaration.message}")
                continue
            if declaration.type != "declaration":
                continue
            name = declaration.lower_name
            properties[name] += 1
            if not declaration.value:
                issues.append(f"第 {declaration.source_line} 行 {name} 缺少值")

    if issues:
        problems += len(issues)
        print(f"✗ {path.relative_to(ROOT)}")
        for item in issues:
            print(f"    {item}")

print("\n" + "=" * 70)
print(f"解析问题合计 {problems} 个")
print("\n出现的属性（排查拼写异常）：")
for name, count in sorted(properties.items()):
    print(f"  {name:<24} {count}")

print("\n选择器中含组合符的（> + ~ 在自定义组件上可能受限）：")
for sel, count in sorted(selectors_seen.items()):
    if any(ch in sel for ch in (" > ", " + ", " ~ ")):
        print(f"  {sel:<40} {count}")

print("\n=== 禁用写法检查（WXSS 编译器不接受的语法）===")
FORBIDDEN = {
    "> *": "WXSS 不支持通配选择器 *",
    " * {": "WXSS 不支持通配选择器 *",
    "env(": "WXSS 不支持 env()，安全区请用固定内边距",
    "constant(": "WXSS 不支持 constant()",
    ":root": "小程序用 page 而非 :root 定义变量",
}
banned = 0
for path in sorted(ROOT.rglob("*.wxss")):
    if any(part in SKIP for part in path.parts):
        continue
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        for token, why in FORBIDDEN.items():
            if token in line and not stripped.startswith("/*"):
                print(f"  ✗ {path.relative_to(ROOT)}:{i} 含 {token!r} —— {why}")
                banned += 1
    # 关键词出现在注释里也要提醒（避免"以为写了"）
for path in sorted(ROOT.rglob("*.wxss")):
    if any(part in SKIP for part in path.parts):
        continue
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("/*") and any(t in line for t in FORBIDDEN):
            print(f"  ! {path.relative_to(ROOT)}:{i} 注释中出现禁用关键字（仅提醒）")
if not banned:
    print("  OK 未使用通配选择器 / env() / constant() / :root")

sys.exit(1 if (problems or banned) else 0)
