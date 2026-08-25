#!/usr/bin/env python3
"""Qwen-native, OCR-anchored high-fidelity layout planner for global policy v3."""
from __future__ import annotations

import argparse, concurrent.futures, importlib.util, json, os, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

SKILL = Path(r'C:\Users\LENOVO\.codex\skills\fast-pptx-reverse\scripts\vision_layout.py')
spec = importlib.util.spec_from_file_location('fast_layout', SKILL)
assert spec and spec.loader
fast_layout = importlib.util.module_from_spec(spec); spec.loader.exec_module(fast_layout)

def native_call(image: Path, ocr: dict) -> dict:
    lines = ocr.get('lines') or []
    inventory = [{'id': line.get('id'), 'bbox': line.get('bbox')} for line in lines if isinstance(line, dict)]
    if not inventory: raise ValueError('OCR inventory is empty')
    width, height = int(ocr['source']['width']), int(ocr['source']['height'])
    prompt = f'''You are the independent non-text visual planner for an editable PowerPoint reconstruction. The attached image is ground truth. Canvas is EXACTLY {width}x{height} source pixels. OCR inventory supplies all visible text and is authoritative; do not output or transcribe text.
Return ONLY strict JSON in this exact schema: {{"canvas":{{"background":"#RRGGBB"}},"assets":[{{"id":"asset-01","kind":"icon|logo|illustration|photo|chart|screenshot","classification":"imagegen_asset","role":"asset","bbox":[x,y,w,h],"zIndex":0,"containsOcr":[],"semantic":"English description with no visible text","colorRoles":{{"primary":"#RRGGBB","secondary":"#RRGGBB","background":"transparent"}}}}],"objects":[{{"id":"obj-01","kind":"rectangle|roundRect|ellipse|line|arrow|connector|table|freeform","classification":"native_editable","role":"container|decoration|divider|flow","bbox":[x,y,w,h],"fill":"#RRGGBB","stroke":"#RRGGBB","strokeWidth":1,"radius":0,"zIndex":0,"containsOcr":["ocr-id"]}}],"note":"short note"}}.
Inventory every meaningful non-text visual. Use native objects for cards, panels, dividers, simple arrows, circles, table borders and simple charts. Use imagegen_asset for every icon, pictogram, logo, complex chart/diagram, gradient/ribbon flow, illustration or photo. Do not create a full-slide asset. Asset boxes must be tight and must exclude nearby editable text. Every semantic foreground object must be included. Put assets before objects.
OCR_INVENTORY={json.dumps(inventory, ensure_ascii=False, separators=(',', ':'))}'''
    key = os.getenv('DASHSCOPE_VISION_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    if not key: raise RuntimeError('missing Qwen vision key')
    body = {'model': os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus', 'input': {'messages': [{'role': 'user', 'content': [{'image': fast_layout.data_url(image)}, {'text': prompt}]}]}, 'parameters': {'result_format': 'message', 'max_tokens': 6000}}
    request = Request(os.getenv('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation', data=json.dumps(body).encode('utf-8'), headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
    last = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response: payload = json.loads(response.read().decode('utf-8'))
            content = (((payload.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content') or []
            text = next((part.get('text') for part in content if isinstance(part, dict) and part.get('text')), '')
            plan = fast_layout.parse_json(text)
            objects, assets = plan.get('objects') or [], plan.get('assets') or []
            source = ocr['source']; ocr_ids = {str(x['id']) for x in inventory}
            def clean(items):
                output=[]; rejected=[]
                for item in items:
                    try: output.append(fast_layout.normalize(item, ocr_ids, width, height, 1, 1))
                    except Exception as exc: rejected.append(str(exc))
                return output, rejected
            objects, rejected1 = clean(objects); assets, rejected2 = clean(assets)
            for item in objects: item['classification']='native_editable'
            for item in assets: item['classification']='imagegen_asset'
            if not objects and not assets: raise ValueError('empty visual plan')
            return {'schema':'pptx-reverse-layout/v1','fidelity':'high','source':source,'ocr_record':'','canvas':plan.get('canvas') if isinstance(plan.get('canvas'),dict) else {'background':'#FFFFFF'},'objects':objects,'assets':assets,'note':str(plan.get('note') or ''),'rejected':rejected1+rejected2,'provider':{'name':'dashscope-native-multimodal','model':body['model'],'transport':'native'}}
        except Exception as exc:
            last=exc; time.sleep(1.5*(attempt+1))
    raise RuntimeError(f'Qwen native layout failed: {last}')

def job(ocr_path: Path, source_dir: Path, output_dir: Path):
    ocr=json.loads(ocr_path.read_text(encoding='utf-8')); source=source_dir/(ocr_path.name.replace('.ocr.v1.json','.png'))
    if not source.exists(): raise FileNotFoundError(source)
    out=output_dir/(ocr_path.name.replace('.ocr.v1.json','.layout.v1.json'))
    record=native_call(source,ocr); record['ocr_record']=str(ocr_path.resolve()); out.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'slide':source.name,'status':'completed','layout':str(out),'objects':len(record['objects']),'assets':len(record['assets'])}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ocr-dir',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--output-dir',type=Path,required=True);ap.add_argument('--workers',type=int,default=2);args=ap.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    paths=sorted(args.ocr_dir.glob('*.ocr.v1.json')); rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(job,path,args.source_dir,args.output_dir):path for path in paths}
        for future in concurrent.futures.as_completed(futures):
            path=futures[future]
            try: rows.append(future.result())
            except Exception as exc: rows.append({'slide':path.name,'status':'failed','error':str(exc)})
    rows.sort(key=lambda x:x['slide']); result={'schema':'qwen-native-layout-batch/v3','model':os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus','uses_openai_or_gpt':False,'slides':rows,'completed':sum(x['status']=='completed' for x in rows),'failed':sum(x['status']!='completed' for x in rows)}
    (args.output_dir/'layout-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    if result['failed']:raise SystemExit(2)
if __name__=='__main__': main()
