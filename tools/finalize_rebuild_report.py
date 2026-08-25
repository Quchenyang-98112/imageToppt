import json
from pathlib import Path
root=Path('output/sources-9-qwen-optimized')
report=json.loads((root/'rebuild_execution_report.json').read_text(encoding='utf-8'))
review=json.loads((root/'qa/qwen_review_round2/manifest.json').read_text(encoding='utf-8'))
report['status']='needs_review'
report['pptx_built']=True
report['render_qa_done']=True
report['local_crop_qa_done']=True
report['validation_done']=True
report['final_candidate']=str((root/'Qwen-only-图库优先-9页混合优化版.pptx').resolve())
report['review_rounds']=[{'round':1,'manifest':str((root/'qa/qwen_review/manifest.json').resolve())},{'round':2,'manifest':str((root/'qa/qwen_review_round2/manifest.json').resolve())}]
report['hard_gate_result']={'pass':False,'reason':'Qwen independent review still finds major non-text omissions/wrong matches on several pages; candidate is delivered as needs_review, not claimed as >=80/90 pass.','thresholds':report.get('thresholds',{})}
report['qwen_review_round2']=review
(root/'rebuild_execution_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'final_candidate':report['final_candidate'],'review':review},ensure_ascii=False,indent=2))
