import argparse, concurrent.futures, json, subprocess, sys
from pathlib import Path
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',type=Path,required=True); ap.add_argument('--render-dir',type=Path,required=True); ap.add_argument('--output-dir',type=Path,required=True); ap.add_argument('--workers',type=int,default=2); a=ap.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True); script=Path(__file__).with_name('qwen_slide_review.py'); jobs=[]
    # Must match the build executor's locale-aware source order exactly.  A
    # source/render mismatch makes visual scores meaningless, so this mapping
    # is an explicit QA contract rather than a historical default order.
    names=['李佳1.png','李佳2.png','李佳3.png','识别1.png','识别2.jpg','识别3.png','智慧养老.png','b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png','saas.png']
    for i,n in enumerate(names,1):
        # Artifact Tool's renderer uses slide-1.png while some legacy renderers
        # use slide-01.png. Accept either so visual QA is not silently skipped.
        render = a.render_dir / f'slide-{i:02d}.png'
        if not render.exists(): render = a.render_dir / f'slide-{i}.png'
        jobs.append((i,a.source_dir/n,render,a.output_dir/f'slide-{i:02d}.review.json'))
    def run(job):
        i,s,r,o=job
        if not s.exists() or not r.exists(): return {'slide':i,'status':'failed','error':'missing input'}
        cmd=[sys.executable,str(script),'--source',str(s),'--render',str(r),'--output',str(o)]
        p=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace')
        if p.returncode: return {'slide':i,'status':'failed','error':p.stderr[-1200:]}
        try: v=json.loads(o.read_text(encoding='utf-8')); return {'slide':i,'status':'completed','textScore':v.get('textScore',0),'nontextScore':v.get('nontextScore',0),'fusionScore':v.get('fusionScore',0),'pass':v.get('pass',False)}
        except Exception as e: return {'slide':i,'status':'failed','error':str(e)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex: results=list(ex.map(run,jobs))
    out={'schema':'qwen-slide-review-batch/v1','workers':a.workers,'results':results}; (a.output_dir/'manifest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
