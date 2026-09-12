"""查找能渲染底栏字形（⌂ ◉ ▤ ◎）的本机字体，为生成 tabBar 图标做准备。"""

from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont

GLYPHS = {
    "home": "⌂",   # U+2302
    "record": "◉",  # U+25C9
    "story": "▤",   # U+25A4
    "mine": "◎",   # U+25CE
}
CODES = {name: ord(char) for name, char in GLYPHS.items()}

candidates = [
    Path(r"C:\Windows\Fonts\seguisym.ttf"),
    Path(r"C:\Windows\Fonts\SegoeUISymbol.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    Path(r"C:\Windows\Fonts\Deng.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\seguiemj.ttf"),
]

for path in candidates:
    if not path.exists():
        print(f"缺失: {path.name}")
        continue
    try:
        font = TTFont(path, fontNumber=0, lazy=True)
        cmap: dict[int, str] = {}
        for table in font["cmap"].tables:
            cmap.update(table.cmap)
        have = {name: code in cmap for name, code in CODES.items()}
        missing = [name for name, ok in have.items() if not ok]
        status = "全部支持" if not missing else f"缺少 {missing}"
        print(f"{path.name:<20} {status}")
        font.close()
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name:<20} 读取失败: {type(exc).__name__}: {exc}")
