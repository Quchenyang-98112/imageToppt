#!/usr/bin/env python3
"""Build a text-free slide base from authoritative OCR boxes.

This helper deliberately removes the *whole glyph region* and reconstructs the
low-frequency background from pixels immediately outside the OCR rectangle.
It does not try to identify one foreground colour, which is the reason the old
browser cleaner left anti-aliased strokes and white-text remnants behind.

The script never changes OCR text or geometry.  It only returns a cleaned PNG
and a small deterministic QA report for the server-side quality gate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def _clamp_rect(item: dict, sx: float, sy: float, width: int, height: int) -> tuple[int, int, int, int, int, int, int, int] | None:
    bx = float(item.get("x") or 0) * sx
    by = float(item.get("y") or 0) * sy
    bw = max(1.0, float(item.get("w") or 1) * sx)
    bh = max(1.0, float(item.get("h") or 1) * sy)

    core_x1 = max(0, min(width - 1, int(math.floor(bx))))
    core_y1 = max(0, min(height - 1, int(math.floor(by))))
    core_x2 = max(core_x1 + 1, min(width, int(math.ceil(bx + bw))))
    core_y2 = max(core_y1 + 1, min(height, int(math.ceil(by + bh))))
    if core_x2 <= core_x1 or core_y2 <= core_y1:
        return None

    # OCR rectangles normally hug visible glyph pixels.  The asymmetric extra
    # room catches antialiasing and descenders without consuming neighbouring
    # icons or card borders.
    pad_x = max(2, min(10, int(round(bh * 0.10))))
    pad_y = max(2, min(9, int(round(bh * 0.18))))
    x1 = max(0, core_x1 - pad_x)
    y1 = max(0, core_y1 - pad_y)
    x2 = min(width, core_x2 + pad_x)
    y2 = min(height, core_y2 + pad_y)
    return x1, y1, x2, y2, core_x1, core_y1, core_x2, core_y2


def _median_band(image: np.ndarray, axis: str, position: int, start: int, end: int, inside: bool) -> np.ndarray:
    height, width = image.shape[:2]
    band = 3
    if axis == "row":
        if inside:
            a, b = max(0, position), min(height, position + band)
        else:
            a, b = max(0, position - band), min(height, position)
        if b <= a:
            a, b = max(0, min(height - 1, position)), max(1, min(height, position + 1))
        return np.median(image[a:b, start:end], axis=0)
    if inside:
        a, b = max(0, position), min(width, position + band)
    else:
        a, b = max(0, position - band), min(width, position)
    if b <= a:
        a, b = max(0, min(width - 1, position)), max(1, min(width, position + 1))
    return np.median(image[start:end, a:b], axis=1)


def _background_patch(image: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    """Fit a robust low-frequency RGB plane from the frame around the box.

    Direct left/right interpolation turns a red bullet or one surviving glyph
    into a long coloured streak.  Iteratively rejecting high-residual frame
    pixels makes bullets, rules and neighbouring labels statistical outliers.
    """
    height, width = image.shape[:2]
    region_h, region_w = y2 - y1, x2 - x1
    margin = max(7, min(24, int(round(min(region_h, region_w) * 0.32))))
    ex1, ey1 = max(0, x1 - margin), max(0, y1 - margin)
    ex2, ey2 = min(width, x2 + margin), min(height, y2 + margin)
    yy, xx = np.mgrid[ey1:ey2, ex1:ex2]
    frame = (xx < x1) | (xx >= x2) | (yy < y1) | (yy >= y2)
    sample_x = xx[frame].astype(np.float64)
    sample_y = yy[frame].astype(np.float64)
    sample_rgb = image[ey1:ey2, ex1:ex2][frame].astype(np.float64)

    if len(sample_rgb) < 12:
        colour = np.median(sample_rgb, axis=0) if len(sample_rgb) else np.array([255.0, 255.0, 255.0])
        return np.broadcast_to(colour, (region_h, region_w, 3)).copy()

    cx = (ex1 + ex2) / 2.0
    cy = (ey1 + ey2) / 2.0
    scale = max(1.0, float(max(ex2 - ex1, ey2 - ey1)))
    design = np.column_stack((np.ones(len(sample_x)), (sample_x - cx) / scale, (sample_y - cy) / scale))
    keep = np.ones(len(sample_x), dtype=bool)
    coefficients = np.zeros((3, 3), dtype=np.float64)
    for _ in range(5):
        coefficients, *_ = np.linalg.lstsq(design[keep], sample_rgb[keep], rcond=None)
        predicted = design @ coefficients
        residual = np.linalg.norm(sample_rgb - predicted, axis=1)
        cutoff = max(5.0, float(np.quantile(residual[keep], 0.68)))
        next_keep = residual <= cutoff
        if next_keep.sum() < 12 or np.array_equal(next_keep, keep):
            break
        keep = next_keep

    target_y, target_x = np.mgrid[y1:y2, x1:x2]
    target_design = np.column_stack((
        np.ones(region_h * region_w),
        (target_x.reshape(-1) - cx) / scale,
        (target_y.reshape(-1) - cy) / scale,
    ))
    patch = (target_design @ coefficients).reshape(region_h, region_w, 3)
    return np.clip(patch, 0, 255)


def _edge_energy(region: np.ndarray) -> float:
    if region.shape[0] < 2 or region.shape[1] < 2:
        return 0.0
    gray = region[..., 0] * 0.299 + region[..., 1] * 0.587 + region[..., 2] * 0.114
    dx = np.abs(np.diff(gray, axis=1)).mean()
    dy = np.abs(np.diff(gray, axis=0)).mean()
    return float(dx + dy)


def _otsu_threshold(values: np.ndarray) -> float:
    if not values.size:
        return 18.0
    clipped = np.clip(values, 0, 255).astype(np.uint8)
    histogram = np.bincount(clipped, minlength=256).astype(np.float64)
    total = histogram.sum()
    cumulative = np.cumsum(histogram)
    cumulative_mean = np.cumsum(histogram * np.arange(256))
    global_mean = cumulative_mean[-1]
    denominator = cumulative * (total - cumulative)
    score = np.zeros(256, dtype=np.float64)
    valid = denominator > 0
    score[valid] = ((global_mean * cumulative[valid] - cumulative_mean[valid] * total) ** 2) / denominator[valid]
    return float(np.argmax(score))


def _grow_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    grown = mask.copy()
    height, width = mask.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            src_y1, src_y2 = max(0, -dy), min(height, height - dy)
            src_x1, src_x2 = max(0, -dx), min(width, width - dx)
            dst_y1, dst_y2 = src_y1 + dy, src_y2 + dy
            dst_x1, dst_x2 = src_x1 + dx, src_x2 + dx
            grown[dst_y1:dst_y2, dst_x1:dst_x2] |= mask[src_y1:src_y2, src_x1:src_x2]
    return grown


def clean(image: np.ndarray, elements: list[dict]) -> tuple[np.ndarray, dict]:
    result = image.astype(np.float32).copy()
    height, width = image.shape[:2]
    sx, sy = width / 1600.0, height / 900.0
    rects = []
    for item in elements:
        if item.get("kind") != "text" or not str(item.get("text") or "").strip():
            continue
        rect = _clamp_rect(item, sx, sy, width, height)
        if rect:
            rects.append((rect, str(item.get("id") or "")))

    # Large regions first; later tight boxes can repair any remaining glyph
    # edge without reintroducing source pixels.
    rects.sort(key=lambda entry: (entry[0][2] - entry[0][0]) * (entry[0][3] - entry[0][1]), reverse=True)
    before_total = 0.0
    after_total = 0.0
    reports = []
    for rect, element_id in rects:
        x1, y1, x2, y2, cx1, cy1, cx2, cy2 = rect
        before = result[cy1:cy2, cx1:cx2].copy()
        patch = _background_patch(result, x1, y1, x2, y2)
        current = result[y1:y2, x1:x2]
        residual = np.linalg.norm(current - patch, axis=2)
        core = np.zeros(residual.shape, dtype=bool)
        core[cy1 - y1:cy2 - y1, cx1 - x1:cx2 - x1] = True
        core_values = residual[core]
        median = float(np.median(core_values)) if core_values.size else 0.0
        mad = float(np.median(np.abs(core_values - median))) if core_values.size else 0.0
        threshold = max(10.0, _otsu_threshold(core_values), median + 2.8 * max(1.0, mad))
        glyph = core & (residual >= threshold)
        raw_coverage = float(glyph.sum() / max(1, core.sum()))
        # Two pixels catch subpixel antialiasing, but dilation is restricted to
        # the OCR rectangle plus its safety fringe, never the whole card.
        allowed = _grow_mask(core, 2)
        glyph = _grow_mask(glyph, 2) & allowed
        cleaned_region = current.copy()
        cleaned_region[glyph] = patch[glyph]
        result[y1:y2, x1:x2] = cleaned_region
        after = result[cy1:cy2, cx1:cx2]
        before_energy = _edge_energy(before)
        after_energy = _edge_energy(after)
        before_total += before_energy
        after_total += after_energy
        reports.append({
            "id": element_id,
            "rect": [x1, y1, x2 - x1, y2 - y1],
            "beforeEdge": round(before_energy, 3),
            "afterEdge": round(after_energy, 3),
            "threshold": round(threshold, 3),
            "maskCoverage": round(raw_coverage, 4),
        })

    ratio = after_total / max(0.001, before_total)
    suspicious = [item for item in reports if item["maskCoverage"] < 0.004 or item["maskCoverage"] > 0.72]
    report = {
        "engine": "ocr-region-background-reconstruction-v1",
        "regions": len(reports),
        "beforeEdge": round(before_total, 3),
        "afterEdge": round(after_total, 3),
        "residualRatio": round(ratio, 4),
        "maxAfterEdge": round(max((item["afterEdge"] for item in reports), default=0.0), 3),
        "suspiciousRegions": len(suspicious),
        "passed": bool(reports) and not suspicious,
        "items": reports,
    }
    return np.clip(np.rint(result), 0, 255).astype(np.uint8), report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--elements-json")
    source.add_argument("--elements-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    elements = json.loads(args.elements_file.read_text(encoding="utf-8-sig") if args.elements_file else args.elements_json)
    if isinstance(elements, dict):
        elements = elements.get("elements", [])
    with Image.open(args.image) as opened:
        source = np.asarray(opened.convert("RGB"))
    cleaned, report = clean(source, elements)
    Image.fromarray(cleaned, mode="RGB").save(args.output, format="PNG", optimize=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
