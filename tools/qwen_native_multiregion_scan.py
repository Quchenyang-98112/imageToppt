#!/usr/bin/env python3
"""Use Qwen3-VL in overlapping source-pixel regions before one deterministic merge.

This is an inventory operation, not a post-PPTX repair operation. OCR remains
the text authority; Qwen sees only the image and OCR box exclusions.
"""
from __future__ import annotations

import argparse, base64, concurrent.futures, io, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image
from qwen_env import load_project_env


# Make direct CLI execution pick up the same project-local model settings as
# the application runtime. Existing process environment values still win.
load_project_env(Path(__file__))

W, H = 1600, 900
ZONES = [
    ('header', (0, 0, 1600, 230)),
    ('content-upper', (0, 180, 1600, 300)),
    ('content-lower', (0, 430, 1600, 320)),
    ('footer', (0, 700, 1600, 200)),
]
KINDS = {'rectangle', 'roundRect', 'ellipse', 'line', 'arrow', 'connector', 'table', 'freeform'}
ASSET_KINDS = {'icon', 'logo', 'illustration', 'photo', 'chart', 'screenshot'}

def data_url(image: Image.Image) -> str:
    stream = io.BytesIO(); image.save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')

def loose_json(text: str) -> dict:
    value = text.strip(); match = re.search(r'```(?:json)?\s*([\s\S]*?)```', value, re.I); value = match.group(1) if match else value
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end < start: raise ValueError('no JSON object in Qwen reply')
    value = value[start:end + 1].replace('\r', '').replace(',}', '}').replace(',]', ']')
    return json.loads(value)

def box(value, width: int, height: int) -> list[int]:
    if not isinstance(value, list) or len(value) != 4: raise ValueError('bbox must be [x,y,w,h]')
    x, y, w, h = [round(float(v)) for v in value]
    if w < 2 or h < 2 or x < 0 or y < 0 or x + w > width or y + h > height: raise ValueError('bbox outside crop')
    return [x, y, w, h]

