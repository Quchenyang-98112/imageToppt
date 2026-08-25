#!/usr/bin/env python3
"""Refine approximate VLM text boxes against the source slide pixels.

This is a measurement step only: it never draws or alters a final visual.
It uses the model's text/color/rough box as a prior, then finds the matching
glyph-color extent in a generously bounded local search region.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def rgb(value: str) -> np.ndarray:
    value = (value or '#202020').lstrip('#')
    if len(value) != 6:
        value = '202020'
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)


def expected_width(item: dict) -> float:
    text = str(item.get('text') or '')
    lines = text.split('\n') or ['']
    chars = max((len(line.replace(' ', '')) for line in lines), default=1)
    line_height = max(2.0, float(item.get('h') or 20) / max(1, len(lines)))
    factor = .80 if line_height >= 46 else .66
    return max(float(item.get('w') or 2), chars * line_height * factor)


def refine(image: np.ndarray, item: dict, sx: float, sy: float) -> dict:
    height, width = image.shape[:2]
    x = float(item.get('x') or 0) * sx
    y = float(item.get('y') or 0) * sy
    model_w = max(2.0, float(item.get('w') or 2))
    # Keep the pixel search anchored to the VLM box. The former unbounded text-
    # length estimate could absorb a neighboring label with the same color.
    w = min(expected_width(item), model_w * 1.18) * sx
    h = max(2.0, float(item.get('h') or 20) * sy)
    # A modest anti-aliasing/search gutter is enough; large gutters merge nearby
    # metric values, units and card labels into one false bounding box.
    pad_x = max(6, min(18, round(h * .24)))
    pad_y = max(4, min(12, round(h * .18)))
    left, top = max(0, int(x - pad_x)), max(0, int(y - pad_y))
    right, bottom = min(width, int(x + w + pad_x)), min(height, int(y + h + pad_y))
    if right - left < 3 or bottom - top < 3:
        return item

    sample = image[top:bottom, left:right].astype(np.int16)
    target = rgb(str(item.get('color') or '#202020'))
    brightness = float(target.mean())
    distance = np.sqrt(((sample - target) ** 2).sum(axis=2))
    # White text cannot be segmented from a white page reliably; retain its VLM box.
    if brightness > 220:
        return item
    tolerance = 72 if max(target) - min(target) > 35 else 58
    mask = distance <= tolerance
    if brightness < 90:
        # Anti-aliased dark glyphs remain dark even if the predicted black is imperfect.
        mask |= sample.max(axis=2) < 125
    ys, xs = np.where(mask)
    if len(xs) < max(18, int((right - left) * (bottom - top) * .003)):
        return item
    min_x, max_x = int(xs.min()) + left, int(xs.max()) + left + 1
    min_y, max_y = int(ys.min()) + top, int(ys.max()) + top + 1
    # Reject a full-region match: it means this color belongs to a panel, not glyphs.
    if (max_x - min_x) * (max_y - min_y) > (right - left) * (bottom - top) * .88:
        return item
    # Convert from original image pixels back to the editor's 1600x900 space.
    result = dict(item)
    result['x'] = round(min_x / sx)
    result['y'] = round(min_y / sy)
    result['w'] = max(2, round((max_x - min_x) / sx))
    result['h'] = max(2, round((max_y - min_y) / sy))
    return result


def main() -> None:
    # Keep the Python -> Node JSON boundary UTF-8 on every Windows code page.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--elements-json', required=True)
    args = parser.parse_args()
    elements = json.loads(args.elements_json)
    with Image.open(args.image) as opened:
        image = np.array(opened.convert('RGB'))
    sx, sy = image.shape[1] / 1600.0, image.shape[0] / 900.0
    print(json.dumps([refine(image, item, sx, sy) for item in elements], ensure_ascii=False))


if __name__ == '__main__':
    main()
