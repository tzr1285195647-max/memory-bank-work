"""从开发者工具日志中找出 WXSS / WXML 编译错误的具体内容。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.home() / "AppData/Local/微信开发者工具/User Data"
logs = sorted(ROOT.glob("*/WeappLog/logs/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)

print(f"最近日志 {len(logs)} 个，逐个搜索编译错误：\n")
found = 0
for log in logs[:4]:
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    hits = [
        ln
        for ln in lines
        if re.search(r"wxss|WXSS|wxml|WXML|编译|compile error|CompileError|样式", ln, re.IGNORECASE)
        and re.search(r"error|错误|fail|失败|invalid|unexpected", ln, re.IGNORECASE)
    ]
    if not hits:
        continue
    found += len(hits)
    print(f"=== {log.name}（{len(hits)} 条）===")
    for ln in hits[-15:]:
        # 只保留时间之后的正文，去掉 asar 路径噪声
        cleaned = re.sub(r"\[[A-Z]\]\[[^\]]*\]\[[^\]]*\]", "", ln)
        cleaned = re.sub(r"C:\\Program Files[^)]*\)", "", cleaned)
        print(f"  {cleaned.strip()[:400]}")
    print()

if not found:
    print("未在日志中找到 WXSS/WXML 编译错误。改为搜索全部含「编译」的最近行：\n")
    latest = logs[0]
    for ln in latest.read_text(encoding="utf-8", errors="replace").splitlines():
        if "编译" in ln or "compile" in ln.lower():
            cleaned = re.sub(r"\[[A-Z]\]\[[^\]]*\]\[[^\]]*\]", "", ln)
            cleaned = re.sub(r"C:\\Program Files[^)]*\)", "", cleaned)
            print(f"  {cleaned.strip()[:300]}")
