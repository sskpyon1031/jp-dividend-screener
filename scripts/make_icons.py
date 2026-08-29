#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PWA / iOS 用アイコン PNG を生成する。

    python scripts/make_icons.py

docs/icon.svg と同じ意匠(緑背景に白い棒グラフ＋%)を、中央 62% の
安全領域に収めて描画する(Android の maskable クロップ対策)。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs"
BG = (14, 124, 102)      # #0e7c66
FG = (255, 255, 255)


def render(size: int) -> Image.Image:
    scale = 4                      # 4倍で描いて縮小 = アンチエイリアス
    s = size * scale
    u = s / 512                    # 512 座標系 -> 実ピクセル
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)

    bar_w = 46 * u
    for x, y_top in ((168, 300), (236, 228), (304, 150)):
        x *= u
        d.rounded_rectangle(
            [x, y_top * u, x + bar_w, 360 * u], radius=12 * u, fill=FG
        )

    try:
        font = ImageFont.load_default(size=int(150 * u))
    except TypeError:                # 古い Pillow 用フォールバック
        font = ImageFont.load_default()
    d.text((s / 2, 150 * u), "%", font=font, fill=FG, anchor="mm")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    for size, name in ((180, "icon-180.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        render(size).save(OUT / name, optimize=True)
        print("wrote", name)


if __name__ == "__main__":
    main()
