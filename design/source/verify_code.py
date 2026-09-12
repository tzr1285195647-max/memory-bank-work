"""代码自检：JS 语法、WXML 标签配对、JSON 合法性、WXSS 括号平衡。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
SKIP_DIRS = {".git", "design", "docs", "node_modules", ".pytest_cache"}

js_files: list[Path] = []
wxml_files: list[Path] = []
json_files: list[Path] = []
wxss_files: list[Path] = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    suffix = path.suffix.lower()
    if suffix == ".js":
        js_files.append(path)
    elif suffix == ".wxml":
        wxml_files.append(path)
    elif suffix == ".json":
        json_files.append(path)
    elif suffix == ".wxss":
        wxss_files.append(path)

problems: list[str] = []

print("=== 1. JS 语法检查（node --check）===")
for path in sorted(js_files):
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    status = "OK" if result.returncode == 0 else "语法错误"
    print(f"  {path.relative_to(ROOT)!s:<48} {status}")
    if result.returncode != 0:
        problems.append(f"{path.relative_to(ROOT)}: {result.stderr.strip().splitlines()[-1] if result.stderr else ''}")

print("\n=== 2. JSON 合法性 ===")
for path in sorted(json_files):
    try:
        json.loads(path.read_text(encoding="utf-8"))
        print(f"  {path.relative_to(ROOT)!s:<48} OK")
    except json.JSONDecodeError as exc:
        print(f"  {path.relative_to(ROOT)!s:<48} 解析失败: {exc}")
        problems.append(f"{path.relative_to(ROOT)}: {exc}")

print("\n=== 3. WXML 标签配对 ===")
VOID = {"image", "input", "import", "include", "wxs"}
for path in sorted(wxml_files):
    text = path.read_text(encoding="utf-8")
    stack: list[str] = []
    broken = None
    for match in re.finditer(r"<(/?)([a-zA-Z][\w-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>", text):
        closing, tag, _attrs, self_closing = match.groups()
        if tag in VOID or self_closing:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                broken = f"</{tag}> 与 <{stack[-1] if stack else '空'}> 不匹配"
                break
            stack.pop()
        else:
            stack.append(tag)
    if broken is None and stack:
        broken = f"未闭合: {stack}"
    print(f"  {path.relative_to(ROOT)!s:<48} {'OK' if broken is None else broken}")
    if broken:
        problems.append(f"{path.relative_to(ROOT)}: {broken}")

print("\n=== 4. WXSS 括号平衡 ===")
for path in sorted(wxss_files):
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    depth = text.count("{") - text.count("}")
    print(f"  {path.relative_to(ROOT)!s:<48} {'OK' if depth == 0 else f'括号差 {depth}'}")
    if depth:
        problems.append(f"{path.relative_to(ROOT)}: 大括号差 {depth}")

print("\n" + "=" * 70)
print(f"JS {len(js_files)} / WXML {len(wxml_files)} / JSON {len(json_files)} / WXSS {len(wxss_files)}")
print(f"问题 {len(problems)} 个")
for item in problems:
    print(f"  ✗ {item}")
sys.exit(1 if problems else 0)