def overlap(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b; iw = max(0, min(ax + aw, bx + bw) - max(ax, bx)); ih = max(0, min(ay + ah, by + bh) - max(ay, by))
    return iw * ih / max(1, aw * ah + bw * bh - iw * ih)

def call_zone(image: Image.Image, zone_id: str, zone: tuple[int, int, int, int], ocr_boxes: list[list[int]]) -> dict:
    x, y, width, height = zone; crop = image.crop((x, y, x + width, y + height))
    local_ocr = [[max(0, bx - x), max(0, by - y), bw, bh] for bx, by, bw, bh in ocr_boxes if bx < x + width and by < y + height and bx + bw > x and by + bh > y]
    prompt = f'''You are Qwen3-VL's local non-text inventory pass for an editable PowerPoint replica. This crop is the {zone_id} region of a 1600x900 source slide. Crop-local canvas is {width}x{height}; use crop-local [x,y,w,h] coordinates exactly. OCR boxes shown below are editable text exclusions, never recreate or list their glyphs.
Return ONLY compact JSON: {{"objects":[{{"kind":"rectangle|roundRect|ellipse|line|arrow|connector|table|freeform","role":"container|divider|flow|decoration","bbox":[x,y,w,h],"fill":"#RRGGBB","stroke":"#RRGGBB","strokeWidth":1,"radius":0,"zIndex":0}}],"assets":[{{"kind":"icon|logo|illustration|photo|chart|screenshot","bbox":[x,y,w,h],"zIndex":0,"semantic":"English description without text","colorRoles":{{"primary":"#RRGGBB","secondary":"#RRGGBB","background":"transparent"}}}}]}}.
List every visible NON-TEXT foreground element intersecting this crop: cards, panels, tabs, lines, arrows, circles, simple charts, icons, logos, illustrations and complex diagrams. Use native objects only for simple editable geometry; use assets for icons, logos, pictures, pictograms, complex charts and gradient/ribbon artwork. Do not list the same large panel more than once. Maximum 12 objects and 12 assets.
OCR_EXCLUSION_BOXES={json.dumps(local_ocr,separators=(',',':'))}'''
    key = os.getenv('DASHSCOPE_VISION_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    payload = {'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus', 'input': {'messages': [{'role': 'user', 'content': [{'image': data_url(crop)}, {'text': prompt}]}]}, 'parameters': {'result_format': 'message', 'max_tokens': 2800}}
    request = Request(os.getenv('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    last = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=120) as response: response_json = json.loads(response.read().decode('utf-8'))
            parts = (((response_json.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or []
            text = next((part.get('text') for part in parts if isinstance(part, dict) and part.get('text')), '')
            return loose_json(text)
        except Exception as exc:
            last = exc
            if attempt == 2: raise RuntimeError(str(last))
            time.sleep(1.2 * (attempt + 1))

def normalize(raw: dict, zone: tuple[int, int, int, int], zone_id: str, ordinal: int) -> dict | None:
    x, y, width, height = zone
    try:
        local = box(raw.get('bbox'), width, height); global_box = [local[0] + x, local[1] + y, local[2], local[3]]
        if raw.get('kind') in KINDS:
            return {'id': f'{zone_id}-native-{ordinal:02d}', 'kind': raw['kind'], 'classification': 'native_editable', 'role': raw.get('role') if raw.get('role') in {'container','divider','flow','decoration'} else 'container', 'bbox': global_box, 'fill': raw.get('fill', '#FFFFFF'), 'stroke': raw.get('stroke', '#D9E3F0'), 'strokeWidth': raw.get('strokeWidth', 1), 'radius': raw.get('radius', 0), 'zIndex': raw.get('zIndex', 0), 'zone': zone_id}
        if raw.get('kind') in ASSET_KINDS:
            colors = raw.get('colorRoles') if isinstance(raw.get('colorRoles'), dict) else {}
            return {'id': f'{zone_id}-asset-{ordinal:02d}', 'kind': raw['kind'], 'classification': 'imagegen_asset', 'role': 'asset', 'bbox': global_box, 'zIndex': raw.get('zIndex', 0), 'semantic': str(raw.get('semantic') or raw['kind']), 'colorRoles': {'primary': colors.get('primary', '#1768B5'), 'secondary': colors.get('secondary', '#FFFFFF'), 'background': colors.get('background', 'transparent')}, 'zone': zone_id}
    except Exception:
        return None
    return None

def merge(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda item: (item['bbox'][1], item['bbox'][0], item['id']))
    result=[]
    for item in ordered:
        same = next((prior for prior in result if prior['classification'] == item['classification'] and prior['kind'] == item['kind'] and overlap(prior['bbox'], item['bbox']) >= .72), None)
        if same is None: result.append(item)
    for index, item in enumerate(result, 1): item['id'] = f"{'obj' if item['classification']=='native_editable' else 'asset'}-{index:03d}"; item['zIndex'] = index
    return result

def scan_slide(source: Path, ocr_path: Path, output: Path) -> dict:
    ocr = json.loads(ocr_path.read_text(encoding='utf-8')); image = Image.open(source).convert('RGB')
    if image.size != (W, H): raise ValueError(f'{source.name} must be normalized to 1600x900')
    ocr_boxes = [line.get('bbox') for line in ocr.get('lines', []) if isinstance(line, dict) and isinstance(line.get('bbox'), list)]
    raw=[]; zone_reports=[]
    for zone_id, zone in ZONES:
        try:
            response = call_zone(image, zone_id, zone, ocr_boxes)
            objects = [normalize(item, zone, zone_id, index + 1) for index, item in enumerate(response.get('objects') or []) if isinstance(item, dict)]
            assets = [normalize(item, zone, zone_id, index + 1) for index, item in enumerate(response.get('assets') or []) if isinstance(item, dict)]
            raw.extend([item for item in objects + assets if item]); zone_reports.append({'zone': zone_id, 'status': 'completed', 'objects': len([x for x in objects if x]), 'assets': len([x for x in assets if x])})
        except Exception as exc:
            zone_reports.append({'zone': zone_id, 'status': 'failed', 'error': str(exc)})
    merged = merge(raw); objects = [item for item in merged if item['classification'] == 'native_editable']; assets = [item for item in merged if item['classification'] == 'imagegen_asset']
    record = {'schema': 'qwen-native-multiregion-layout/v3', 'source': ocr['source'], 'ocr_record': str(ocr_path.resolve()), 'provider': {'name': 'dashscope-native-multimodal', 'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus', 'strategy': 'overlapping_4_zone_nontext_scan'}, 'objects': objects, 'assets': assets, 'zoneReports': zone_reports, 'zoneCompletion': sum(x['status'] == 'completed' for x in zone_reports) / len(ZONES), 'needsReview': any(x['status'] != 'completed' for x in zone_reports)}
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'slide': source.name, 'objects': len(objects), 'assets': len(assets), 'zonesPassed': sum(x['status'] == 'completed' for x in zone_reports), 'needsReview': record['needsReview']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-dir',type=Path,required=True);parser.add_argument('--ocr-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--workers',type=int,default=2);args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for source in sorted(args.source_dir.glob('*.png')):
        ocr = args.ocr_dir / f'{source.stem}.ocr.v1.json'
        if ocr.exists(): jobs.append((source, ocr, args.output_dir / f'{source.stem}.layout.v3.json'))
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(scan_slide,*job):job[0] for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            source=futures[future]
            try: rows.append(future.result())
            except Exception as exc: rows.append({'slide':source.name,'failed':True,'error':str(exc)})
    rows.sort(key=lambda x:x['slide']); report={'schema':'qwen-native-multiregion-batch/v3','uses_openai_or_gpt':False,'model':os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus','zonesPerSlide':len(ZONES),'slides':rows,'completed':sum(not x.get('failed') for x in rows),'failed':sum(bool(x.get('failed')) for x in rows)}
    (args.output_dir/'multiregion-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
    if report['failed']: raise SystemExit(2)
if __name__=='__main__': main()
