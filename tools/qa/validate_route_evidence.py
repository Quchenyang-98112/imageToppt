#!/usr/bin/env python3
"""Validate independent v3 route evidence and emit the only fusion authorization."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def load(path): return json.loads(path.read_text(encoding='utf-8'))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--text',type=Path,required=True);ap.add_argument('--nontext',type=Path,required=True);ap.add_argument('--background',type=Path,required=True);ap.add_argument('--fusion',type=Path);ap.add_argument('--editability',type=Path);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--policy',type=Path,default=Path(__file__).resolve().parents[2]/'config/qwen-global-policy.json');args=ap.parse_args()
    p=load(args.policy);g=p['quality_gates'];text=load(args.text);non=load(args.nontext);bg=load(args.background)
    tf=[];nf=[]
    checks=[('renderScore','text_min_score','min'),('contentAccuracy','text_content_min_accuracy','min'),('criticalTextAccuracy','critical_text_accuracy','min'),('bboxMeanIou','text_bbox_min_iou','min'),('styleMaxRelativeError','text_style_max_relative_error','max'),('colorMaxDeltaE','text_color_max_delta_e','max'),('visibleObjectCoverage','text_visible_object_coverage','min')]
    for field,key,mode in checks:
        if field not in text:tf.append(field);continue
        value=float(text[field]);limit=float(g[key]);bad=value<limit if mode=='min' else value>limit
        if bad:tf.append(field)
    if int(text.get('spellingOrOcrErrors',1))>0:tf.append('spellingOrOcrErrors')
    checks=[('renderScore','nontext_min_score','min'),('majorRecall','nontext_major_recall','min'),('allRecall','nontext_all_recall','min'),('bboxMeanIou','nontext_bbox_min_iou','min'),('sizeMaxRelativeError','nontext_size_max_relative_error','max'),('aspectMaxRelativeError','nontext_aspect_max_relative_error','max')]
    for field,key,mode in checks:
        if field not in non:nf.append(field);continue
        value=float(non[field]);limit=float(g[key]);bad=value<limit if mode=='min' else value>limit
        if bad:nf.append(field)
    if int(non.get('missingMajorElements',1))>0:nf.append('missingMajorElements')
    if int(non.get('structuralVetoes',1))>0:nf.append('structuralVetoes')
    if not non.get('layeringPassed'):nf.append('layeringPassed')
    gates={'text':{'score':float(text.get('renderScore',0)),'passed':not tf,'hardFailures':tf},'nontext':{'score':float(non.get('renderScore',0)),'passed':not nf,'hardFailures':nf},'background':{'score':min(float(bg.get('outsideMaskPixelIdentity',0)),float(bg.get('outsideMaskSsim',0)),float(bg.get('decorationCompleteness',0))),'passed':bool(bg.get('passed')),'hardFailures':bg.get('hardFailures',[])}}
    independent=all(gates[x]['passed'] for x in ('text','nontext','background'))
    if args.fusion:
        fusion=load(args.fusion);ff=[]
        if float(fusion.get('renderScore',0))<g['fusion_min_score']:ff.append('fusionRenderScore')
        if not fusion.get('textNontextAlignmentPassed'):ff.append('textNontextAlignment')
        if not fusion.get('layeringPassed'):ff.append('layering')
        if fusion.get('backgroundInflatedScore'):ff.append('backgroundInflatedScore')
        gates['fusion']={'score':float(fusion.get('renderScore',0)),'passed':independent and not ff,'hardFailures':([] if independent else ['routeFusionBeforeIndependentPass'])+ff}
    if args.editability:
        edit=load(args.editability);gates['editability']={'score':1 if edit.get('passed') else 0,'passed':bool(edit.get('passed')),'hardFailures':edit.get('hardFailures',[])}
    passed=independent and gates.get('fusion',{'passed':False})['passed'] and gates.get('editability',{'passed':False})['passed']
    result={'schema':'route-gate-manifest/v3','policy':p['schema'],'passed':passed,'fusionAuthorized':passed,'gates':gates,'repairOrder':p['repair_order'],'scanOrder':p['repair_loop']['scan_order'],'roundLimitIsNotPassCondition':True}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    if not passed:raise SystemExit(2)
if __name__=='__main__':main()
