"""校验 tabBar 图标：格式、尺寸、体积是否满足微信小程序要求。

官方约束（常见踩坑点）：
  - 必须是 PNG/JPG 等受支持格式
  - 建议 81×81，最大不超过 40KB
  - 路径必须是相对小程序根目录、且不能带前导 ./
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
app = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(PNG_SIG):
        return None
    return struct.unpack(">II", data[16:24])


print("=== tabBar 配置检查 ===")
print(f"color={app['tabBar']['color']} selectedColor={app['tabBar']['selectedColor']}")
print(f"borderStyle={app['tabBar'].get('borderStyle')}  backgroundColor={app['tabBar'].get('backgroundColor')}")
print(f"条目数={len(app['tabBar']['list'])}\n")

problems = 0
for item in app["tabBar"]["list"]:
    print(f"--- {item['text']}  pagePath={item['pagePath']}")
    page_ok = (ROOT / f"{item['pagePath']}.js").exists() and (ROOT / f"{item['pagePath']}.json").exists()
    print(f"    页面文件存在: {page_ok}")
    if not page_ok:
        problems += 1
    for key in ("iconPath", "selectedIconPath"):
        rel = item[key]
        path = ROOT / rel
        if not path.exists():
            print(f"    ✗ {key}={rel} 文件缺失")
            problems += 1
            continue
        data = path.read_bytes()
        size = png_size(data)
        kb = len(data) / 1024
        issues = []
        if size is None:
            issues.append("不是合法 PNG")
        elif size != (81, 81):
            issues.append(f"尺寸 {size[0]}x{size[1]}（建议 81x81）")
        if kb > 40:
            issues.append(f"{kb:.1f}KB 超过 40KB 上限")
        if rel.startswith("./"):
            issues.append("路径不应以 ./ 开头")
        flag = "OK" if not issues else "；".join(issues)
        print(f"    {'✓' if not issues else '✗'} {key}={rel}  {size[0] if size else '?'}x{size[1] if size else '?'}  {kb:.1f}KB  {flag}")
        problems += len(issues)

print(f"\n=== 问题合计 {problems} 个 ===")
