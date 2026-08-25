#!/usr/bin/env python3
"""Create gallery retrieval query records only from locally verified candidates."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--review-dir',type=Path,required=True); ap.add_argument('--output-dir',type=Path,required=True); args=ap.parse_args(); args.output_dir.mkdir(parents=True,exist_ok=True)
    rows=[]
    for review_path in sorted(args.review_dir.glob('*.candidate-review.json')):
        review=json.loads(review_path.read_text(encoding='utf-8'))
        assets=[]
        for item in review.get('reviews',[]):
            if item.get('status')!='verified' or item.get('classification') not in {'library_asset','qwen_image_asset','decorative_movable'}: continue
            semantic=str(item.get('semantic') or '').strip()
            if not semantic: continue
            assets.append({'id':item['candidateId'],'kind':'icon','role':'asset','bbox':item['sourceBBox'],'semantic':semantic,'colorRoles':{},'candidateClassification':item['classification'],'confidence':item.get('confidence',0),'structuralVetoes':item.get('structuralVetoes',[])})
        payload={'schema':'candidate-verified-gallery-query/v1','source':review.get('source'),'imagegenAssets':assets,'sourceCropFinalUseForbidden':True}
        output=args.output_dir / review_path.name.replace('.candidate-review.json','.gallery-query.json')
        output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        rows.append({'slide':review_path.name,'assets':len(assets),'output':str(output)})
    (args.output_dir/'manifest.json').write_text(json.dumps({'schema':'candidate-gallery-query-batch/v1','slides':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'slides':len(rows),'assets':sum(x['assets'] for x in rows)},ensure_ascii=False))
if __name__=='__main__': main()
