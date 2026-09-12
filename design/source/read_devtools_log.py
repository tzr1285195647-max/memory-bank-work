"""从微信开发者工具日志中抽取编译错误，并判断当前工程是否编译通过。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.home() / "AppData/Local/微信开发者工具/User Data"
logs = sorted(
    ROOT.glob("*/WeappLog/logs/*.log"), key=lambda p: p.stat().st_mtime, reverse=True
)
log = logs[0]
print(f"日志文件: {log.name}  ({log.stat().st_size / 1024:.0f} KB)\n")

text = log.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

print("=" * 72)
print("1. 提到本工程的日志行（含 记忆银行 / memory-bank / D:\\记忆银行）")
print("=" * 72)
project_hits = [ln for ln in lines if re.search(r"记忆银行|memory-bank|D:\\\\记忆银行|D:/记忆银行", ln)]
print(f"共 {len(project_hits)} 行，展示后 12 行：")
for ln in project_hits[-12:]:
    print(f"  {ln[:260]}")

print()
print("=" * 72)
print("2. ERROR / WARN 行（完整内容）")
print("=" * 72)
errors = [ln for ln in lines if re.search(r"\[(ERROR|WARN)\]", ln)]
print(f"共 {len(errors)} 行：")
for ln in errors[-20:]:
    print(f"  {ln}")

print()
print("=" * 72)
print("3. 编译产物与小程序编译相关关键字")
print("=" * 72)
for keyword in [
    "编译成功",
    "compile success",
    "CompileError",
    "WXML",
    "WXSS",
    "app.json",
    "pages/welcome",
    "components/nav-bar",
    "appservice",
    "simulator",
]:
    count = sum(1 for ln in lines if keyword in ln)
    print(f"  {keyword:<22} 出现 {count} 行")

print()
print("=" * 72)
print("4. 判定")
print("=" * 72)
fatal = [
    ln
    for ln in errors
    if re.search(r"编译|compile|WXML|WXSS|app\.json|页面|component", ln, re.IGNORECASE)
]
if fatal:
    print("发现疑似编译相关错误：")
    for ln in fatal[-10:]:
        print(f"  {ln[:300]}")
else:
    print("未发现与编译（WXML/WXSS/app.json/页面/组件）相关的错误。")
    print("日志中仅有的 ERROR 行如下（用于人工判断是否与工程有关）：")
    for ln in errors[-5:]:
        print(f"  {ln[:300]}")
