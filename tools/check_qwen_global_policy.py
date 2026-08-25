#!/usr/bin/env python3
"""Validate the project-wide Qwen-only dual-route policy before a batch run."""
from __future__ import annotations

import argparse, json, os
from pathlib import Path
import sys

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
from qwen_env import load_project_env


def main() -> None:
    load_project_env(Path(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy', type=Path, default=Path(__file__).resolve().parents[1] / 'config' / 'qwen-global-policy.json')
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding='utf-8'))
    models = policy['models']
    configured = {
        'ocr': os.getenv('DASHSCOPE_OCR_MODEL') or models['ocr'],
        'vision': os.getenv('DASHSCOPE_VISION_MODEL') or models['vision'],
        'image_generation': os.getenv('DASHSCOPE_IMAGE_EDIT_MODEL') or models['image_generation'],
    }
    violations = []
    if policy.get('schema') != 'qwen-global-reconstruction-policy/v3':
        violations.append('policy schema must be qwen-global-reconstruction-policy/v3')
    if policy.get('mode') != 'qwen_only_parallel_workbench':
        violations.append('parallel workbench mode is mandatory')
    normalization = policy.get('workbench', {}).get('source_reference_normalization', {})
    if normalization != {'width': 1600, 'height': 900, 'mode': 'contain_with_letterbox', 'immutable_after_creation': True}:
        violations.append('immutable 1600x900 contain-with-letterbox SOURCE_REFERENCE normalization is mandatory')
    if policy.get('coordinate_contract', {}).get('canonical_bbox') != '[x,y,w,h] source pixels':
        violations.append('canonical source-pixel bbox contract missing')
    repair_bbox = policy.get('coordinate_contract', {}).get('repair_bbox_protocol', {})
    if repair_bbox.get('reject_when_undeclared_or_unroundtrippable') is not True:
        violations.append('repair bbox protocol must fail closed on undeclared coordinate modes')
    if set(repair_bbox.get('supported_coordinate_modes', [])) != {'xywh_1600', 'xyxy_1600', 'normalized_1000_xyxy'}:
        violations.append('repair bbox protocol must enumerate only supported coordinate modes')
    discovery = policy.get('nontext_discovery_policy', {})
    if discovery.get('mode') != 'candidate_grounded_local_verification':
        violations.append('candidate-grounded local non-text verification is mandatory')
    if discovery.get('detector', {}).get('runs_before_vlm') is not True:
        violations.append('non-text candidate detector must run before VLM')
    if discovery.get('verification', {}).get('unverified_candidate_blocks_fusion') is not True:
        violations.append('unverified non-text candidates must block fusion')
    if 'numeric_text_as_chart_asset' not in discovery.get('forbid', []):
        violations.append('numeric OCR-to-asset misclassification must be forbidden')
    background = policy.get('background_policy', {})
    if background.get('mode') not in {'clean_background_from_union_foreground_mask', 'background_first_clean_from_union_foreground_mask'}:
        violations.append('union-foreground-mask clean-background policy missing')
    if background.get('unresolved_region_export') != 'forbidden':
        violations.append('unresolved background regions must block export')
    if not background.get('whole_slide_regeneration_forbidden'):
        violations.append('whole-slide background regeneration must be forbidden')
    if not background.get('source_pixels_outside_mask_must_remain_unchanged'):
        violations.append('outside-mask source-pixel identity rule missing')
    if not policy.get('workbench', {}).get('forbid_visible_full_source_reference'):
        violations.append('visible full-source reference must be forbidden')
    expected_canvases = {'SOURCE_REFERENCE', 'BASE_BG', 'FIXED_DECOR', 'BG_CLEAN', 'OCR_LAYER', 'NONTEXT_LAYER', 'COMPOSITE', 'DIFF_HEATMAP'}
    if set(policy.get('workbench', {}).get('canvases', [])) != expected_canvases:
        violations.append('eight-canvas workbench contract is incomplete')
    if policy.get('gallery_policy', {}).get('preview_png_is_retrieval_only') is not True:
        violations.append('gallery preview PNG must be retrieval-only')
    if policy.get('gallery_policy', {}).get('query_crop_final_use_forbidden') is not True:
        violations.append('source query crops must be forbidden as final assets')
    vector_policy = policy.get('complex_asset_format_policy', {})
    if vector_policy.get('preferred_format') != 'real_vector_svg':
        violations.append('complex assets must prefer validated real-vector SVG')
    if 'has_no_embedded_raster_image' not in vector_policy.get('svg_requirements', []):
        violations.append('SVG validation must forbid embedded raster payloads')
    asset_preflight = policy.get('generated_asset_preflight', {})
    if asset_preflight.get('required_before_candidate_insertion') is not True or asset_preflight.get('minimum_score') != .88:
        violations.append('generated asset preflight must gate candidate insertion at 0.88')
    candidate_acceptance = policy.get('repair_loop', {}).get('candidate_repair_acceptance', {})
    if candidate_acceptance.get('mode') != 'candidate_only_before_final_fusion' or candidate_acceptance.get('acceptance_record_required') is not True:
        violations.append('repair candidate acceptance evidence is mandatory before final fusion')
    action_contract = policy.get('repair_loop', {}).get('repair_action_contract', {})
    if set(action_contract.get('allowed_operations', [])) != {'replace_existing_native', 'add_missing_native', 'add_missing_asset', 'add_fixed_decoration'}:
        violations.append('repair actions must use explicit replace/add operation semantics')
    if 'existing_component_rule' not in action_contract or 'compound_component_rule' not in action_contract:
        violations.append('repair contract must protect existing and compound components')
    required_views = {'background_only', 'ocr_on_neutral', 'nontext_on_neutral', 'composite', 'difference_heatmap', 'move_test', 'delete_test'}
    if not required_views.issubset(set(policy.get('qa_views', []))):
        violations.append('rendered QA views are incomplete')
    gates = policy.get('quality_gates', {})
    for key, expected in {
        'text_min_score': .90,
        'nontext_min_score': .80,
        'nontext_major_recall': .95,
        'background_min_score': .98,
        'background_ocr_rescan_max_lines': 0,
        'decoration_visual_completeness': .95,
    }.items():
        if gates.get(key) != expected:
            violations.append(f'{key} must equal {expected}')
    for key, expected in models.items():
        if configured[key] != expected:
            violations.append(f'{key}={configured[key]} != {expected}')
    result = {
        'schema': 'qwen-global-policy-check/v3',
        'status': 'passed' if not violations else 'blocked',
        'uses_openai_or_gpt': False,
        'policy': str(args.policy.resolve()),
        'models': configured,
        'thresholds': policy['quality_gates'],
        'violations': violations,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if violations:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
