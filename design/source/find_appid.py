"""查找本机微信开发者工具数据中的 AppID 与账号线索（只读扫描，不打印任何 token）。"""

from __future__ import annotations

import re
from pathlib import Path

DATA = Path(r"C:\Users\12851\AppData\Local\微信开发者工具\User Data")

APPID = re.compile(rb"wx[0-9a-f]{16}")
# 只为定位上下文，不打印内容
KEYWORDS = [b"appid", b"appId", b"AppID", b"nickName", b"nickname"]

hits: dict[str, set[str]] = {}
scanned = 0

for path in DATA.rglob("*"):
    if not path.is_file():
        continue
    try:
        if path.stat().st_size > 3_000_000:
            continue
    except OSError:
        continue
    scanned += 1
    try:
        blob = path.read_bytes()
    except OSError:
        continue
    for match in APPID.finditer(blob):
        appid = match.group().decode()
        context = blob[max(0, match.start() - 80) : match.end() + 80]
        label = "含关键字" if any(k in context for k in KEYWORDS) else "普通出现"
        hits.setdefault(appid, set()).add(f"{label} @ {path.name}")

print(f"扫描文件 {scanned} 个（跳过 >3MB）")
print(f"发现 AppID {len(hits)} 个：\n")
for appid, where in sorted(hits.items(), key=lambda kv: -len(kv[1])):
    print(f"  {appid}   出现 {len(where)} 处")
    for item in sorted(where)[:3]:
        print(f"      {item}")
