#!/usr/bin/env python3
"""Tighten approximate asset boxes using only Pillow and NumPy."""

import argparse
import colorsys
import json
import math
import sys
from collections import deque

import numpy as np
from PIL import Image


def hex_rgb(value: str) -> np.ndarray:
    value = (value or "#1670C5").lstrip("#")
    if len(value) != 6:
        value = "1670C5"
    return np.array([int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)], dtype=np.float32)


def component_gap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = max(0, max(ax, bx) - min(ax + aw, bx + bw))
    dy = max(0, max(ay, by) - min(ay + ah, by + bh))
    return math.hypot(dx, dy)


def connected_components(mask: np.ndarray):
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=np.uint8)
    components = []
    for yy, xx in zip(*np.nonzero(mask)):
        if seen[yy, xx]:
            continue
        queue = deque([(int(xx), int(yy))])
        seen[yy, xx] = 1
        left = right = int(xx)
        top = bottom = int(yy)
        area = 0
        while queue:
            cx, cy = queue.popleft()
            area += 1
            left, right = min(left, cx), max(right, cx)
            top, bottom = min(top, cy), max(bottom, cy)
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1),
                           (cx - 1, cy - 1), (cx + 1, cy - 1), (cx - 1, cy + 1), (cx + 1, cy + 1)):
                if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = 1
                    queue.append((nx, ny))
        if area >= 5:
            components.append((left, top, right - left + 1, bottom - top + 1, area))
    return components


def refine(image: np.ndarray, item: dict) -> dict:
    height, width = image.shape[:2]
    sx, sy = width / 1600.0, height / 900.0
    x, y = float(item.get("x", 0)) * sx, float(item.get("y", 0)) * sy
    w, h = max(2.0, float(item.get("w", 2)) * sx), max(2.0, float(item.get("h", 2)) * sy)
    if item.get("kind") == "image":
        return item
    pad_x, pad_y = max(28.0, w * 0.85), max(28.0, h * 0.9)
    rx1, ry1 = max(0, round(x - pad_x)), max(0, round(y - pad_y))
    rx2, ry2 = min(width, round(x + w + pad_x)), min(height, round(y + h + pad_y))
    roi = image[ry1:ry2, rx1:rx2].astype(np.float32)
    if roi.size == 0:
        return item
    target = hex_rgb(str(item.get("fill") or item.get("stroke") or "#1670C5"))
    target_h, target_s, _ = colorsys.rgb_to_hsv(*(target / 255.0))
    maximum = roi.max(axis=2)
    minimum = roi.min(axis=2)
    saturation = (maximum - minimum) / np.maximum(maximum, 1)
    distance = np.linalg.norm(roi - target, axis=2)
    if target_s >= .18:
        # Euclidean colour distance is stable for the red/blue/green flat
        # report pictograms used by generated business slides.
        threshold = 105 if target_s >= .55 else 78
        mask = (distance <= threshold) & (saturation >= max(.10, target_s * .22)) & (maximum >= 35)
    else:
        mask = (distance <= 70) & (maximum < 225)
    components = connected_components(mask)
    if not components:
        return item
    seed = (round(x) - rx1, round(y) - ry1, round(w), round(h))
    selected = []
    for component in components:
        cx, cy, cw, ch, area = component
        overlap_w = max(0, min(seed[0] + seed[2], cx + cw) - max(seed[0], cx))
        overlap_h = max(0, min(seed[1] + seed[3], cy + ch) - max(seed[1], cy))
        if overlap_w * overlap_h > 0 and (area >= 14 or overlap_w * overlap_h >= 6):
            selected.append(component)
    if not selected:
        center = (seed[0] + seed[2] / 2, seed[1] + seed[3] / 2)
        selected = [min(components, key=lambda c: math.hypot(c[0] + c[2] / 2 - center[0], c[1] + c[3] / 2 - center[1]))]
    threshold = max(10.0, min(24.0, max(w, h) * .14))
    changed = True
    while changed:
        changed = False
        for component in components:
            if component in selected or component[4] < 8:
                continue
            if min(component_gap(component[:4], current[:4]) for current in selected) <= threshold:
                selected.append(component)
                changed = True
    left, top = min(c[0] for c in selected) + rx1, min(c[1] for c in selected) + ry1
    right, bottom = max(c[0] + c[2] for c in selected) + rx1, max(c[1] + c[3] for c in selected) + ry1
    refined_w, refined_h = right - left, bottom - top
    original_center, refined_center = (x + w / 2, y + h / 2), ((left + right) / 2, (top + bottom) / 2)
    overlap_w = max(0.0, min(x + w, right) - max(x, left))
    overlap_h = max(0.0, min(y + h, bottom) - max(y, top))
    overlap_ratio = overlap_w * overlap_h / max(1.0, refined_w * refined_h)
    if (math.dist(original_center, refined_center) > max(w, h) * 1.05 or refined_w < w * .16 or
            refined_h < h * .16 or refined_w > w * 1.75 or refined_h > h * 1.75 or overlap_ratio < .08):
        return item
    gutter = max(2, round(min(sx, sy) * 3))
    left, top, right, bottom = max(0, left - gutter), max(0, top - gutter), min(width, right + gutter), min(height, bottom + gutter)
    result = dict(item)
    result.update({"x": round(left / sx), "y": round(top / sy), "w": max(2, round((right - left) / sx)), "h": max(2, round((bottom - top) / sy))})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--elements-json")
    parser.add_argument("--elements-file")
    args = parser.parse_args()
    image = np.asarray(Image.open(args.image).convert("RGB"))
    if not args.elements_json and not args.elements_file:
        parser.error("one of --elements-json or --elements-file is required")
    raw = args.elements_json if args.elements_json else open(args.elements_file, "r", encoding="utf-8").read()
    items = json.loads(raw)
    if isinstance(items, dict):
        items = [item for item in items.get("elements", []) if item.get("kind") in ("icon", "image")]
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([refine(image, item) for item in items], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
