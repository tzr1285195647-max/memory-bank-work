"""把 flex gap 换成兼容性更好的 margin 写法，并输出改动清单。

小程序的 flex gap 在部分基础库/低版本安卓 webview 上不生效，
这里统一改为「容器不设 gap + 子项 margin-bottom / margin-right」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(r"D:\记忆银行\pages")

# (相对 wxss 路径, 需要替换的 gap 规则, 追加的子项 margin 规则)
EDITS = [
    (
        "topic/index.wxss",
        ".topic__list {\n  margin-top: 46px;\n  display: flex;\n  flex-direction: column;\n  gap: 23px;\n}",
        ".topic__list {\n  margin-top: 46px;\n  display: flex;\n  flex-direction: column;\n}\n\n"
        ".topic__list > * {\n  margin-bottom: 23px;\n}",
    ),
    (
        "stories/index.wxss",
        ".stories__list {\n  margin-top: 24px;\n  display: flex;\n  flex-direction: column;\n  gap: 24px;\n}",
        ".stories__list {\n  margin-top: 24px;\n  display: flex;\n  flex-direction: column;\n}\n\n"
        ".stories__list > * {\n  margin-bottom: 24px;\n}",
    ),
    (
        "profile/index.wxss",
        ".profile__stats {\n  margin-top: 16px;\n  display: flex;\n  gap: 16px;\n}",
        ".profile__stats {\n  margin-top: 16px;\n  display: flex;\n}\n\n"
        ".profile__stat + .profile__stat {\n  margin-left: 16px;\n}",
    ),
    (
        "profile/index.wxss",
        ".profile__rows {\n  margin-top: 16px;\n  display: flex;\n  flex-direction: column;\n  gap: 14px;\n}",
        ".profile__rows {\n  margin-top: 16px;\n  display: flex;\n  flex-direction: column;\n}\n\n"
        ".profile__rows > * {\n  margin-bottom: 14px;\n}",
    ),
    (
        "record/index.wxss",
        ".record__modes {\n  margin-top: 90px;\n  display: flex;\n  justify-content: center;\n  gap: 26px;\n}",
        ".record__modes {\n  margin-top: 90px;\n  display: flex;\n  justify-content: center;\n}\n\n"
        ".record__mode + .record__mode {\n  margin-left: 26px;\n}",
    ),
    (
        "record/index.wxss",
        ".record__actions {\n  margin-top: 60px;\n  display: flex;\n  flex-direction: column;\n  gap: 16px;\n}",
        ".record__actions {\n  margin-top: 60px;\n  display: flex;\n  flex-direction: column;\n}\n\n"
        ".record__actions > * {\n  margin-bottom: 16px;\n}",
    ),
    (
        "story-preview/index.wxss",
        ".preview__actions {\n  margin-top: 32px;\n  padding-bottom: 40px;\n  display: flex;\n  flex-direction: column;\n  gap: 14px;\n}",
        ".preview__actions {\n  margin-top: 32px;\n  padding-bottom: 40px;\n  display: flex;\n  flex-direction: column;\n}\n\n"
        ".preview__actions > * {\n  margin-bottom: 14px;\n}",
    ),
    (
        "login/index.wxss",
        ".login__links {\n  margin-top: 32px;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  gap: 28px;\n}",
        ".login__links {\n  margin-top: 32px;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n}\n\n"
        ".login__link + .login__link {\n  margin-top: 28px;\n}",
    ),
]

changed = 0
for rel, old, new in EDITS:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"  ！未匹配（跳过）: {rel}")
        continue
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    changed += 1
    print(f"  ✓ {rel}")

print(f"\n已修改 {changed} 处")
print("\n=== 复查：是否还有残留 gap ===")
for path in ROOT.rglob("*.wxss"):
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"\bgap\s*:", line):
            print(f"  {path.relative_to(ROOT)}:{i}: {line.strip()}")
print("（无输出表示已清理干净）")
