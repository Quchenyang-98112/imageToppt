#!/usr/bin/env python3
"""Qwen-only rendered-vs-source visual gate review."""
from __future__ import annotations
import argparse, base64, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen

def env(name):
    v=os.getenv(name,'').strip()
    if v: return v
    p=Path('.env.local')
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            if line.startswith(name+'='): return line.split('=',1)[1].strip()
    return ''
def data_url(p):
    mime='image/jpeg' if p.suffix.lower() in {'.jpg','.jpeg'} else 'image/png'
    return f'data:{mime};base64,'+base64.b64encode(p.read_bytes()).decode()
def parse(s):
    s=s.strip(); a=s.find('{'); b=s.rfind('}')
    if a<0 or b<=a: raise ValueError('no JSON')
    return json.loads(s[a:b+1])
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True); ap.add_argument('--render',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--round',type=int,default=1); a=ap.parse_args()
    key=env('DASHSCOPE_API_KEY'); base=(env('DASHSCOPE_VISION_BASE_URL') or 'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/')
    model=env('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus'
    prompt='''You are the final independent rendered-vs-source QA gate for an editable PowerPoint reconstruction. Compare IMAGE A (source) and IMAGE B (rendered PPTX). This review is authoritative: never score from object counts or reward blank space.

Audit text independently for exact content (including typo/garbled-character detection), source-pixel position, size, font weight, color, wrapping and alignment. Audit non-text independently and aggressively: background preservation, cards/panels/pills, borders/shadows, arrows/flow bands/direction, charts, icons/logos, decorative lines/shapes, coordinates, dimensions, palette, and z-layer/occlusion. Missing a major non-text object, using it in a wrong location, or showing source foreground under an editable replacement is a hard failure. List missing items before wrong-match or styling items. Coordinates are SOURCE pixels as [x,y,w,h].

Return ONLY JSON: {"textScore":0..1,"nontextScore":0..1,"fusionScore":0..1,"backgroundPassed":true|false,"coordinateContractPassed":true|false,"missingFirst":[{"semantic":"","bbox":[x,y,w,h],"severity":"major|minor","suggestedRepair":""}],"wrong":[{"semantic":"","issue":"geometry|match|color|layering|background","severity":"major|minor","bbox":[x,y,w,h]}],"textIssues":[{"issue":"content|bbox|font|color|wrap","severity":"major|minor"}],"pass":true|false,"notes":""}. A pass requires text >= .90, nontext >= .80, fusion >= .85, backgroundPassed, coordinateContractPassed, no major missing, no major wrong layering.'''
    payload={'model':model,'temperature':0,'enable_thinking':False,'response_format':{'type':'json_object'},'max_completion_tokens':5000,'messages':[{'role':'user','content':[{'type':'text','text':prompt},{'type':'text','text':'IMAGE A = source'},{'type':'image_url','image_url':{'url':data_url(a.source),'min_pixels':65536,'max_pixels':3200000}},{'type':'text','text':'IMAGE B = rendered PPTX'},{'type':'image_url','image_url':{'url':data_url(a.render),'min_pixels':65536,'max_pixels':3200000}}]}]}
    req=Request(base+'/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    started=time.time()
    with urlopen(req,timeout=300) as resp: reply=json.loads(resp.read().decode())
    content=((reply.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    value=parse(content)
    major_missing=any(isinstance(x,dict) and x.get('severity')=='major' for x in value.get('missingFirst',[]))
    major_wrong=any(isinstance(x,dict) and x.get('severity')=='major' for x in value.get('wrong',[]))
    computed=bool(float(value.get('textScore',0))>=.90 and float(value.get('nontextScore',0))>=.80 and float(value.get('fusionScore',0))>=.85 and value.get('backgroundPassed') is True and value.get('coordinateContractPassed') is True and not major_missing and not major_wrong)
    value['pass']=computed
    value.update({'schema':'qwen-slide-review/v2','source':str(a.source),'render':str(a.render),'model':model,'round':a.round,'elapsedMs':round((time.time()-started)*1000)})
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'status':'completed','output':str(a.output),'textScore':value.get('textScore'),'nontextScore':value.get('nontextScore'),'fusionScore':value.get('fusionScore')}))
if __name__=='__main__': main()
