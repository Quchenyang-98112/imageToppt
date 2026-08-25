#!/usr/bin/env python3
"""Measure OCR-line typography directly from source pixels.

The OCR service owns text and glyph bounds.  This deterministic pass estimates
foreground color and adds just enough vertical room for browser/PowerPoint font
metrics, following Knight's tight-bbox and text-fit principles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def dominant_quantized(pixels: np.ndarray) -> np.ndarray:
    if not len(pixels):
        return np.array([32, 32, 32], dtype=np.float32)
    quantized = (pixels // 12).astype(np.int16)
    keys, counts = np.unique(quantized, axis=0, return_counts=True)
    return keys[int(np.argmax(counts))].astype(np.float32) * 12 + 5.5


def style_line(image: np.ndarray, item: dict, sx: float, sy: float) -> dict:
    height, width = image.shape[:2]
    bx = float(item.get('x') or 0)
    by = float(item.get('y') or 0)
    bw = max(2.0, float(item.get('w') or 2))
    bh = max(2.0, float(item.get('h') or 2))
    x1 = max(0, int(round(bx * sx)))
    y1 = max(0, int(round(by * sy)))
    x2 = min(width, max(x1 + 2, int(round((bx + bw) * sx))))
    y2 = min(height, max(y1 + 2, int(round((by + bh) * sy))))
    crop = image[y1:y2, x1:x2].astype(np.int16)
    result = dict(item)
    if crop.size:
        border = np.concatenate((crop[:1].reshape(-1, 3), crop[-1:].reshape(-1, 3), crop[:, :1].reshape(-1, 3), crop[:, -1:].reshape(-1, 3)))
        background = dominant_quantized(border)
        flat = crop.reshape(-1, 3).astype(np.float32)
        distance = np.linalg.norm(flat - background, axis=1)
        foreground = flat[distance > max(34.0, float(np.percentile(distance, 58)))]
        if len(foreground) >= 4:
            color = dominant_quantized(foreground)
            result['color'] = '#%02X%02X%02X' % tuple(np.clip(np.rint(color), 0, 255).astype(int))

    # OCR boxes hug glyphs. CSS/PPT line boxes include ascender/descender room.
    # Expand vertically but keep the visible glyph baseline in the same place.
    font_px = max(8, round(bh * .82))
    safe_h = max(bh + 2, font_px * 1.22)
    extra = safe_h - bh
    result['y'] = max(0, round(by - extra * .35))
    result['h'] = min(900 - result['y'], max(2, round(safe_h)))
    result['x'] = max(0, round(bx - min(2.0, bw * .01)))
    result['w'] = min(1600 - result['x'], max(2, round(bw + min(5.0, bw * .025))))
    result['fontSize'] = font_px
    result['fontWeight'] = 700 if bh >= 31 else 600 if bh >= 23 else 400
    result['align'] = 'left'
    return result


def main() -> None:
    # Knight contract: helpers own their stdout encoding.  On Windows the
    # inherited console code page is commonly GBK, while Node decodes child
    # output as UTF-8.  Without this, valid OCR Chinese becomes U+FFFD (�).
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--elements-json', required=True)
    args = parser.parse_args()
    elements = json.loads(args.elements_json)
    with Image.open(args.image) as opened:
        image = np.asarray(opened.convert('RGB'))
    sx, sy = image.shape[1] / 1600.0, image.shape[0] / 900.0
    print(json.dumps([style_line(image, item, sx, sy) for item in elements], ensure_ascii=False))


if __name__ == '__main__':
    main()
