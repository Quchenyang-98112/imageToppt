#!/usr/bin/env python3
"""Prepare a browser-equivalent export payload from saved analysis QA files."""

import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image


def data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def crop_asset(source: Image.Image, item: dict) -> str:
    scale_x, scale_y = source.width / 1600.0, source.height / 900.0
    padding = 10
    x = max(0, round((item["x"] - padding) * scale_x))
    y = max(0, round((item["y"] - padding) * scale_y))
    right = min(source.width, round((item["x"] + item["w"] + padding) * scale_x))
    bottom = min(source.height, round((item["y"] + item["h"] + padding) * scale_y))
    crop = source.crop((x, y, right, bottom)).convert("RGBA")
    pixels = np.asarray(crop).copy()
    brightness = pixels[:, :, :3].min(axis=2)
    alpha = np.where(brightness > 238, 0, np.where(brightness > 214, ((238 - brightness) / 24 * 255).astype(np.uint8), 255)).astype(np.uint8)
    pixels[:, :, 3] = alpha
    active = alpha > 24
    if not active.any():
        return data_url(crop)
    ys, xs = np.nonzero(active)
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    # Keep only the first artwork cluster when a blank gutter separates
    # accidental right-side bullet/text pixels from the icon.
    column_counts = active.sum(axis=0)
    gap = 0
    for column in range(left, right + 1):
        gap = gap + 1 if column_counts[column] == 0 else 0
        if gap >= max(8, round(pixels.shape[1] * .07)) and column < right - 6:
            right = column - gap
            break
    content = Image.fromarray(pixels).crop((left, top, right + 1, bottom + 1))
    output = Image.new("RGBA", (content.width + 24, content.height + 24), (0, 0, 0, 0))
    output.alpha_composite(content, (12, 12))
    return data_url(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--background", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    source = Image.open(args.source).convert("RGBA")
    elements = []
    for item in analysis.get("elements", []):
        if item.get("kind") in ("icon", "image"):
            item = dict(item)
            item["kind"] = "image"
            item["imageSrc"] = crop_asset(source, item)
        elements.append(item)
    background = Image.open(args.background).convert("RGB")
    payload = {"cleanBackground": data_url(background), "elements": elements}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
