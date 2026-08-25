#!/usr/bin/env python3
"""Deterministically propose non-text visual candidates before local VLM review.

This is intentionally not an asset extractor.  It uses source pixels plus the
OCR exclusion mask to produce auditable source-pixel boxes.  Qwen3-VL receives
only these candidates and decides semantic class / gallery eligibility.
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def clamp_box(box, width, height):
    x, y, w, h = [int(round(float(v))) for v in box]
    x = max(0, min(x, width - 1)); y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x)); h = max(1, min(h, height - y))
    return x, y, w, h


def expand(box, width, height, amount):
    x, y, w, h = clamp_box(box, width, height)
    left, top = max(0, x - amount), max(0, y - amount)
    right, bottom = min(width, x + w + amount), min(height, y + h + amount)
    return left, top, right - left, bottom - top


def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    iw = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    ih = max(0, min(ay + ah, by + bh) - max(ay, by))
    return iw * ih / max(1, aw * ah + bw * bh - iw * ih)


def components(mask):
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=np.uint8)
    result = []
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if visited[y, x]:
            continue
        visited[y, x] = 1
        q = deque([(x, y)])
        left = right = x; top = bottom = y; count = 0
        while q:
            cx, cy = q.popleft(); count += 1
            left, right = min(left, cx), max(right, cx)
            top, bottom = min(top, cy), max(bottom, cy)
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1), (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1), (cx + 1, cy + 1)):
                if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = 1; q.append((nx, ny))
        result.append((left, top, right - left + 1, bottom - top + 1, count))
    return result


def text_overlap(box, text_boxes):
    x, y, w, h = box; area = max(1, w * h); covered = 0
    for tx, ty, tw, th in text_boxes:
        covered += max(0, min(x + w, tx + tw) - max(x, tx)) * max(0, min(y + h, ty + th) - max(y, ty))
    return min(1.0, covered / area)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--ocr-record', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--max-candidates', type=int, default=48)
    args = ap.parse_args()
    image = np.asarray(Image.open(args.image).convert('RGB'))
    height, width = image.shape[:2]
    ocr = json.loads(args.ocr_record.read_text(encoding='utf-8'))
    text_boxes = [clamp_box(line['bbox'], width, height) for line in ocr.get('lines', []) if isinstance(line, dict) and isinstance(line.get('bbox'), list)]
    exclusion = np.zeros((height, width), dtype=bool)
    for box in text_boxes:
        x, y, w, h = expand(box, width, height, max(3, min(10, round(box[3] * .15))))
        exclusion[y:y + h, x:x + w] = True

    rgb = image.astype(np.int16)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    gray = (rgb[:, :, 0] * 30 + rgb[:, :, 1] * 59 + rgb[:, :, 2] * 11) // 100
    gradient = np.maximum(np.abs(np.diff(gray, axis=1, prepend=gray[:, :1])), np.abs(np.diff(gray, axis=0, prepend=gray[:1, :])))
    active = ((chroma >= 26) | (gradient >= 30)) & ~exclusion
    # Merge anti-aliased strokes into their host, while preserving separated icons.
    merged = Image.fromarray((active * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))
    merged_mask = np.asarray(merged) > 0
    boxes = []
    for x, y, w, h, pixel_count in components(merged_mask):
        if w < 18 or h < 18 or pixel_count < 18:
            continue
        if w > width * .98 and h > height * .98:
            continue
        original_active = float(active[y:y + h, x:x + w].mean())
        overlap = text_overlap((x, y, w, h), text_boxes)
        # text hosts remain relevant native candidates, but text-only fragments are not.
        if overlap > .86 and original_active < .12:
            continue
        boxes.append({'bbox': [x, y, w, h], 'activeRatio': round(original_active, 4), 'ocrOverlap': round(overlap, 4), 'componentPixels': pixel_count})

    boxes.sort(key=lambda row: (row['bbox'][1], row['bbox'][0], -(row['bbox'][2] * row['bbox'][3])))
    selected = []
    for row in boxes:
        box = row['bbox']
        if any(iou(box, prior['bbox']) >= .72 for prior in selected):
            continue
        x, y, w, h = box
        fill_like = row['activeRatio'] >= .42
        role_hint = 'host_or_native_geometry' if fill_like or row['ocrOverlap'] >= .15 else 'icon_or_complex_visual'
        row.update({
            'id': f'candidate-{len(selected) + 1:03d}',
            'sourceBBox': box,
            'roleHint': role_hint,
            'localCropBBox': list(expand(box, width, height, 12)),
            'requiresLocalVision': True,
            'status': 'proposed',
        })
        selected.append(row)
        if len(selected) >= args.max_candidates:
            break
    selected.sort(key=lambda row: (row['sourceBBox'][1], row['sourceBBox'][0]))
    for index, row in enumerate(selected, 1):
        row['id'] = f'candidate-{index:03d}'
    payload = {
        'schema': 'nontext-candidate-inventory/v1',
        'source': {'path': str(args.image.resolve()), 'width': width, 'height': height},
        'ocrRecord': str(args.ocr_record.resolve()),
        'coordinateContract': '[x,y,w,h] source pixels',
        'detector': {'name': 'ocr_masked_color_edge_connected_components', 'maxCandidates': args.max_candidates},
        'candidates': selected,
        'summary': {'proposed': len(selected), 'ocrExclusionBoxes': len(text_boxes), 'requiresLocalVision': len(selected)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'completed', 'candidates': len(selected), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
