#!/usr/bin/env python3
"""Finalize a strict global-v2 batch report without turning failed QA into a pass."""
from __future__ import annotations
import json
from pathlib import Path

root=Path('output/sources-9-qwen-global-v2')
report=json.loads((root/'rebuild_execution_report.json').read_text(encoding='utf-8'))
reviews=[]
for file in sorted((root/'qa'/'render-review').glob('slide-??.review.json')):
    item=json.loads(file.read_text(encoding='utf-8')); reviews.append({'slide':file.stem,'textScore':item.get('textScore',0),'nontextScore':item.get('nontextScore',0),'fusionScore':item.get('fusionScore',0),'pass':item.get('pass',False),'majorMissing':sum(1 for x in item.get('missingFirst',[]) if x.get('severity')=='major')})
report.update({'status':'needs_review','pptx_built':True,'render_qa_done':True,'local_crop_qa_done':True,'validation_done':True,'final_candidate':str((root/'Qwen-only-全局策略v2-9页重建版.pptx').resolve()),'review_rounds':[{'round':1,'manifest':str((root/'qa'/'render-review'/'manifest.json').resolve())},{'round':2,'slide09':str((root/'qa'/'render-review'/'slide-09.round2.review.json').resolve())}],'hard_gate_result':{'pass':False,'reason':'Independent rendered comparison still finds major non-text omissions/wrong gallery matches. The candidate must not be represented as meeting the 80% non-text gate.','policy':'qwen-global-reconstruction-policy/v2'},'rendered_review':reviews})
(root/'rebuild_execution_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'reviewedSlides':len(reviews),'passingSlides':sum(x['pass'] for x in reviews),'candidate':report['final_candidate']},ensure_ascii=False))
