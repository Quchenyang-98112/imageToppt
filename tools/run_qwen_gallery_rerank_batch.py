#!/usr/bin/env python3
"""Bounded concurrent slide-level Qwen gallery adjudication."""
from __future__ import annotations
import argparse, concurrent.futures, json, subprocess, sys
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',type=Path,required=True); ap.add_argument('--audit-dir',type=Path,required=True); ap.add_argument('--gallery-dir',type=Path,required=True); ap.add_argument('--workers',type=int,default=2); ap.add_argument('--max-assets',type=int,default=10); args=ap.parse_args()
    script=Path(__file__).with_name('qwen_gallery_rerank.py'); jobs=[]
    for audit in args.audit_dir.glob('*.nontext.audit.json'):
        stem=audit.name.replace('.nontext.audit.json',''); source=next((p for p in args.source_dir.iterdir() if p.stem==stem),None); matches=args.gallery_dir/f'{stem}.matches.json'; output=args.gallery_dir/f'{stem}.rerank.json'
        if source and matches.exists() and not output.exists(): jobs.append((source,audit,matches,output))
    def run(job):
        source,audit,matches,output=job; cmd=[sys.executable,str(script),'--source',str(source),'--audit',str(audit),'--matches',str(matches),'--output',str(output),'--max-assets',str(args.max_assets)]
        p=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace'); return {'source':str(source),'status':'completed' if p.returncode==0 else 'failed','output':str(output),'detail':(p.stdout or p.stderr)[-1000:]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,args.workers)) as pool: results=list(pool.map(run,jobs))
    payload={'schema':'qwen-gallery-rerank-batch/v1','results':results}; (args.gallery_dir/'rerank-manifest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False))
if __name__=='__main__': main()
