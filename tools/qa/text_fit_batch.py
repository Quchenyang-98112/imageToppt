#!/usr/bin/env python3
"""Batch wrapper around the imported Knight skill's ppt_text_fit helper."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def load_helper():
    root = Path(__file__).resolve().parents[2]
    path = root / 'reference' / 'knight-imagetopptx-skill' / 'scripts' / 'ppt_text_fit.py'
    spec = importlib.util.spec_from_file_location('knight_ppt_text_fit', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load text-fit helper: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    # This helper is also launched by Node during export.  Own the encoding
    # instead of relying on PYTHONIOENCODING being configured by the caller.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    helper = load_helper()
    items = json.loads(args.input.read_text(encoding='utf-8'))
    results = []
    notfits = []
    for item in items:
        intended = max(5.0, float(item.get('intendedPt') or 12))
        source_px = max(6.0, float(item.get('sourceFontPx') or intended / .6))
        explicit_lines = max(1, str(item.get('text') or '').count('\n') + 1)
        geometric_lines = max(1, round(float(item.get('h') or source_px) / max(1.0, source_px * 1.15)))
        fit_args = SimpleNamespace(
            text=str(item.get('text') or ''),
            box=f"{max(2, float(item.get('w') or 2))}x{max(2, float(item.get('h') or 2))}",
            font='Microsoft YaHei', bold=bool(item.get('bold')), min_pt=max(5.0, intended * .55), max_pt=intended,
            max_lines=max(explicit_lines, geometric_lines), line_spacing=1.06, width_safety=.92,
            height_safety=.95, render_fudge=1.01, slide_px='1600x900', slide_in='13.333333x7.5',
        )
        measured = helper.best_fit(fit_args)
        result = {'id': item.get('id'), 'recommendedPt': measured['recommended_pt'], 'fits': measured['fits'], 'lineCount': measured['line_count']}
        results.append(result)
        if not measured['fits']:
            notfits.append(result)
    print(json.dumps({'results': results, 'report': {'called': len(results), 'notfits': notfits, 'exceptions': [], 'key_long_texts': [item.get('id') for item in items if len(str(item.get('text') or '')) >= 18][:12]}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
