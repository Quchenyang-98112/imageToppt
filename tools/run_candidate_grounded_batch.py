#!/usr/bin/env python3
"""Run deterministic candidate detection then bounded local Qwen verification."""
from __future__ import annotations

import argparse, concurrent.futures, json, subprocess, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-dir', type=Path, required=True)
    ap.add_argument('--ocr-dir', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--max-candidates', type=int, default=48)
    args = ap.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    detector, verifier = here / 'vision' / 'detect_nontext_candidates.py', here / 'qwen_local_candidate_review.py'
    jobs = []
    for image in sorted(args.source_dir.glob('*.png')):
        ocr = args.ocr_dir / f'{image.stem}.ocr.v1.json'
        if ocr.exists(): jobs.append((image, ocr, args.output_dir / f'{image.stem}.candidates.json', args.output_dir / f'{image.stem}.candidate-review.json'))

    def run(job):
        image, ocr, candidate, review = job
        detect = subprocess.run([sys.executable, str(detector), '--image', str(image), '--ocr-record', str(ocr), '--output', str(candidate), '--max-candidates', str(args.max_candidates)], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if detect.returncode: return {'slide': image.name, 'status': 'failed_detector', 'error': detect.stderr[-800:]}
        verify = subprocess.run([sys.executable, str(verifier), '--image', str(image), '--ocr-record', str(ocr), '--candidates', str(candidate), '--output', str(review)], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if verify.returncode: return {'slide': image.name, 'status': 'failed_verifier', 'error': verify.stderr[-800:]}
        payload = json.loads(review.read_text(encoding='utf-8'))
        return {'slide': image.name, 'status': 'completed', **payload['summary'], 'candidateInventory': str(candidate), 'review': str(review)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(run, jobs))
    rows.sort(key=lambda row: row['slide'])
    complete = all(row['status'] == 'completed' and row.get('unverified', 1) == 0 for row in rows)
    manifest = {'schema': 'candidate-grounded-nontext-batch/v1', 'uses_openai_or_gpt': False, 'model': 'qwen3-vl-plus', 'slides': rows, 'passed': complete}
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'passed' if complete else 'needs_review', 'slides': len(rows), 'output': str(args.output_dir / 'manifest.json')}, ensure_ascii=False))
    if not complete: raise SystemExit(2)


if __name__ == '__main__': main()
