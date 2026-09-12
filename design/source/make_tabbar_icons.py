"""生成 tabBar 图标 PNG（81×81）。

设计稿底栏图标是文本字形（⌂ ◉ ▤ ◎），而原生 tabBar 只接受图片，因此在此落成 PNG。
字形取自 C:\\Windows\\Fonts\\seguisym.ttf，颜色按设计稿实测值：
选中 #4E6657 / 未选中 #747A73。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(r"C:\Windows\Fonts\seguisym.ttf")
OUT_DIR = Path(r"D:\记忆银行\assets\tabbar")

SIZE = 81
GLYPH_BOX = 54  # 字形目标高度
SELECTED = "#4E6657"
UNSELECTED = "#747A73"
REFERENCE_FONT_SIZE = 200  # 用于测量字形比例的参考字号

ICONS = {
    "home": "⌂",
    "record": "◉",
    "story": "▤",
    "mine": "◎",
}


def glyph_span(char: str) -> int:
    """参考字号下字形包围盒的长边（用于等比换算目标字号）。"""
    font = ImageFont.truetype(str(FONT_PATH), REFERENCE_FONT_SIZE)
    left, top, right, bottom = font.getbbox(char)
    return max(right - left, bottom - top)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 统一字号：以四个字形里最大的为准，保证视觉大小一致
    span = max(glyph_span(char) for char in ICONS.values())
    font_size = int(GLYPH_BOX * REFERENCE_FONT_SIZE / span)
    font = ImageFont.truetype(str(FONT_PATH), font_size)

    print(f"参考跨度 {span}（字号 {REFERENCE_FONT_SIZE}）-> 目标字号 {font_size}")
    print(f"输出目录 {OUT_DIR}\n")

    for name, char in ICONS.items():
        for suffix, color in (("", UNSELECTED), ("-active", SELECTED)):
            image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            # anchor="mm" 以字形中心对齐画布中心
            draw.text((SIZE / 2, SIZE / 2), char, font=font, fill=color, anchor="mm")
            path = OUT_DIR / f"{name}{suffix}.png"
            image.save(path)
            left, top, right, bottom = font.getbbox(char)
            print(
                f"  {path.name:<22} {SIZE}x{SIZE}  {color}  "
                f"字形 {right - left}x{bottom - top}"
            )


if __name__ == "__main__":
    main()
