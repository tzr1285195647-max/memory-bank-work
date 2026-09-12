"""校验字体子集：字重、字符覆盖、是否包含界面必需的字。"""

from __future__ import annotations

import json
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(r"D:\记忆银行")
FONT_DIR = ROOT / "assets" / "fonts"
CHARSET = json.loads((ROOT / "design" / "source" / "font-charset.json").read_text(encoding="utf-8"))

REQUIRED = "记忆银行外婆的桂花树按下讲述今日叙事老街河流赶集与邻里0123456789:·"

for path in sorted(FONT_DIR.glob("*.ttf")):
    font = TTFont(path, lazy=True)
    cmap: dict[int, str] = {}
    for table in font["cmap"].tables:
        cmap.update(table.cmap)

    name_table = font["name"]
    family = ""
    subfamily = ""
    for record in name_table.names:
        if record.nameID == 1:
            family = family or str(record)
        if record.nameID == 2:
            subfamily = subfamily or str(record)

    os2 = font["OS/2"]
    weight_class = os2.usWeightClass

    charset = CHARSET["text"]
    missing_charset = [c for c in charset if ord(c) not in cmap]
    missing_required = [c for c in REQUIRED if ord(c) not in cmap]

    print(f"=== {path.name} ===")
    print(f"  字体名: {family} / {subfamily}")
    print(f"  usWeightClass: {weight_class}  （400=Regular, 700=Bold）")
    print(f"  字形数: {len(cmap)}")
    print(f"  子集缺失: {len(missing_charset)} 个 {missing_charset[:10]}")
    print(f"  界面必需字缺失: {len(missing_required)} 个 {missing_required}")
    print(f"  大小: {path.stat().st_size / 1024:.0f} KB")
    font.close()
