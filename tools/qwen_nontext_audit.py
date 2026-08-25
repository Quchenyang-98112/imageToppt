#!/usr/bin/env python3
"""Qwen-only non-text inventory pass with missing-first review."""
from __future__ import annotations

import argparse, base64, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image

from qwen_env import load_project_env


# Standalone executors must behave like the Next.js runtime, which loads the
# project-local .env.local automatically.  Never print loaded values.
load_project_env(Path(__file__))


def env_value(name: str) -> str:
    return os.getenv(name, '').strip()


def image_url(path: Path) -> str:
    mime = 'image/jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else 'image/png'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def parse_json(value: str):
    candidate = value.strip()
    if candidate.startswith(chr(96) * 3):
        candidate = candidate.strip(chr(96)).replace('json\n', '', 1).strip()
    start, end = candidate.find('{'), candidate.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('Qwen returned no JSON object')
    return json.loads(candidate[start:end + 1])


def bbox(raw, width: int, height: int, coordinate_system: str, bbox_encoding: str):
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x, y, a, b = [float(v) for v in raw]
    except Exception:
        return None
    if coordinate_system == 'normalized_1000':
        x, y, a, b = x * width / 1000.0, y * height / 1000.0, a * width / 1000.0, b * height / 1000.0
    if bbox_encoding == 'xyxy':
        w, h = a - x, b - y
    elif bbox_encoding == 'xywh':
        w, h = a, b
    else:
        return None
    if w <= 1 or h <= 1:
        return None
    x = max(0.0, min(x, width - 2.0)); y = max(0.0, min(y, height - 2.0))
    w = max(2.0, min(w, width - x)); h = max(2.0, min(h, height - y))
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def normalize(items, width: int, height: int, classification: str, coordinate_system: str, bbox_encoding: str):
    out = []
    for i, raw in enumerate(items if isinstance(items, list) else []):
        if not isinstance(raw, dict):
            continue
        box = bbox(raw.get('bbox'), width, height, coordinate_system, bbox_encoding)
        if not box:
            continue
        contains_ocr = raw.get('containsOcr')
        if not isinstance(contains_ocr, list):
            contains_ocr = []
        out.append({
            'id': str(raw.get('id') or f'{classification}-{i + 1:03d}'),
            'kind': str(raw.get('kind') or ('icon' if classification == 'imagegen_asset' else 'rectangle')),
            'classification': classification,
            'role': str(raw.get('role') or ('asset' if classification == 'imagegen_asset' else 'container')),
            'bbox': box,
            'raw_bbox': raw.get('bbox'),
            'raw_bbox_coordinate_system': coordinate_system,
            'raw_bbox_encoding': bbox_encoding,
            'zIndex': int(raw.get('zIndex') or 0),
            'fill': raw.get('fill') or raw.get('color') or raw.get('fillColor'),
            'stroke': raw.get('stroke') or raw.get('lineColor'),
            'strokeWidth': raw.get('strokeWidth'),
            'radius': raw.get('radius'),
            'opacity': raw.get('opacity'),
            'rotation': raw.get('rotation'),
            'containsOcr': contains_ocr[:40],
            'semantic': str(raw.get('semantic') or raw.get('name') or '')[:240],
            'colorRoles': raw.get('colorRoles') or {'primary': '#FFFFFF', 'background': 'transparent'},
            'confidence': float(raw.get('confidence') or 0.0),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--ocr-record', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--round', type=int, default=1)
    args = parser.parse_args()
    image = args.image.resolve()
    ocr = json.loads(args.ocr_record.read_text(encoding='utf-8'))
    with Image.open(image) as opened:
        width, height = opened.size
    key = env_value('DASHSCOPE_VISION_API_KEY') or env_value('DASHSCOPE_API_KEY')
    if not key:
        raise RuntimeError('DASHSCOPE_API_KEY is required')
    base = (env_value('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation').rstrip('/')
    model = env_value('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus'
    inventory = [{'id': x.get('id'), 'bbox': x.get('bbox')} for x in ocr.get('lines', []) if isinstance(x, dict)]
    prompt = f'''You are the non-text route's strict source-pixel layout auditor for an editable PowerPoint reconstruction. Inspect the entire source image at EXACTLY {width}x{height} pixels. OCR owns text; do not emit text content or create a text object.

This is a mandatory global policy. Treat all visible non-text as an inventory problem before choosing an asset: scan top-to-bottom and left-to-right for (1) page background/edge decoration, (2) cards/panels/pills/badges and their borders/shadows, (3) lines, arrows, chevrons, connectors and flow bands, (4) charts/diagram hosts, (5) logos, icons and pictograms, (6) repeated small glyphs, and (7) bottom navigation/footers. Do not omit a major foreground object because it contains text.

Raw visual geometry contract: return every bbox in the coordinate system "normalized_1000" and encoding "xyxy", i.e. [left,top,right,bottom] where the source width maps to 1000 and source height maps to 1000. The pipeline will deterministically convert it to canonical source-pixel [x,y,w,h]. You MUST put top-level values "coordinateSystem":"normalized_1000" and "bboxEncoding":"xyxy" in the JSON. Do not guess a whole-slide or oversized bbox for a small icon. If uncertain, set confidence below 0.85 and state why in review, but still give the best measured bbox. Use the original visual centroid and dimensions; every major object needs a separate bbox and zIndex.

Classification: nativeObjects only for editable rectangles/roundRects/circles/simple polygons/lines/chevrons and chart hosts. imagegenAssets for logos, icons, pictograms, compound ribbon flows, illustrations, dense chart artwork. A source crop is only a query, never a final asset. Preserve source background as pageBackground/edgeDecoration inventory; foreground will be masked with clean patches behind editable replacements.

Return ONLY JSON with keys coordinateSystem, bboxEncoding, nativeObjects, imagegenAssets, review. Each native object needs id, kind, role, bbox, fill, stroke, strokeWidth, radius, opacity, zIndex, containsOcr, semantic, colorRoles, confidence. Each asset needs id, kind, role, bbox, zIndex, containsOcr, semantic, colorRoles, confidence. review must contain missingFirst (every suspected missing major object), majorNontextCount, majorRecall, coordinateContractPassed, backgroundRegions, reviewScore and notes. Do not return a full-slide screenshot.

OCR inventory for avoiding text duplication: {json.dumps(inventory, ensure_ascii=False, separators=(',', ':'))}'''
    # The native multimodal endpoint is used intentionally.  The compatible
    # OpenAI endpoint has intermittently reset connections in this workspace,
    # which silently weakened the independent visual audit.
    payload = {
        'model': model,
        'input': {'messages': [{'role': 'user', 'content': [
            {'text': prompt},
            {'image': image_url(image)},
        ]}]},
        'parameters': {'result_format': 'message', 'max_tokens': 12000},
    }
    request = Request(base, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
    started = time.time()
    with urlopen(request, timeout=300) as response:
        reply = json.loads(response.read().decode('utf-8'))
    parts = (((reply.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or []
    content = next((part.get('text') for part in parts if isinstance(part, dict) and part.get('text')), '')
    value = parse_json(content)
    coordinate_system = str(value.get('coordinateSystem') or '')
    bbox_encoding = str(value.get('bboxEncoding') or '')
    if coordinate_system != 'normalized_1000' or bbox_encoding != 'xyxy':
        raise ValueError(f'Coordinate contract violation: expected normalized_1000/xyxy, got {coordinate_system}/{bbox_encoding}')
    result = {
        'schema': 'qwen-nontext-audit/v2',
        'source': str(image),
        'source_width': width,
        'source_height': height,
        'provider': {'name': 'dashscope-native-multimodal', 'model': model},
        'round': args.round,
        'bbox_contract': '[x,y,w,h] canonical source pixels; deterministically converted from declared normalized_1000/xyxy',
        'raw_bbox_contract': 'normalized_1000/xyxy declared by Qwen',
        'nativeObjects': normalize(value.get('nativeObjects'), width, height, 'native_editable', coordinate_system, bbox_encoding),
        'imagegenAssets': normalize(value.get('imagegenAssets'), width, height, 'imagegen_asset', coordinate_system, bbox_encoding),
        'review': value.get('review') if isinstance(value.get('review'), dict) else {},
        'elapsedMs': round((time.time() - started) * 1000),
    }
    result['review'].setdefault('missingFirst', [])
    result['review'].setdefault('majorNontextCount', 0)
    result['review'].setdefault('reviewScore', 0.0)
    result['review'].setdefault('majorRecall', 0.0)
    result['review'].setdefault('coordinateContractPassed', False)
    result['review'].setdefault('backgroundRegions', [])
    result['review'].setdefault('notes', '')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'completed', 'output': str(args.output), 'nativeObjects': len(result['nativeObjects']), 'imagegenAssets': len(result['imagegenAssets']), 'reviewScore': result['review']['reviewScore']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
