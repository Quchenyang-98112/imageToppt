#!/usr/bin/env python3
"""Build a deterministic-first BG_CLEAN candidate from a v3 foreground inventory.

The tool never changes pixels outside the union removal mask. Complex regions are
reported for masked Qwen Image escalation rather than silently declared clean.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

REMOVABLE = {
    'ocr_text', 'native_editable', 'library_native', 'library_svg', 'library_png',
    'exact_brand_asset', 'qwen_image_asset', 'decorative_movable',
}


def parse_box(item: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int] | None:
    raw = item.get('sourceBBox') or item.get('bbox')
    if isinstance(raw, dict): raw = [raw.get('x'), raw.get('y'), raw.get('w'), raw.get('h')]
    if not isinstance(raw, list) or len(raw) != 4: return None
    try: x, y, w, h = [float(v) for v in raw]
    except (TypeError, ValueError): return None
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width + .5 or y + h > height + .5: return None
    expansion = max(2, min(8, round(h * .08)))
    left = max(0, int(np.floor(x)) - expansion); top = max(0, int(np.floor(y)) - expansion)
    right = min(width, int(np.ceil(x + w)) + expansion); bottom = min(height, int(np.ceil(y + h)) + expansion)
    return left, top, right, bottom


def merge_rectangles(rectangles: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    result: list[tuple[int, int, int, int]] = []
    for rect in sorted(rectangles, key=lambda r: (r[1], r[0])):
        current = rect; changed = True
        while changed:
            changed = False; keep = []
            for other in result:
                if current[0] <= other[2] + 2 and current[2] + 2 >= other[0] and current[1] <= other[3] + 2 and current[3] + 2 >= other[1]:
                    current = (min(current[0], other[0]), min(current[1], other[1]), max(current[2], other[2]), max(current[3], other[3])); changed = True
                else: keep.append(other)
            result = keep
        result.append(current)
    return result


def ring_pixels(rgb: np.ndarray, mask: np.ndarray, rect: tuple[int, int, int, int], ring: int = 12) -> np.ndarray:
    x1, y1, x2, y2 = rect; h, w = mask.shape
    ox1, oy1, ox2, oy2 = max(0, x1-ring), max(0, y1-ring), min(w, x2+ring), min(h, y2+ring)
    region = rgb[oy1:oy2, ox1:ox2]; valid = ~mask[oy1:oy2, ox1:ox2]
    return region[valid]


def gradient_fill(shape: tuple[int, int], top: np.ndarray, bottom: np.ndarray, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    h, w = shape; yy = np.linspace(0, 1, max(1, h))[:, None, None]; xx = np.linspace(0, 1, max(1, w))[None, :, None]
    vertical = top[None, None, :] * (1-yy) + bottom[None, None, :] * yy
    horizontal = left[None, None, :] * (1-xx) + right[None, None, :] * xx
    return np.clip((vertical + horizontal) / 2, 0, 255)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--inventory', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--mask-output', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    args = ap.parse_args()
    source_image = Image.open(args.source).convert('RGB'); rgb = np.asarray(source_image, dtype=np.float32).copy(); height, width = rgb.shape[:2]
    payload = json.loads(args.inventory.read_text(encoding='utf-8')); items = payload.get('elements') or payload.get('inventory') or payload
    if not isinstance(items, list): raise ValueError('inventory must contain an elements/inventory array')
    rectangles=[]; invalid=[]; fixed=[]
    for item in items:
        if not isinstance(item, dict): continue
        classification = str(item.get('reconstructionClass') or item.get('classification') or '')
        if classification == 'decorative_fixed': fixed.append(str(item.get('id') or item.get('elementId') or 'fixed')); continue
        if classification not in REMOVABLE: continue
        rect = parse_box(item, width, height)
        if rect: rectangles.append(rect)
        else: invalid.append(str(item.get('id') or item.get('elementId') or 'unknown'))
    merged = merge_rectangles(rectangles); mask = np.zeros((height, width), dtype=bool)
    for x1,y1,x2,y2 in merged: mask[y1:y2,x1:x2] = True
    clean = rgb.copy(); regions=[]; unresolved=[]
    for index, rect in enumerate(merged, 1):
        x1,y1,x2,y2=rect; samples=ring_pixels(rgb,mask,rect)
        if len(samples) < 16:
            unresolved.append(index); regions.append({'id':index,'bbox':[x1,y1,x2-x1,y2-y1],'method':'needs_qwen_masked_local_edit','reason':'insufficient_sampling_ring'}); continue
        median=np.median(samples,axis=0); variance=float(np.mean(np.std(samples,axis=0)))
        if variance <= 10:
            fill=np.broadcast_to(median,(y2-y1,x2-x1,3)); method='solid_color'
        elif variance <= 34:
            top=np.mean(rgb[max(0,y1-4):y1,max(0,x1-4):min(width,x2+4)],axis=(0,1)) if y1 else median
            bottom=np.mean(rgb[y2:min(height,y2+4),max(0,x1-4):min(width,x2+4)],axis=(0,1)) if y2<height else median
            left=np.mean(rgb[max(0,y1-4):min(height,y2+4),max(0,x1-4):x1],axis=(0,1)) if x1 else median
            right=np.mean(rgb[max(0,y1-4):min(height,y2+4),x2:min(width,x2+4)],axis=(0,1)) if x2<width else median
            fill=gradient_fill((y2-y1,x2-x1),np.nan_to_num(top,nan=median),np.nan_to_num(bottom,nan=median),np.nan_to_num(left,nan=median),np.nan_to_num(right,nan=median)); method='gradient_fit'
        else:
            fill=np.broadcast_to(median,(y2-y1,x2-x1,3)); method='needs_qwen_masked_local_edit'; unresolved.append(index)
        local=mask[y1:y2,x1:x2]; target=clean[y1:y2,x1:x2]; target[local]=fill[local]; clean[y1:y2,x1:x2]=target
        regions.append({'id':index,'bbox':[x1,y1,x2-x1,y2-y1],'method':method,'ringStd':round(variance,3)})
    output=np.clip(clean,0,255).astype(np.uint8); args.output.parent.mkdir(parents=True,exist_ok=True); Image.fromarray(output).save(args.output)
    args.mask_output.parent.mkdir(parents=True,exist_ok=True); Image.fromarray((mask*255).astype(np.uint8),'L').save(args.mask_output)
    outside=~mask; identity=float(np.mean(np.all(output[outside]==np.asarray(source_image)[outside],axis=1))) if outside.any() else 1.0
    report={'schema':'bg-clean-build/v3','source':str(args.source.resolve()),'output':str(args.output.resolve()),'mask':str(args.mask_output.resolve()),'sourceSize':[width,height],'removedRegions':regions,'fixedDecorationIds':fixed,'invalidElementIds':invalid,'outsideMaskPixelIdentity':round(identity,6),'unresolvedRegionIds':unresolved,'status':'needs_qwen_masked_local_edit' if unresolved or invalid else 'deterministic_candidate_ready','ocrRescanRequired':True,'wholeSlideRegenerated':False}
    args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'status':report['status'],'regions':len(regions),'unresolved':len(unresolved),'outsideIdentity':report['outsideMaskPixelIdentity']},ensure_ascii=False))

if __name__ == '__main__': main()
