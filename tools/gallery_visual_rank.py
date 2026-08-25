#!/usr/bin/env python3
"""Rank gallery candidates by source-crop appearance as well as semantic metadata.

The crop is used only as a retrieval query. It is never emitted as an asset.
"""
from __future__ import annotations

import argparse, json, math, re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def tokens(value: str) -> set[str]:
    return {x for x in re.split(r"[^a-z0-9\u4e00-\u9fff]+", value.lower()) if len(x) > 1}


def valid_box(box: Any, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if w <= 1 or h <= 1 or x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return round(x), round(y), max(2, round(w)), max(2, round(h))


def visual_features(image: Image.Image) -> tuple[np.ndarray, np.ndarray, float]:
    rgba = image.convert('RGBA')
    alpha = np.asarray(rgba.getchannel('A').resize((96, 96)), dtype=np.float32) / 255.0
    rgb = np.asarray(rgba.convert('RGB').resize((96, 96)), dtype=np.float32) / 255.0
    gray = rgb[..., 0] * .299 + rgb[..., 1] * .587 + rgb[..., 2] * .114
    gx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    gy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    edge = (gx + gy) * np.maximum(alpha, .2)
    hist = np.concatenate([np.histogram(rgb[..., c], bins=12, range=(0, 1), density=True)[0] for c in range(3)])
    ratio = image.width / max(1, image.height)
    return edge / max(1e-6, edge.sum()), hist / max(1e-6, hist.sum()), ratio


def visual_score(query: Image.Image, candidate: Image.Image) -> float:
    qe, qh, qr = visual_features(query)
    ce, ch, cr = visual_features(candidate)
    return feature_score((qe, qh, qr), (ce, ch, cr))


def feature_score(query, candidate) -> float:
    qe, qh, qr = query
    ce, ch, cr = candidate
    edge = max(0.0, 1.0 - float(np.mean(np.abs(qe - ce))) * 120.0)
    color = max(0.0, 1.0 - float(np.mean(np.abs(qh - ch))) * 14.0)
    aspect = math.exp(-abs(math.log(max(1e-6, qr / cr))) * 0.8)
    return .45 * edge + .35 * color + .20 * aspect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--audit', type=Path, required=True)
    ap.add_argument('--repository', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--top-k', type=int, default=0)
    ap.add_argument('--policy', type=Path, default=Path(__file__).resolve().parents[1] / 'config' / 'qwen-global-policy.json')
    args = ap.parse_args()
    policy = json.loads(args.policy.read_text(encoding='utf-8'))
    if policy.get('schema') != 'qwen-global-reconstruction-policy/v3':
        raise ValueError('gallery retrieval requires global policy v3')
    top_k = args.top_k or int(policy['gallery_policy']['candidate_top_k'])
    manifest = json.loads((args.repository / 'manifest.json').read_text(encoding='utf-8'))
    gallery = manifest.get('items', [])
    source = Image.open(args.source).convert('RGBA')
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    entries = list(audit.get('imagegenAssets', []))
    # Gallery feature extraction is done once per source page. Reopening 334
    # preview files for every icon was the old bottleneck.
    cached_gallery = []
    for item in gallery:
        preview = args.repository / str(item.get('category', '')) / str(item.get('preview', ''))
        if not preview.exists():
            continue
        try:
            cached_gallery.append((item, preview, visual_features(Image.open(preview))))
        except Exception:
            continue
    result: list[dict[str, Any]] = []
    for asset in entries:
        box = valid_box(asset.get('bbox'), source.width, source.height)
        if not box:
            continue
        x, y, w, h = box
        query_feature = visual_features(source.crop((x, y, x + w, y + h)))
        wanted = tokens(' '.join(str(asset.get(k, '')) for k in ('semantic', 'role', 'kind')))
        ranked = []
        for item, preview, candidate_feature in cached_gallery:
            metadata = tokens(' '.join([str(item.get('id', '')), str(item.get('category', '')), str(item.get('source_shape_name', '')), *[str(x) for x in item.get('keywords', [])]]))
            semantic = len(wanted & metadata) / max(1, len(wanted))
            try:
                visual = feature_score(query_feature, candidate_feature)
            except Exception:
                continue
            # Visual similarity dominates; metadata keeps icons in their intended family.
            score = .78 * visual + .22 * semantic
            ranked.append({'id': item.get('id'), 'category': item.get('category'), 'preview': str(preview), 'actualAssetKind': item.get('asset_kind'), 'assetPng': item.get('asset_png'), 'nativeSource': {'deck': item.get('source_deck'), 'slide': item.get('source_slide'), 'shapeId': item.get('source_shape_id')}, 'visualScore': round(visual, 4), 'semanticScore': round(semantic, 4), 'score': round(score, 4), 'previewIsRetrievalOnly': True})
        ranked.sort(key=lambda row: row['score'], reverse=True)
        result.append({'elementId': asset.get('id'), 'semantic': asset.get('semantic'), 'bbox': asset.get('bbox'), 'queryOnlyCrop': [x, y, w, h], 'sourceCropFinalUseForbidden': True, 'candidates': ranked[:max(1, top_k)]})
    payload = {'schema': 'gallery-visual-retrieval/v3', 'source': str(args.source), 'audit': str(args.audit), 'repository': str(args.repository), 'policy': policy['gallery_policy'], 'results': result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'completed', 'assets': len(result), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
