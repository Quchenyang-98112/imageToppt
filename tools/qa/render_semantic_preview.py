#!/usr/bin/env python3
"""Render a server analysis JSON as a quick 1600x900 semantic QA preview."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def font(size: int, bold: bool = False):
    candidates = ([r"C:\Windows\Fonts\msyhbd.ttc"] if bold else []) + FONT_CANDIDATES
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, max(6, int(size)))
    return ImageFont.load_default()


def colour(value, fallback=(0, 0, 0, 255)):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5)) + (255,)
    return fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--background", required=True)
    parser.add_argument("--source")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    canvas = Image.open(args.background).convert("RGBA").resize((1600, 900))
    draw = ImageDraw.Draw(canvas)
    for item in data.get("elements", []):
        kind = item.get("kind")
        x, y, w, h = (int(item.get(key, 0)) for key in ("x", "y", "w", "h"))
        if kind == "text":
            continue
        if kind in ("icon", "image"):
            # Preview the source crop in place; the browser performs alpha trim.
            original = Image.open(args.source).convert("RGBA") if args.source else None
            if original:
                sx, sy = original.width / 1600, original.height / 900
                crop = original.crop((int(x * sx), int(y * sy), int((x + w) * sx), int((y + h) * sy))).resize((max(1, w), max(1, h)))
                canvas.alpha_composite(crop, (x, y))
            else:
                draw.rectangle((x, y, x + w, y + h), outline=(0, 110, 230, 255), width=2)
            continue
        stroke = colour(item.get("stroke"), (0, 0, 0, 0))
        fill = colour(item.get("fill"), (255, 255, 255, 0))
        width = max(1, int(item.get("strokeWidth", 0)))
        if kind == "ellipse":
            draw.ellipse((x, y, x + w, y + h), fill=fill, outline=stroke, width=width)
        elif kind in ("line", "connector"):
            draw.line((x, y, x + w, y + max(1, h)), fill=stroke, width=width)
        else:
            radius = max(0, int(item.get("radius", 0)))
            draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=fill, outline=stroke, width=width)
    for item in data.get("elements", []):
        if item.get("kind") != "text":
            continue
        x, y, w, h = (int(item.get(key, 0)) for key in ("x", "y", "w", "h"))
        text = str(item.get("text") or "")
        text_font = font(int(item.get("fontSize", 20)), int(item.get("fontWeight", 400)) >= 600)
        fill = colour(item.get("color"), (0, 0, 0, 255))
        draw.multiline_text((x, y), text, font=text_font, fill=fill, spacing=2)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(args.output, quality=94)


if __name__ == "__main__":
    main()
