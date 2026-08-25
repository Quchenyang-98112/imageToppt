#!/usr/bin/env python3
"""Merge accepted local reviews and automated composite repairs into build inventories."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--review-dir',type=Path,required=True);ap.add_argument('--ocr-dir',type=Path,required=True);ap.add_argument('--split-dir',type=Path,required=True);ap.add_argument('--output-dir',type=Path,required=True);a=ap.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True);rows=[]
    for rp in sorted(a.review_dir.glob('*.candidate-review.json')):
        stem=rp.name.replace('.candidate-review.json','');review=json.loads(rp.read_text(encoding='utf-8'));elements=[]
        for x in review.get('reviews',[]):
            if x.get('status')=='verified': elements.append({'id':x['candidateId'],'classification':x['classification'],'sourceBBox':x['sourceBBox'],'kind':'roundRect' if x['classification']=='native_editable' else 'icon','semantic':x.get('semantic',''),'fill':'#1768B5' if 'blue' in str(x.get('semantic','')).lower() else '#FFFFFF','stroke':'#D9E3F0','strokeWidth':1})
        sp=a.split_dir/f'{stem}.split.json'
        if sp.exists():
            for repair in json.loads(sp.read_text(encoding='utf-8')).get('repairs',[]):
                elements.extend(repair.get('children',[]))
        ocr=json.loads((a.ocr_dir/f'{stem}.ocr.v1.json').read_text(encoding='utf-8'))
        for line in ocr.get('lines',[]):elements.append({'id':line['id'],'classification':'ocr_text','sourceBBox':line['bbox'],'kind':'text','text':line['text']})
        output=a.output_dir/f'{stem}.inventory.json';output.write_text(json.dumps({'schema':'candidate-grounded-build-inventory/v1','source':ocr['source'],'elements':elements},ensure_ascii=False,indent=2),encoding='utf-8');rows.append({'slide':stem,'elements':len(elements),'output':str(output)})
    (a.output_dir/'manifest.json').write_text(json.dumps({'schema':'candidate-grounded-build-inventory-batch/v1','slides':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'slides':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
