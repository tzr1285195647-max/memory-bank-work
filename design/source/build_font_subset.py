"""从思源宋体可变字体生成小程序用的静态子集字体。

步骤：
  1. 用 varLib.instancer 把可变字体实例化为静态字重（400 / 700）
  2. 按 design/source/font-charset.json 做子集化
  3. 输出 TTF 到 assets/fonts/（后端会把它作为静态资源提供）

设计稿全稿标的是 Lora，但 Lora 没有中文字形，中文实际是 fallback 渲染；
思源宋体（Noto Serif SC）是同源观感最接近的开源选择（SIL OFL，可商用）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(r"D:\记忆银行")
SOURCE_VF = Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf")
CHARSET = ROOT / "design" / "source" / "font-charset.json"
OUT_DIR = ROOT / "assets" / "fonts"

# 字重轴名（Noto Serif SC VF 的轴是 wght）
WEIGHTS = {"Regular": 400, "Bold": 700}


def build_static(weight: int, target: Path) -> Path:
    font = TTFont(SOURCE_VF, lazy=False)
    instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)
    static_path = target.with_suffix(".static.ttf")
    font.save(static_path)
    font.close()
    return static_path


def build_subset(static_path: Path, chars: str, target: Path) -> None:
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.drop_tables = ["DSIG"]
    options.flavor = None  # 输出未压缩 TTF，兼容性最好

    font = subset.load_font(str(static_path), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=chars)
    subsetter.subset(font)
    subset.save_font(font, str(target), options)
    font.close()


def main() -> None:
    if not SOURCE_VF.exists():
        raise SystemExit(f"找不到源字体：{SOURCE_VF}")

    payload = json.loads(CHARSET.read_text(encoding="utf-8"))
    chars = payload["text"]
    print(f"字符集：{payload['totalChars']} 个（其中设计稿与代码中出现 {payload['designChars']} 个）")
    print(f"源字体：{SOURCE_VF.name}（{SOURCE_VF.stat().st_size / 1024 / 1024:.1f} MB，可变字体）\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, weight in WEIGHTS.items():
        target = OUT_DIR / f"NotoSerifSC-{name}-subset.ttf"
        print(f"--- {name} (wght={weight}) ---")
        static_path = build_static(weight, target)
        print(f"  静态实例：{static_path.stat().st_size / 1024 / 1024:.2f} MB")
        build_subset(static_path, chars, target)
        static_path.unlink(missing_ok=True)
        size_kb = target.stat().st_size / 1024
        print(f"  子集输出：{target.name}  {size_kb:.0f} KB")

    print("\n=== 输出目录 ===")
    for path in sorted(OUT_DIR.glob("*.ttf")):
        print(f"  {path.name:<34} {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
