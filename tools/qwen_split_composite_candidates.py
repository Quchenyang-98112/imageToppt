#!/usr/bin/env python3
"""Resolve the final OCR-overlapping composite candidates before asset execution."""
from __future__ import annotations
import argparse, base64, io, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw

def data_url(image):
    out=io.BytesIO(); image.save(out,'PNG'); return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
def parse(text):
    start,end=text.find('{'),text.rfind('}')
    if start<0 or end<=start: raise ValueError('no JSON');
    return json.loads(text[start:end+1].replace(',}', '}').replace(',]', ']'))
def call(content):
    key=os.getenv('DASHSCOPE_VISION_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    body={'model':os.getenv('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus','input':{'messages':[{'role':'user','content':content}]},'parameters':{'result_format':'message','max_tokens':5000}}
    req=Request(os.getenv('DASHSCOPE_VISION_NATIVE_BASE_URL') or 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    last=None
    for i in range(3):
        try:
            with urlopen(req,timeout=180) as r: value=json.loads(r.read().decode())
            parts=(((value.get('output')or{}).get('choices')or[{}])[0].get('message')or{}).get('content')or[]
            return parse(next((x.get('text') for x in parts if isinstance(x,dict) and x.get('text')),''))
        except Exception as exc: last=exc; time.sleep(1.2*(i+1))
    raise RuntimeError(str(last))
def valid_box(box,w,h):
    if not isinstance(box,list) or len(box)!=4:return None
    try:x,y,bw,bh=[round(float(v)) for v in box]
    except: return None
    if x<0 or y<0 or bw<2 or bh<2 or x+bw>w or y+bh>h:return None
    return [x,y,bw,bh]
def intersects(a,b):
    return max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))*max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True);ap.add_argument('--ocr',type=Path,required=True);ap.add_argument('--review',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    image=Image.open(a.source).convert('RGB'); review=json.loads(a.review.read_text(encoding='utf-8'));ocr=json.loads(a.ocr.read_text(encoding='utf-8')); repaired=[]
    for unresolved in [x for x in review.get('reviews',[]) if x.get('status')=='unverified']:
        x,y,w,h=[int(v) for v in unresolved['sourceBBox']]; crop=image.crop((x,y,x+w,y+h)); marked=crop.copy();ImageDraw.Draw(marked).rectangle((0,0,w-1,h-1),outline='#00FF00',width=4)
        local_ocr=[]
        for line in ocr.get('lines',[]):
            bx,by,bw,bh=[round(float(v)) for v in line.get('bbox',[0,0,0,0])]
            if intersects([x,y,w,h],[bx,by,bw,bh])>0:local_ocr.append([bx-x,by-y,bw,bh])
        prompt='''The green outline bounds one composite slide region that was blocked because it includes OCR text. Split ONLY its non-text foreground into native simple geometry, independent icon/logo assets, and fixed decoration. OCR boxes are supplied and MUST NOT be included in any image asset. Create native card/panel/pill/chevron/line shapes around text; create one asset per icon/logo without text. Return only JSON: {"native":[{"kind":"rectangle|roundRect|ellipse|line|arrow|chevron","bbox":[x,y,w,h],"fill":"#RRGGBB","stroke":"#RRGGBB","strokeWidth":1,"semantic":""}],"assets":[{"kind":"icon|logo|illustration","bbox":[x,y,w,h],"semantic":"English visual description without text"}],"decorativeFixed":[{"bbox":[x,y,w,h],"semantic":""}]}. Coordinates are crop-local. Do not return OCR glyphs, labels, numbers, dates, or text-bearing composites.'''
        result=call([{'text':prompt+f'\nCrop size={w}x{h}; OCR boxes={local_ocr}'},{'image':data_url(marked)}])
        children=[]
        for typ,key in [('native','native_editable'),('assets','library_asset'),('decorativeFixed','decorative_fixed')]:
            for index,item in enumerate(result.get(typ)or[],1):
                if not isinstance(item,dict):continue
                box=valid_box(item.get('bbox'),w,h)
                if not box:continue
                global_box=[x+box[0],y+box[1],box[2],box[3]]
                if key=='library_asset' and any(intersects(global_box,[bx,by,bw,bh])>max(12,.15*global_box[2]*global_box[3]) for bx,by,bw,bh in [line.get('bbox') for line in ocr.get('lines',[]) if isinstance(line,dict)]):continue
                children.append({'id':f"{unresolved['candidateId']}-{typ}-{index:02d}",'classification':key,'sourceBBox':global_box,'kind':item.get('kind','rectangle' if key=='native_editable' else 'icon'),'semantic':str(item.get('semantic')or''),'fill':item.get('fill','#FFFFFF'),'stroke':item.get('stroke','#D9E3F0'),'strokeWidth':item.get('strokeWidth',1),'parentCandidateId':unresolved['candidateId']})
        repaired.append({'candidateId':unresolved['candidateId'],'resolved':bool(children),'children':children})
    payload={'schema':'qwen-composite-split/v1','provider':{'name':'dashscope-native-multimodal','model':os.getenv('DASHSCOPE_VISION_MODEL')or'qwen3-vl-plus'},'source':str(a.source.resolve()),'review':str(a.review.resolve()),'repairs':repaired,'passed':all(x['resolved'] for x in repaired)}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'repairs':len(repaired),'passed':payload['passed'],'output':str(a.output)},ensure_ascii=False))
    if not payload['passed']:raise SystemExit(2)
if __name__=='__main__':main()
