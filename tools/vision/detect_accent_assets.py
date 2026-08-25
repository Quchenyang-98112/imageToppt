#!/usr/bin/env python3
"""Detect small gold accent assets inside semantic red header/footer hosts."""

import argparse
import json
import sys
from collections import deque

import numpy as np
from PIL import Image


def components(mask):
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=np.uint8)
    output = []
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
        output.append((left, top, right - left + 1, bottom - top + 1, area))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--elements-file", required=True)
    args = parser.parse_args()
    image = np.asarray(Image.open(args.image).convert("RGB"))
    data = json.load(open(args.elements_file, "r", encoding="utf-8-sig"))
    height, width = image.shape[:2]
    sx, sy = width / 1600.0, height / 900.0
    found = []
    for index, item in enumerate(data):
        name = str(item.get("name", "")).lower()
        fill = str(item.get("fill", "")).upper()
        if not any(token in name for token in ("section", "header", "footer", "summary", "target", "judgment")):
            continue
        if fill not in ("#C00000", "#C60000", "#CC0000", "#D00000", "#D21012", "#D32026"):
            continue
        x, y = max(0, round(float(item.get("x", 0)) * sx)), max(0, round(float(item.get("y", 0)) * sy))
        x2 = min(width, round((float(item.get("x", 0)) + float(item.get("w", 0))) * sx))
        y2 = min(height, round((float(item.get("y", 0)) + float(item.get("h", 0))) * sy))
        if x2 <= x or y2 <= y:
            continue
        roi = image[y:y2, x:x2]
        red, green, blue = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
        gold = (red >= 170) & (green >= 90) & (green <= 230) & (blue <= 135) & ((red.astype(np.int16) - blue.astype(np.int16)) >= 70)
        candidates = []
        for left, top, w, h, area in components(gold):
            if area < 18 or w < 5 or h < 5 or w > 90 or h > 90:
                continue
            ratio = w / max(1, h)
            if ratio < .35 or ratio > 2.8:
                continue
            # Report headers put their gold star/accent near the leading edge.
            # Restrict detection to that zone so anti-aliased text elsewhere in
            # the red bar can never become a false asset.
            if left + w / 2 > min(190 * sx, (x2 - x) * .28):
                continue
            candidates.append((left, top, w, h, area))
        if candidates:
            left, top, w, h, area = max(candidates, key=lambda component: component[4])
            found.append({
                "id": f"detected-gold-accent-{index}-{len(found)}",
                "kind": "icon",
                "name": f"{item.get('name', 'semantic')}-gold-accent-asset",
                "role": "icon",
                "containsOcr": [],
                "x": round((x + left - 3) / sx),
                "y": round((y + top - 3) / sy),
                "w": max(8, round((w + 6) / sx)),
                "h": max(8, round((h + 6) / sy)),
                "fill": "#F5B746",
                "stroke": "#F5B746",
                "strokeWidth": 0,
                "opacity": 1,
                "sourceElement": False,
            })
    # De-duplicate adjacent detections caused by overlapping parent/label hosts.
    unique = []
    for item in found:
        cx, cy = item["x"] + item["w"] / 2, item["y"] + item["h"] / 2
        if any(abs(cx - (old["x"] + old["w"] / 2)) < 12 and abs(cy - (old["y"] + old["h"] / 2)) < 12 for old in unique):
            continue
        unique.append(item)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(unique[:12], ensure_ascii=False))


if __name__ == "__main__":
    main()
