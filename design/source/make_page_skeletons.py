"""生成 9 个页面的最小骨架（占位内容，完整页面属于 P0-2）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"D:\记忆银行")
PAGES = ROOT / "pages"

# (目录名, 设计稿编号, 标题, 顶栏主题, 是否 tabBar 页)
SPECS = [
    ("welcome", "01", "欢迎页", "brand", False),
    ("role", "02", "身份选择", "accent", False),
    ("login", "03", "登录", "brand", False),
    ("home", "04", "长辈首页", "brandLight", True),
    ("topic", "05", "主题选择", "brandLight", False),
    ("record", "06", "录音讲述", "accent", True),
    ("story-preview", "07", "故事预览", "brand", False),
    ("family", "08", "家庭看板", "brandLight", False),
    ("stories", "09", "故事书", "brand", True),
    ("profile", "10", "个人中心", "brand", True),
]

COMPONENT_JSON = {
    "usingComponents": {
        "nav-bar": "../../components/nav-bar/index",
    },
    "navigationStyle": "custom",
}

for folder, number, title, theme, is_tab in SPECS:
    target = PAGES / folder
    if folder == "record":
        # 录音页同时是 tabBar 页，需要底部让位类
        pass
    target.mkdir(parents=True, exist_ok=True)

    (target / "index.json").write_text(
        json.dumps(COMPONENT_JSON, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    page_class = "page page--tab" if is_tab else "page"
    (target / "index.wxml").write_text(
        f"""<nav-bar title="记忆银行" theme="{theme}" showBack="{{{{false}}}}" />

<view class="{page_class} container">
  <view class="placeholder">
    <text class="placeholder__no">{number}</text>
    <text class="placeholder__title">{title}</text>
    <text class="placeholder__hint">页面骨架已注册，完整实现属于 P0-2</text>
  </view>
</view>
""",
        encoding="utf-8",
    )

    (target / "index.wxss").write_text(
        """.placeholder {
  padding: 80px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.placeholder__no {
  font-size: 34px;
  font-weight: 700;
  color: var(--accent);
  font-family: var(--font-latin);
}

.placeholder__title {
  font-size: 25px;
  font-weight: 700;
  color: var(--text-primary);
  margin-top: 12px;
}

.placeholder__hint {
  font-size: 15px;
  color: var(--text-muted);
  margin-top: 8px;
}
""",
        encoding="utf-8",
    )

    (target / "index.js").write_text(
        f"""Page({{
  data: {{
    designNo: '{number}',
    title: '{title}',
  }},
}});
""",
        encoding="utf-8",
    )
    print(f"  pages/{folder}/index.*  ({number} {title}{' · tab' if is_tab else ''})")

print(f"\n共 {len(SPECS)} 个页面骨架已生成")
