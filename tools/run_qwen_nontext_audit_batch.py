#!/usr/bin/env python3
"""Run the global Qwen non-text audit in bounded parallel workers."""
from __future__ import annotations

import argparse, concurrent.futures, json, subprocess, sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--ocr-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--round', type=int, default=1)
    args = parser.parse_args()
    script = Path(__file__).with_name('qwen_nontext_audit.py')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for image in sorted(args.input_dir.iterdir()):
        if image.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
            continue
        ocr = args.ocr_dir / f'{image.stem}.ocr.v1.json'
        if not ocr.exists():
            continue
        out = args.output_dir / f'{image.stem}.nontext.audit.json'
        jobs.append((image, ocr, out))

    def run(item):
        image, ocr, out = item
        if out.exists():
            try:
                payload = json.loads(out.read_text(encoding='utf-8'))
                if payload.get('schema') == 'qwen-nontext-audit/v2' and payload.get('bbox_contract') == '[x,y,w,h] canonical from Qwen' and args.round <= int(payload.get('round', 0)):
                    return {'source': str(image), 'output': str(out), 'status': 'reused', 'nativeObjects': len(payload.get('nativeObjects', [])), 'imagegenAssets': len(payload.get('imagegenAssets', [])), 'reviewScore': payload.get('review', {}).get('reviewScore', 0)}
            except Exception:
                pass
        command = [sys.executable, str(script), '--image', str(image), '--ocr-record', str(ocr), '--output', str(out), '--round', str(args.round)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if completed.returncode:
            return {'source': str(image), 'output': str(out), 'status': 'failed', 'error': completed.stderr[-1000:]}
        try:
            payload = json.loads(out.read_text(encoding='utf-8'))
            return {'source': str(image), 'output': str(out), 'status': 'completed', 'nativeObjects': len(payload.get('nativeObjects', [])), 'imagegenAssets': len(payload.get('imagegenAssets', [])), 'reviewScore': payload.get('review', {}).get('reviewScore', 0)}
        except Exception as exc:
            return {'source': str(image), 'output': str(out), 'status': 'failed', 'error': str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(run, jobs))
    manifest = {'schema': 'qwen-nontext-audit-batch/v1', 'workers': args.workers, 'results': results}
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
