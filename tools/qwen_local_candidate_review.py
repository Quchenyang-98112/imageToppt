#!/usr/bin/env python3
"""Verify detector-proposed non-text candidates with Qwen3-VL local crops.

Unlike a whole-slide inventory, every response is grounded to stable candidate
IDs and an existing source-pixel box.  OCR remains text authority.
"""
from __future__ import annotations

import argparse, base64, io, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw
from qwen_env import load_project_env


load_project_env(Path(__file__))


def data_url(image: Image.Image) -> str:
    out = io.BytesIO(); image.save(out, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


def parse_json(text: str) -> dict:
    value = text.strip()
    fence = re.search(r'```(?:json)?\s*([\s\S]*?)```', value, re.I)
    if fence: value = fence.group(1)
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start: raise ValueError('Qwen returned no JSON object')
    value = value[start:end + 1].replace(',}', '}').replace(',]', ']')
    return json.loads(value)


def call(content):
    key = os.getenv('DASHSCOPE_VISION_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    if not key: raise RuntimeError('DASHSCOPE_VISION_API_KEY or DASHSCOPE_API_KEY is required')
    payload = {
        'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus',
        'input': {'messages': [{'role': 'user', 'content': content}]},
        'parameters': {'result_format': 'message', 'max_tokens': 6000},
    }
    request = Request(os.getenv('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    last = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response: reply = json.loads(response.read().decode('utf-8'))
            parts = (((reply.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or []
            return parse_json(next((part.get('text') for part in parts if isinstance(part, dict) and part.get('text')), ''))
        except Exception as exc:
            last = exc; time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(str(last))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--ocr-record', type=Path, required=True)
    ap.add_argument('--candidates', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--batch-size', type=int, default=8)
    args = ap.parse_args()
    source = Image.open(args.image).convert('RGB')
    ocr = json.loads(args.ocr_record.read_text(encoding='utf-8'))
    candidate_payload = json.loads(args.candidates.read_text(encoding='utf-8'))
    candidates = candidate_payload.get('candidates') or []
    reviews = []
    prompt = '''You are the local candidate verifier in an editable PowerPoint reconstruction. OCR is authoritative for glyphs only: readable words, numbers, dates, percentages and units must stay editable OCR text, but their visible NON-TEXT HOSTS absolutely must be retained. A blue date pill, a numbered square, a card border, a callout tab, a chart panel or an arrow band containing OCR must be classified native_editable, not rejected.
Each crop contains a bright green outline showing the exact candidate to judge. The green outline is an inspection marker, NOT source content and never an icon. Evaluate only that outlined region. Reject only when the outlined region contains no meaningful non-text geometry beyond glyph pixels/noise. Classify it as native_editable (simple card/panel/pill/line/arrow/circle/table host), library_asset (one icon/logo/pictogram/reusable complex asset), qwen_image_asset (one unique complex visual), decorative_fixed, decorative_movable, or reject_ocr_or_noise. Do not classify OCR number glyphs or KPI values as chart artwork. A library_asset or qwen_image_asset MUST NOT contain readable OCR: if it overlaps OCR, classify the nonsemantic backdrop decorative_fixed/decorative_movable or choose native_editable for a simple host. If the outlined region contains several independent elements, classify the enclosing host if one exists; otherwise use qwen_image_asset with semantic "composite visual requires split".
Return strict JSON only: {"reviews":[{"candidateId":"","isNontext":true,"classification":"","semantic":"English concise visual description, never text content","refinedLocalBBox":[x,y,w,h],"confidence":0..1,"structuralVetoes":[],"reason":""}]}. refinedLocalBBox is relative to the crop. Do not self-score recall.'''
    for offset in range(0, len(candidates), max(1, args.batch_size)):
        group = candidates[offset:offset + max(1, args.batch_size)]
        content = [{'text': prompt}]
        for item in group:
            x, y, w, h = [int(v) for v in item['localCropBBox']]
            crop = source.crop((x, y, x + w, y + h))
            # The model must visually see the exact detector proposal.  A
            # textual coordinate alone caused text-bearing hosts to be missed.
            marked = crop.copy(); draw = ImageDraw.Draw(marked)
            bx, by, bw, bh = [int(v) for v in item['sourceBBox']]
            draw.rectangle((bx - x, by - y, bx - x + bw - 1, by - y + bh - 1), outline='#00FF00', width=max(2, min(5, round(min(w, h) * .025))))
            local_ocr = []
            for line in ocr.get('lines', []):
                bx, by, bw, bh = [float(v) for v in line.get('bbox', [0, 0, 0, 0])]
                if bx < x + w and by < y + h and bx + bw > x and by + bh > y:
                    local_ocr.append([round(bx - x), round(by - y), round(bw), round(bh)])
            content += [
                {'text': f"CANDIDATE {item['id']}; marked source box={item['sourceBBox']}; crop canvas={w}x{h}; candidate-local box={[item['sourceBBox'][0]-x,item['sourceBBox'][1]-y,item['sourceBBox'][2],item['sourceBBox'][3]]}; role hint={item['roleHint']}; OCR boxes={local_ocr}"},
                {'image': data_url(marked)},
            ]
        response = call(content)
        by_id = {row.get('candidateId'): row for row in response.get('reviews', []) if isinstance(row, dict)}
        for item in group:
            raw = by_id.get(item['id'])
            if not raw:
                reviews.append({'candidateId': item['id'], 'status': 'unverified', 'reason': 'missing_from_qwen_response'})
                continue
            local = raw.get('refinedLocalBBox')
            try:
                lx, ly, lw, lh = [round(float(v)) for v in local]
                cx, cy, cw, ch = item['localCropBBox']
                if lw < 2 or lh < 2 or lx < 0 or ly < 0 or lx + lw > cw or ly + lh > ch: raise ValueError
                refined = [cx + lx, cy + ly, lw, lh]
            except Exception:
                refined = item['sourceBBox']
            classification = str(raw.get('classification') or 'reject_ocr_or_noise')
            if classification not in {'native_editable','library_asset','qwen_image_asset','decorative_fixed','decorative_movable','reject_ocr_or_noise'}:
                classification = 'reject_ocr_or_noise'
            is_nontext = bool(raw.get('isNontext')) and classification != 'reject_ocr_or_noise'
            # A generated/reused picture may never bake readable OCR.  Native
            # containers are allowed to host text; decorative fixed assets are
            # repaired around OCR by the clean-background route.
            ocr_overlap = float(item.get('ocrOverlap') or 0)
            asset_overlaps_ocr = classification in {'library_asset', 'qwen_image_asset', 'decorative_movable'} and ocr_overlap > .15
            status = 'unverified' if asset_overlaps_ocr else ('verified' if is_nontext and float(raw.get('confidence') or 0) >= .80 else 'rejected')
            reviews.append({
                'candidateId': item['id'], 'status': status,
                'isNontext': is_nontext, 'classification': classification,
                'semantic': str(raw.get('semantic') or ''), 'sourceBBox': refined,
                'confidence': round(float(raw.get('confidence') or 0), 3),
                'structuralVetoes': raw.get('structuralVetoes') if isinstance(raw.get('structuralVetoes'), list) else [],
                'reason': ('image_asset_contains_ocr_text; split or classify backdrop as decorative_fixed' if asset_overlaps_ocr else str(raw.get('reason') or '')),
            })
    verified = [row for row in reviews if row['status'] == 'verified']
    payload = {
        'schema': 'qwen-local-candidate-review/v1',
        'provider': {'name': 'dashscope-native-multimodal', 'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus'},
        'source': str(args.image.resolve()), 'candidateInventory': str(args.candidates.resolve()),
        'coordinateContract': '[x,y,w,h] source pixels', 'reviews': reviews,
        'summary': {'proposed': len(candidates), 'verified': len(verified), 'rejected': sum(row['status'] == 'rejected' for row in reviews), 'unverified': sum(row['status'] == 'unverified' for row in reviews)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'completed', **payload['summary'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
