#!/usr/bin/env python3
"""Compare generated local assets against source crops with Qwen-VL."""
from __future__ import annotations

import argparse, base64, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from qwen_env import load_project_env

load_project_env(Path(__file__))


def data_url(path: Path) -> str:
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def parse(value: str) -> dict:
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('Qwen-VL returned no JSON')
    return json.loads(value[start:end + 1])


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument('--source', type=Path, required=True); ap.add_argument('--plan', type=Path, required=True); ap.add_argument('--output', type=Path, required=True); args = ap.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8')); rows = plan.get('assets') or []
    source = Image.open(args.source)
    content = [{'type': 'text', 'text': '''Compare each SOURCE CROP and GENERATED ASSET pair. The generated asset must preserve the same semantic identity, silhouette, internal structure, orientation, palette and stroke style. Reject extra objects, missing structure, hallucinated text, card/panel backgrounds, wrong direction, wrong logo mark, severe deformation or unrelated icons. Return only JSON: {"reviews":[{"id":"","pass":true,"visualSimilarity":0..1,"structuralVetoes":[],"reason":""}]}. A pass requires visualSimilarity >= 0.88 and no structural vetoes; exact acceptance target is >= 0.95.'''}]
    for row in rows:
        bbox = [round(float(value)) for value in row['sourceBBox']]; x, y, w, h = bbox
        crop_path = args.output.parent / f"source-crop-{row['id']}.png"; source.crop((x, y, x + w, y + h)).save(crop_path)
        content += [{'type': 'text', 'text': f"ASSET {row['id']} semantic={row.get('semantic','')}"}, {'type': 'image_url', 'image_url': {'url': data_url(crop_path), 'min_pixels': 4096, 'max_pixels': 400000}}, {'type': 'image_url', 'image_url': {'url': data_url(Path(row['generatedPath'])), 'min_pixels': 4096, 'max_pixels': 400000}}]
    key = os.getenv('DASHSCOPE_API_KEY')
    if not key: raise RuntimeError('DASHSCOPE_API_KEY is required')
    base = (os.getenv('DASHSCOPE_VISION_BASE_URL') or 'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/')
    payload = {'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus', 'temperature': 0, 'enable_thinking': False, 'response_format': {'type': 'json_object'}, 'max_completion_tokens': 5000, 'messages': [{'role': 'user', 'content': content}]}
    started = time.time(); request = Request(base + '/chat/completions', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=300) as response: value = parse(((json.loads(response.read().decode()).get('choices') or [{}])[0].get('message') or {}).get('content') or '')
    by_id = {str(row.get('id')): row for row in value.get('reviews', []) if isinstance(row, dict)}
    reviews = []
    for row in rows:
        review = by_id.get(str(row['id']), {}); score = float(review.get('visualSimilarity') or 0); vetoes = review.get('structuralVetoes') if isinstance(review.get('structuralVetoes'), list) else []
        reviews.append({**row, 'visualSimilarity': score, 'structuralVetoes': vetoes, 'pass': bool(review.get('pass')) and score >= .88 and not vetoes, 'reason': str(review.get('reason') or 'missing review')})
    result = {'schema': 'qwen-image-asset-audit/v1', 'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus', 'elapsedMs': round((time.time() - started) * 1000), 'reviews': reviews, 'passed': bool(reviews) and all(row['pass'] for row in reviews)}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'); print(json.dumps({'status': 'passed' if result['passed'] else 'needs_review', 'assets': len(reviews), 'approved': sum(row['pass'] for row in reviews)}, ensure_ascii=False))
    if not result['passed']: raise SystemExit(2)


if __name__ == '__main__': main()
