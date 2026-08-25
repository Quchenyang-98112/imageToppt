#!/usr/bin/env python3
"""One Qwen-VL gallery adjudication pass per slide, using crop + Top-K contact sheets."""
from __future__ import annotations
import argparse, base64, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw
from qwen_env import load_project_env


load_project_env(Path(__file__))

def env(name):
    return os.getenv(name,'').strip()
def data_url(image):
    import io
    b=io.BytesIO(); image.convert('RGB').save(b,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(b.getvalue()).decode()
def parse(s):
    a,b=s.find('{'),s.rfind('}')
    if a<0 or b<=a: raise ValueError('Qwen returned no JSON')
    return json.loads(s[a:b+1])
def crop_box(v,w,h):
    x,y,bw,bh=[round(float(x)) for x in v]
    return max(0,x),max(0,y),min(w,max(1,bw)),min(h,max(1,bh))
def priority(asset):
    b=asset.get('bbox') or [0,0,0,0]; area=float(b[2])*float(b[3])
    words=(str(asset.get('semantic',''))+' '+str(asset.get('role',''))).lower()
    return area + (25000 if any(x in words for x in ('logo','icon','chart','arrow','ribbon','illustration','flow')) else 0)
def contact(candidates):
    tile=160; canvas=Image.new('RGB',(tile*5,200),'white'); draw=ImageDraw.Draw(canvas)
    for i,c in enumerate(candidates[:10]):
        im=Image.open(c['preview']).convert('RGBA'); im.thumbnail((145,145))
        x=(i%5)*tile+(tile-im.width)//2; y=(i//5)*100
        canvas.paste(im,(x,y),im); draw.text(((i%5)*tile+4,y+75),f"{i}:{c['id']}",fill='black')
    return canvas
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True); ap.add_argument('--audit',type=Path,required=True); ap.add_argument('--matches',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--max-assets',type=int,default=10); ap.add_argument('--asset-ids',default='',help='optional comma-separated stable asset IDs for one bounded local review batch'); args=ap.parse_args()
    source=Image.open(args.source).convert('RGBA'); audit=json.loads(args.audit.read_text(encoding='utf-8')); matches=json.loads(args.matches.read_text(encoding='utf-8'))
    candidate_by_id={x['elementId']:x.get('candidates',[]) for x in matches.get('results',[])}
    assets=sorted([x for x in audit.get('imagegenAssets',[]) if candidate_by_id.get(x.get('id'))],key=priority,reverse=True)
    requested={x.strip() for x in args.asset_ids.split(',') if x.strip()}
    if requested:
        assets=[x for x in assets if x.get('id') in requested]
    assets=assets[:args.max_assets]
    content=[{'type':'text','text':'''You are the global gallery adjudicator under reconstruction policy v3. For each SOURCE CROP and CANDIDATE CONTACT SHEET, choose only a candidate with the same semantic identity, silhouette, internal structure, direction/opening, aspect and style. Reject wrong arrow direction, pie opening, bar count, head orientation, internal structure, or logo mark/text as a structural veto. A source crop is query only and must never be reused as an asset. Preview PNGs are retrieval-only; the executor must use the candidate's actual native/SVG/PNG payload. Return only JSON {"selections":[{"elementId":"","candidateIndex":0..4|-1,"visualSimilarity":0..1,"structuralVetoes":[],"reason":""}]}. Use -1 below 0.88; 0.95 is required for direct acceptance.'''}]
    for asset in assets:
        x,y,w,h=crop_box(asset['bbox'],source.width,source.height); crop=source.crop((x,y,x+w,y+h)); candidates=candidate_by_id[asset['id']]
        content += [{'type':'text','text':f"ELEMENT {asset['id']}: {asset.get('semantic','')}; source crop then candidates."},{'type':'image_url','image_url':{'url':data_url(crop),'min_pixels':4096,'max_pixels':400000}},{'type':'image_url','image_url':{'url':data_url(contact(candidates)),'min_pixels':65536,'max_pixels':640000}}]
    key=env('DASHSCOPE_API_KEY'); base=(env('DASHSCOPE_VISION_BASE_URL') or 'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/'); model=env('DASHSCOPE_VISION_MODEL') or 'qwen3-vl-plus'
    payload={'model':model,'temperature':0,'enable_thinking':False,'response_format':{'type':'json_object'},'max_completion_tokens':5000,'messages':[{'role':'user','content':content}]}
    started=time.time(); req=Request(base+'/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    with urlopen(req,timeout=300) as response: answer=parse(json.loads(response.read().decode())['choices'][0]['message']['content'])
    selected=[]
    for row in answer.get('selections',[]):
        eid=row.get('elementId'); index=row.get('candidateIndex'); candidates=candidate_by_id.get(eid,[])
        vetoes=row.get('structuralVetoes') or []
        if isinstance(index,int) and 0<=index<len(candidates) and float(row.get('visualSimilarity',0))>=.88 and not vetoes:
            selected.append({'elementId':eid,'candidate':candidates[index],'visualSimilarity':float(row['visualSimilarity']),'structuralVetoes':[],'reason':row.get('reason',''),'approved':True,'requiresPostInsertRenderReview':True})
        elif eid: selected.append({'elementId':eid,'approved':False,'reason':row.get('reason','no candidate above visual threshold')})
    payload={'schema':'qwen-gallery-rerank/v1','model':model,'source':str(args.source),'maxAssets':args.max_assets,'selections':selected,'unreviewedAssetIds':[x['id'] for x in audit.get('imagegenAssets',[]) if x.get('id') not in {r.get('elementId') for r in answer.get('selections',[])}],'elapsedMs':round((time.time()-started)*1000)}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'output':str(args.output),'approved':sum(x.get('approved',False) for x in selected),'reviewed':len(selected)},ensure_ascii=False))
if __name__=='__main__': main()
