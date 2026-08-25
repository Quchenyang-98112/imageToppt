#!/usr/bin/env python3
"""Deterministic BG_CLEAN residual and preservation audit for policy v3."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any
import numpy as np
from PIL import Image

TEXT_CLASSES={'ocr_text'}
NONTEXT_CLASSES={'native_editable','library_native','library_svg','library_png','exact_brand_asset','qwen_image_asset','decorative_movable'}

def box(item:dict[str,Any],w:int,h:int):
    raw=item.get('sourceBBox') or item.get('bbox')
    if isinstance(raw,dict): raw=[raw.get('x'),raw.get('y'),raw.get('w'),raw.get('h')]
    if not isinstance(raw,list) or len(raw)!=4:return None
    try:x,y,bw,bh=[float(v) for v in raw]
    except:return None
    if x<0 or y<0 or bw<=0 or bh<=0 or x+bw>w+.5 or y+bh>h+.5:return None
    e=max(2,min(8,round(bh*.08)));return max(0,int(x)-e),max(0,int(y)-e),min(w,int(np.ceil(x+bw))+e),min(h,int(np.ceil(y+bh))+e)

def masks(items,w,h):
    text=np.zeros((h,w),bool);nontext=np.zeros((h,w),bool);fixed=0
    for item in items:
        if not isinstance(item,dict):continue
        cls=str(item.get('reconstructionClass') or item.get('classification') or '')
        if cls=='decorative_fixed':fixed+=1;continue
        b=box(item,w,h)
        if not b:continue
        x1,y1,x2,y2=b
        if cls in TEXT_CLASSES:text[y1:y2,x1:x2]=True
        elif cls in NONTEXT_CLASSES:nontext[y1:y2,x1:x2]=True
    return text,nontext,fixed

def edges(rgb):
    gray=rgb[...,0]*.299+rgb[...,1]*.587+rgb[...,2]*.114
    gx=np.abs(np.diff(gray,axis=1,prepend=gray[:,:1]));gy=np.abs(np.diff(gray,axis=0,prepend=gray[:1,:]));return gx+gy

def residual(source_edges,clean_edges,mask,threshold=18):
    original=(source_edges>threshold)&mask
    if not original.any():return 0.0
    return float(np.mean(clean_edges[original]>threshold))

def simplified_ssim(a,b,mask):
    if not mask.any():return 1.0
    x=a[mask].mean(axis=1);y=b[mask].mean(axis=1);c1=6.5025;c2=58.5225
    mx,my=float(x.mean()),float(y.mean());vx,vy=float(x.var()),float(y.var());cov=float(np.mean((x-mx)*(y-my)))
    return ((2*mx*my+c1)*(2*cov+c2))/max(1e-9,(mx*mx+my*my+c1)*(vx+vy+c2))

def rgb_to_lab(rgb):
    x=np.clip(rgb/255.0,0,1);x=np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
    xyz=x@np.array([[.4124564,.3575761,.1804375],[.2126729,.7151522,.0721750],[.0193339,.1191920,.9503041]]).T
    xyz=xyz/np.array([.95047,1.0,1.08883]);e=216/24389;k=24389/27
    f=np.where(xyz>e,np.cbrt(xyz),(k*xyz+16)/116)
    return np.stack([116*f[...,1]-16,500*(f[...,0]-f[...,1]),200*(f[...,1]-f[...,2])],axis=-1)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--clean',type=Path,required=True);ap.add_argument('--inventory',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--ocr-rescan-record',type=Path);ap.add_argument('--decoration-score',type=float);ap.add_argument('--seam-score',type=float);args=ap.parse_args()
    source=np.asarray(Image.open(args.source).convert('RGB'),dtype=np.float32);clean=np.asarray(Image.open(args.clean).convert('RGB'),dtype=np.float32)
    if source.shape!=clean.shape:raise ValueError('source and clean background dimensions differ')
    h,w=source.shape[:2];payload=json.loads(args.inventory.read_text(encoding='utf-8'));items=payload.get('elements') or payload.get('inventory') or payload
    if not isinstance(items,list):raise ValueError('inventory array missing')
    text_mask,nontext_mask,fixed_count=masks(items,w,h);remove=text_mask|nontext_mask;outside=~remove
    difference=np.abs(source-clean);identity=float(np.mean(np.all(difference[outside]<=1,axis=1))) if outside.any() else 1.0
    delta=float(np.mean(np.linalg.norm(rgb_to_lab(source)[outside]-rgb_to_lab(clean)[outside],axis=1))) if outside.any() else 0.0;ssim=float(simplified_ssim(source,clean,outside))
    se,ce=edges(source),edges(clean);text_res=residual(se,ce,text_mask);nontext_res=residual(se,ce,nontext_mask);shadow_res=residual(se,ce,nontext_mask,8)
    ocr_lines=-1
    if args.ocr_rescan_record and args.ocr_rescan_record.exists():
        o=json.loads(args.ocr_rescan_record.read_text(encoding='utf-8'));ocr_lines=len(o.get('lines') or o.get('elements') or [])
    decoration=float(args.decoration_score if args.decoration_score is not None else (1.0 if fixed_count==0 else 0.0))
    seams_passed=args.seam_score is not None and args.seam_score>=.98
    failures=[]
    if identity<.995:failures.append('outside_mask_pixel_identity')
    if ssim<.995:failures.append('outside_mask_ssim')
    if delta>2:failures.append('outside_mask_delta')
    if ocr_lines<0:failures.append('ocr_rescan_missing')
    elif ocr_lines>0:failures.append('ocr_rescan_detects_text')
    if text_res>.005:failures.append('text_edge_residual')
    if nontext_res>.01:failures.append('nontext_edge_residual')
    if shadow_res>.02:failures.append('shadow_residual')
    if decoration<.95:failures.append('fixed_decoration_review')
    if not seams_passed:failures.append('repair_seams_review')
    result={'schema':'bg-clean-audit/v3','source':str(args.source.resolve()),'clean':str(args.clean.resolve()),'outsideMaskPixelIdentity':round(identity,6),'outsideMaskSsim':round(ssim,6),'outsideMaskDeltaE':round(delta,4),'ocrRescanLines':ocr_lines,'textEdgeResidual':round(text_res,6),'nontextEdgeResidual':round(nontext_res,6),'shadowResidual':round(shadow_res,6),'decorationCompleteness':round(decoration,4),'seamsPassed':seams_passed,'fixedDecorationCount':fixed_count,'passed':not failures,'hardFailures':failures}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'passed':result['passed'],'hardFailures':failures},ensure_ascii=False))

if __name__=='__main__':main()
