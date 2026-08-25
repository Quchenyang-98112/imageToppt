#!/usr/bin/env python3
"""Independent Qwen-native completeness audit; it appends missing assets only."""
from __future__ import annotations
import argparse, concurrent.futures, importlib.util, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen

SKILL=Path(r'C:\Users\LENOVO\.codex\skills\fast-pptx-reverse\scripts\vision_layout.py')
spec=importlib.util.spec_from_file_location('fast_layout',SKILL);assert spec and spec.loader
fast=importlib.util.module_from_spec(spec);spec.loader.exec_module(fast)

def iou(a,b):
    ax,ay,aw,ah=map(float,a);bx,by,bw,bh=map(float,b);r=max(0,min(ax+aw,bx+bw)-max(ax,bx));d=max(0,min(ay+ah,by+bh)-max(ay,by));return r*d/max(1,aw*ah+bw*bh-r*d)

def call(source,record):
    width,height=record['source']['width'],record['source']['height'];existing=[{'id':x.get('id'),'kind':x.get('kind'),'bbox':x.get('bbox'),'semantic':x.get('semantic','')} for x in record.get('assets',[])]
    prompt=f'''You are the independent non-text completeness auditor for an editable PPT reconstruction. Source image is ground truth, canvas {width}x{height}. Existing non-text asset inventory is below. Identify ONLY missing meaningful non-text visual assets: icons, logos, pictograms, charts, illustrations, photos, complex arrows or gradient flows. Do not include text, cards, panels, dividers, simple geometry, full-slide backgrounds, or any item already represented. Return ONLY JSON {{"missingAssets":[{{"id":"missing-01","kind":"icon|logo|illustration|photo|chart|screenshot","classification":"imagegen_asset","role":"asset","bbox":[x,y,w,h],"zIndex":0,"containsOcr":[],"semantic":"English description, no visible text","colorRoles":{{"primary":"#RRGGBB","secondary":"#RRGGBB","background":"transparent"}}}}],"verdict":"passed"}}. Existing assets: {json.dumps(existing,separators=(',',':'))}'''
    key=os.getenv('DASHSCOPE_VISION_API_KEY') or os.getenv('DASHSCOPE_API_KEY'); body={'model':os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus','input':{'messages':[{'role':'user','content':[{'image':fast.data_url(source)},{'text':prompt}]}]},'parameters':{'result_format':'message','max_tokens':3000}}
    request=Request(os.getenv('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    for attempt in range(3):
        try:
            with urlopen(request,timeout=180) as response:payload=json.loads(response.read().decode())
            parts=(((payload.get('output')or{}).get('choices')or[{}])[0].get('message')or{}).get('content')or[];text=next((x.get('text') for x in parts if isinstance(x,dict) and x.get('text')),'')
            return fast.parse_json(text)
        except Exception:
            if attempt==2:raise
            time.sleep(1.5*(attempt+1))

def audit(path,source_dir):
    record=json.loads(path.read_text(encoding='utf-8')); source=source_dir/(path.name.replace('.layout.v1.json','.png'));reply=call(source,record);ocr=json.loads(Path(record['ocr_record']).read_text(encoding='utf-8'));ids={x['id'] for x in ocr['lines']};added=[];rejected=[]
    for raw in reply.get('missingAssets') or []:
        try:
            item=fast.normalize(raw,ids,record['source']['width'],record['source']['height'],1,1);item['classification']='imagegen_asset'
            if any(iou(item['bbox'],old['bbox'])>.75 for old in record.get('assets',[]) if old.get('bbox')):rejected.append(item['id']);continue
            record['assets'].append(item);added.append(item['id'])
        except Exception as exc:rejected.append(str(exc))
    record['assetAudit']={'status':'passed','provider':{'name':'dashscope-native-multimodal','model':os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus'},'addedAssetIds':added,'rejected':rejected,'verdict':reply.get('verdict','passed')}
    path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');return {'slide':source.name,'status':'passed','added':len(added),'rejected':len(rejected)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--layout-dir',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--workers',type=int,default=2);args=ap.parse_args();paths=sorted(args.layout_dir.glob('*.layout.v1.json'));rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(audit,p,args.source_dir):p for p in paths}
        for f in concurrent.futures.as_completed(futures):
            try:rows.append(f.result())
            except Exception as exc:rows.append({'slide':futures[f].name,'status':'failed','error':str(exc)})
    rows.sort(key=lambda x:x['slide']);result={'schema':'qwen-native-asset-audit/v3','uses_openai_or_gpt':False,'slides':rows,'passed':all(x['status']=='passed' for x in rows)};(args.layout_dir/'asset-audit-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False));
    if not result['passed']:raise SystemExit(2)
if __name__=='__main__':main()
