#!/usr/bin/env python3
"""Small deterministic smoke test for the v3 clean-background proof chain."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='ppt-v3-test-') as raw:
        folder = Path(raw)
        source = folder / 'source.png'; clean = folder / 'clean.png'; mask = folder / 'mask.png'
        inventory = folder / 'inventory.json'; build_report = folder / 'build.json'; audit_report = folder / 'audit.json'; ocr = folder / 'ocr.json'
        text_evidence=folder/'text.json';nontext_evidence=folder/'nontext.json';fusion_evidence=folder/'fusion.json';editability=folder/'editability.json';route_report=folder/'routes.json'
        image = Image.new('RGB', (320, 180), (238, 243, 249)); draw = ImageDraw.Draw(image)
        draw.rectangle((60, 60, 155, 84), fill=(20, 40, 80))
        image.save(source)
        inventory.write_text(json.dumps({'elements': [{
            'id': 'ocr-001', 'reconstructionClass': 'ocr_text', 'sourceBBox': [60, 60, 96, 25],
            'zIndex': 1, 'placementConfidence': 1, 'parentId': None,
        }]}), encoding='utf-8')
        ocr.write_text(json.dumps({'lines': []}), encoding='utf-8')
        subprocess.run([sys.executable, str(project / 'tools/vision/build_clean_background.py'), '--source', str(source), '--inventory', str(inventory), '--output', str(clean), '--mask-output', str(mask), '--report', str(build_report)], check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, str(project / 'tools/vision/audit_clean_background.py'), '--source', str(source), '--clean', str(clean), '--inventory', str(inventory), '--output', str(audit_report), '--ocr-rescan-record', str(ocr), '--decoration-score', '1', '--seam-score', '1'], check=True, capture_output=True, text=True)
        build = json.loads(build_report.read_text(encoding='utf-8')); audit = json.loads(audit_report.read_text(encoding='utf-8'))
        assert build['wholeSlideRegenerated'] is False
        assert build['outsideMaskPixelIdentity'] == 1.0
        assert audit['passed'] is True
        assert audit['outsideMaskPixelIdentity'] == 1.0
        assert audit['ocrRescanLines'] == 0
        text_evidence.write_text(json.dumps({'renderScore':.95,'contentAccuracy':1,'criticalTextAccuracy':1,'bboxMeanIou':.95,'styleMaxRelativeError':.03,'colorMaxDeltaE':2,'visibleObjectCoverage':1,'spellingOrOcrErrors':0}),encoding='utf-8')
        nontext_evidence.write_text(json.dumps({'renderScore':.9,'majorRecall':1,'allRecall':.96,'bboxMeanIou':.9,'sizeMaxRelativeError':.03,'aspectMaxRelativeError':.02,'missingMajorElements':0,'structuralVetoes':0,'layeringPassed':True}),encoding='utf-8')
        fusion_evidence.write_text(json.dumps({'renderScore':.9,'textNontextAlignmentPassed':True,'layeringPassed':True,'backgroundInflatedScore':False}),encoding='utf-8')
        editability.write_text(json.dumps({'passed':True,'hardFailures':[]}),encoding='utf-8')
        subprocess.run([sys.executable,str(project/'tools/qa/validate_route_evidence.py'),'--text',str(text_evidence),'--nontext',str(nontext_evidence),'--background',str(audit_report),'--fusion',str(fusion_evidence),'--editability',str(editability),'--output',str(route_report)],check=True,capture_output=True,text=True)
        routes=json.loads(route_report.read_text(encoding='utf-8'));assert routes['fusionAuthorized'] is True
        print(json.dumps({'schema': 'global-v3-smoke-test/v1', 'passed': True, 'build': build['status'], 'audit': audit['schema']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
