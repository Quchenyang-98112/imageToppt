#!/usr/bin/env python3
"""Generate one source-grounded local asset with Qwen Image.

The source crop is a reference only. The resulting isolated asset is never
inserted until the separate qwen_image_asset_audit.py gate approves it.
"""
from __future__ import annotations

import argparse, base64, io, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from qwen_env import load_project_env

load_project_env(Path(__file__))


def data_url(path: Path) -> str:
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def parse_response(value: dict) -> str:
    content = (((value.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or []
    image = next((item.get('image') for item in content if isinstance(item, dict) and item.get('image')), None)
    if not image:
        raise RuntimeError(str((value.get('error') or {}).get('message') or 'Qwen Image returned no image URL'))
    return str(image)


def isolate(image: Image.Image) -> Image.Image:
    rgba = image.convert('RGBA')
    arr = np.asarray(rgba).copy()
    rgb = arr[:, :, :3].astype(np.int16)
    # Prefer the requested magenta key but tolerate a near-white sheet only by
    # treating it as an unapproved result in the report, not as a valid asset.
    magenta = (rgb[:, :, 0] > 180) & (rgb[:, :, 2] > 150) & (rgb[:, :, 1] < 130) & ((rgb[:, :, 0] - rgb[:, :, 1]) > 70)
    arr[:, :, 3][magenta] = 0
    alpha = arr[:, :, 3]
    ys, xs = np.where(alpha > 20)
    if len(xs) == 0:
        raise RuntimeError('generated asset has no foreground after chroma removal')
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    crop = Image.fromarray(arr[y0:y1, x0:x1], mode='RGBA')
    side = max(crop.width, crop.height) + max(24, round(max(crop.width, crop.height) * .24))
    canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--bbox', required=True, help='x,y,w,h in source pixels')
    ap.add_argument('--semantic', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    x, y, w, h = [round(float(value)) for value in args.bbox.split(',')]
    source = Image.open(args.source).convert('RGB')
    crop = source.crop((max(0, x), max(0, y), min(source.width, x + max(2, w)), min(source.height, y + max(2, h))))
    crop_path = args.output.with_suffix('.source.png')
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path)
    key = os.getenv('DASHSCOPE_IMAGE_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    if not key:
        raise RuntimeError('Qwen Image credential is unavailable')
    model = os.getenv('DASHSCOPE_IMAGE_EDIT_MODEL') or 'qwen-image-2.0-pro'
    endpoint = os.getenv('DASHSCOPE_IMAGE_EDIT_ENDPOINT') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
    prompt = f'''Reconstruct exactly one isolated foreground PowerPoint visual from the attached source crop. Semantic description: {args.semantic}. Preserve the source silhouette, internal structure, stroke weight, palette, orientation, proportions, and local visual identity. Do not redesign, beautify, simplify, substitute a synonym icon, or add any other objects. The crop is a visual reference only; do not include readable text, labels, numbers, watermark, card background, border, or surrounding slide. Place the single asset centered on a flat pure magenta #FF00FF chroma-key background with generous empty margins. Return one flat icon/illustration only.'''
    payload = {'model': model, 'input': {'messages': [{'role': 'user', 'content': [{'image': data_url(crop_path)}, {'text': prompt}]}]}, 'parameters': {'n': 1, 'size': '1328*1328'}}
    started = time.time()
    request = Request(endpoint, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=300) as response:
        image_url = parse_response(json.loads(response.read().decode('utf-8')))
    with urlopen(image_url, timeout=300) as response:
        generated = Image.open(io.BytesIO(response.read())).convert('RGBA')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    isolate(generated).save(args.output)
    report = {'schema': 'qwen-image-local-asset/v1', 'status': 'generated_pending_audit', 'model': model, 'source': str(args.source), 'sourceCrop': str(crop_path), 'sourceBBox': [x, y, w, h], 'semantic': args.semantic, 'output': str(args.output), 'elapsedMs': round((time.time() - started) * 1000)}
    args.output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
