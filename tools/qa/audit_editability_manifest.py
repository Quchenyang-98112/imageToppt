#!/usr/bin/env python3
"""Hard gate a PPT shape-tree and move/delete-test manifest."""
from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();value=json.loads(args.manifest.read_text(encoding='utf-8'))
    failures=[];source_hash=str(value.get('sourceImageHash') or '')
    visible_pictures=value.get('visiblePictures') or []
    full_source=[x for x in visible_pictures if isinstance(x,dict) and float(x.get('slideAreaRatio',0))>=.90 and (not source_hash or x.get('sha256')==source_hash)]
    if full_source:failures.append('visible_full_source_reference')
    expected=int(value.get('expectedOcrObjects',0));actual=int(value.get('visibleOcrObjects',0))
    if expected!=actual:failures.append(f'ocr_object_coverage:{actual}/{expected}')
    moves=value.get('moveTests') or []
    if not moves:failures.append('move_tests_missing')
    for row in moves:
        if isinstance(row,dict) and float(row.get('originDifferenceFromBgClean',1))>.01:failures.append(f"move_residual:{row.get('elementId','unknown')}")
    deletes=value.get('deleteTests') or []
    if not deletes or any(not bool(x.get('passed')) for x in deletes if isinstance(x,dict)):failures.append('delete_test_failed_or_missing')
    if not bool(value.get('foregroundOnlyRenderPassed')):failures.append('foreground_only_render_failed')
    if not bool(value.get('stableObjectNamesPassed')):failures.append('stable_object_names_failed')
    if not bool(value.get('componentGroupingPassed')):failures.append('component_grouping_failed')
    result={'schema':'pptx-editability-audit/v3','passed':not failures,'hardFailures':failures,'expectedOcrObjects':expected,'visibleOcrObjects':actual,'visibleFullSourceImages':len(full_source),'moveTests':len(moves),'deleteTests':len(deletes)}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    if failures:raise SystemExit(2)
if __name__=='__main__':main()
