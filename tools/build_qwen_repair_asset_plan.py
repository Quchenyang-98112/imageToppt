#!/usr/bin/env python3
"""Turn Qwen-VL repair plans into compact Qwen Image grid requests."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-per-slide", type=int, default=2, help="generate only the highest-impact missing complex assets per slide")
    parser.add_argument("--min-area", type=int, default=6000, help="skip tiny assets in the first missing-first generation pass")
    args = parser.parse_args()
    sheets = []
    for plan_file in sorted(args.repair_dir.glob("slide-*.repair-plan.json")):
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        slide = int(plan["slide"])
        assets = []
        # Constrain image generation to the highest-impact repair items.  Qwen
        # is better at a focused 3×3 grid than a large, noisy asset set.
        ranked_images = sorted(
            (plan.get("imageRepairs") or []),
            key=lambda action: float((action.get("sourceBBox") or action.get("bbox") or [0, 0, 0, 0])[2]) * float((action.get("sourceBBox") or action.get("bbox") or [0, 0, 0, 0])[3]),
            reverse=True,
        )
        selected_images = []
        for action in ranked_images:
            bbox = action.get("sourceBBox") or action.get("bbox") or [0, 0, 0, 0]
            try:
                area = float(bbox[2]) * float(bbox[3])
            except (TypeError, ValueError, IndexError):
                area = 0
            semantic = str(action.get("semantic") or "").lower()
            if area < args.min_area or any(token in semantic for token in ("logo", "watermark", "arrow", "lightbulb")):
                continue
            selected_images.append(action)
            if len(selected_images) >= max(0, args.max_per_slide):
                break
        for action in selected_images:
            assets.append({"id": action["id"], "semantic": action["semantic"], "colorRoles": {"foreground": "#1768B5", "secondary": "#DDECF9"}, "repairType": "editable_complex_asset"})
        for action in (plan.get("fixedDecorationRepairs") or []):
            semantic = str(action.get("semantic") or "").lower()
            # Brand marks must use the dedicated exact-brand route, never a
            # generative approximation inside the decoration layer.
            if any(token in semantic for token in ("logo", "avic", "fastcode", "watermark")):
                continue
            assets.append({"id": action["id"], "semantic": action["semantic"], "colorRoles": {"foreground": "#DDECF9", "secondary": "#1768B5"}, "repairType": "fixed_decoration"})
            break
        if not assets:
            continue
        # Qwen Image is most reliable at no more than 9 distinct visual cells.
        for batch_index in range(0, len(assets), 9):
            batch = assets[batch_index : batch_index + 9]
            # Background silhouettes and long flowing visuals frequently cause
            # Qwen Image to ignore a multi-cell grid. Generate that whole page
            # one asset at a time, which gives deterministic crop boundaries.
            # Isolate only the long/compound visual itself. Previously a single
            # ribbon in a batch forced every unrelated small icon into a
            # separate image-generation call, adding latency without adding
            # fidelity.
            standalone_tokens = ("mountain", "wave", "ribbon", "skyline", "timeline", "flow element", "background pattern")
            standalone = [item for item in batch if any(token in str(item["semantic"]).lower() for token in standalone_tokens)]
            grid_items = [item for item in batch if item not in standalone]
            groups = [[item] for item in standalone] + ([grid_items] if grid_items else [])
            source_actions = {item["id"]: item for item in (plan.get("imageRepairs") or []) + (plan.get("fixedDecorationRepairs") or [])}
            for group_index, group in enumerate(groups, 1):
                single_asset_mode = len(group) == 1 and group[0] in standalone
                cols = 1 if single_asset_mode else 3
                rows = 1 if single_asset_mode else math.ceil(len(group) / cols)
                # Grid extraction requires an explicit item for every cell.
                while len(group) < cols * rows:
                    group.append({
                        "id": f"repair-s{slide:02d}-padding-{batch_index // 9 + 1:02d}-{group_index:02d}-{len(group)+1:02d}",
                        "semantic": "small neutral blue dot placeholder (unused)",
                        "colorRoles": {"foreground": "#DDECF9"},
                        "repairType": "unused_grid_padding",
                    })
                prompts = []
                for index, asset in enumerate(group, 1):
                    action = source_actions.get(asset["id"])
                    visual_prompt = str(action["prompt"]) if action else "one tiny neutral pale-blue dot, isolated and unused"
                    prompts.append(f"({index}) {one_line(visual_prompt)}")
                prompt = (
                    f"Create a strict {cols} columns × {rows} rows grid of isolated presentation visual assets on a solid pure chroma-key magenta #FF00FF background. "
                    "Equal cells with visible gutters; each visual centered in the central 60% with at least 25% empty magenta padding. "
                    "No readable text, numbers, letters, labels, card frames, full-slide background, shadows, clipping or overlap. "
                    "Each cell must contain exactly one transparent-ready clean vector-like icon or decoration. "
                    "Cells left-to-right, top-to-bottom: " + "; ".join(prompts)
                )
                number = batch_index // 9 + group_index
                sheets.append({"id": f"repair-s{slide:02d}-{number:02d}", "columns": cols, "rows": rows, "size": "1328*1328", "prompt": prompt, "assets": group})
    result = {"schema": "qwen-repair-asset-grid-plan/v1", "sheets": sheets}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "completed", "sheets": len(sheets), "assets": sum(len(item["assets"]) for item in sheets)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
