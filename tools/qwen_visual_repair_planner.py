#!/usr/bin/env python3
"""Create source-grounded non-text repair plans with Qwen-VL.

The planner is deliberately separated from the PPTX writer.  It compares the
normalized source with the actual rendered candidate, records only visual
repair actions, and classifies each action as native geometry, an independent
Qwen Image asset, or permitted fixed decoration.  This lets the executor make
deterministic edits without treating the source screenshot as a visible slide
background.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

from qwen_env import load_project_env


load_project_env(Path(__file__))


def data_url(path: Path) -> str:
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def parse_json(value: str) -> dict:
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Qwen-VL did not return a JSON object")
    payload = value[start : end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Some compatible endpoint responses contain a harmless trailing comma
        # despite response_format=json_object. Retry first; this cleanup merely
        # handles that syntax defect and never guesses a missing action.
        return json.loads(re.sub(r",\s*([}\]])", r"\1", payload))


SUPPORTED_COORDINATE_MODES = {"xywh_1600", "xyxy_1600", "normalized_1000_xyxy"}
NATIVE_INELIGIBLE = re.compile(r"\b(icon|logo|watermark|ribbon|flow|curved|wave|skyline|mountain|chart|illustration|progression arrows|multi[- ]stage)\b", re.I)


def clip_box(box: object, coordinate_mode: str) -> list[int] | None:
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        x, y, third, fourth = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if coordinate_mode == "xywh_1600":
        w, h = third, fourth
    elif coordinate_mode == "xyxy_1600":
        w, h = third - x, fourth - y
    elif coordinate_mode == "normalized_1000_xyxy":
        x, y, third, fourth = x * 1.6, y * 0.9, third * 1.6, fourth * 0.9
        w, h = third - x, fourth - y
    else:
        return None
    x, y, w, h = [int(round(value)) for value in (x, y, w, h)]
    x, y = max(0, min(1599, x)), max(0, min(899, y))
    w, h = max(1, min(1600 - x, w)), max(1, min(900 - y, h))
    return [x, y, w, h]


def action_contract(action: dict, coordinate_mode: str) -> tuple[list[int], list[int], float, str, int] | None:
    """Fail closed: a repair may only be staged with complete, round-trippable geometry."""
    box = clip_box(action.get("sourceBBox"), coordinate_mode)
    crop = clip_box(action.get("localCropReference"), coordinate_mode)
    raw_confidence = action.get("placementConfidence")
    parent_id = str(action.get("parentId") or "").strip()
    if not box or not crop or raw_confidence is None or not parent_id or "zIndex" not in action:
        return None
    try:
        confidence = float(raw_confidence)
        z_index = int(action["zIndex"])
    except (TypeError, ValueError):
        return None
    if not 0.80 <= confidence <= 1.0:
        return None
    x, y, width, height = box
    cx, cy, cwidth, cheight = crop
    if not (cx <= x and cy <= y and cx + cwidth >= x + width and cy + cheight >= y + height):
        return None
    # The VLM commonly returns the object bbox as its crop. Expand it
    # deterministically so the acceptance reviewer can see surrounding text,
    # hosts and neighbours and therefore detect new occlusion.
    x, y, width, height = box
    padding = 16
    review_x, review_y = max(0, x - padding), max(0, y - padding)
    review_right, review_bottom = min(1600, x + width + padding), min(900, y + height + padding)
    review_crop = [review_x, review_y, review_right - review_x, review_bottom - review_y]
    return box, review_crop, confidence, parent_id, z_index


def normalize_plan(value: dict, slide: int, source: Path, render: Path) -> dict:
    coordinate_mode = str(value.get("coordinateMode") or "")
    base = {
        "schema": "qwen-visual-repair-plan/v2",
        "slide": slide,
        "source": str(source),
        "render": str(render),
        "model": os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3-vl-plus",
        "coordinateMode": coordinate_mode,
        "candidateOnly": True,
        "acceptedForFinal": False,
    }
    if coordinate_mode not in SUPPORTED_COORDINATE_MODES:
        return {
            **base,
            "status": "rejected_coordinate_contract",
            "rejection": "coordinateMode must be explicitly declared as xywh_1600, xyxy_1600, or normalized_1000_xyxy",
            "nativeRepairs": [], "imageRepairs": [], "fixedDecorationRepairs": [],
            "notes": str(value.get("notes") or ""),
        }
    native: list[dict] = []
    for index, action in enumerate(value.get("nativeRepairs") or [], 1):
        if not isinstance(action, dict):
            continue
        contract = action_contract(action, coordinate_mode)
        kind = str(action.get("kind") or "").strip()
        semantic = str(action.get("semantic") or kind)
        operation = str(action.get("operation") or "")
        if not contract or operation not in {"replace_existing_native", "add_missing_native"} or kind not in {"rect", "roundRect", "ellipse", "line", "triangle", "trapezoid", "chevron", "rightArrow", "downArrow"} or NATIVE_INELIGIBLE.search(semantic):
            continue
        box, crop, confidence, parent_id, z_index = contract
        native.append({
            "id": f"repair-native-{slide:02d}-{index:02d}",
            "semantic": semantic,
            "operation": operation,
            "kind": kind,
            "sourceBBox": box,
            "bbox": box,
            "localCropReference": crop,
            "placementConfidence": confidence,
            "parentId": parent_id,
            "fill": str(action.get("fill") or "#1768B5"),
            "stroke": str(action.get("stroke") or "none"),
            "strokeWidth": max(0, min(8, float(action.get("strokeWidth") or 0))),
            "zIndex": z_index,
            "reason": str(action.get("reason") or "Qwen-VL source-vs-render repair"),
        })
    images: list[dict] = []
    for index, action in enumerate(value.get("imageRepairs") or [], 1):
        if not isinstance(action, dict):
            continue
        contract = action_contract(action, coordinate_mode)
        semantic = re.sub(r"\s+", " ", str(action.get("semantic") or "")).strip()
        prompt = re.sub(r"\s+", " ", str(action.get("prompt") or "")).strip()
        operation = str(action.get("operation") or "")
        if not contract or operation != "add_missing_asset" or not semantic or not prompt:
            continue
        box, crop, confidence, parent_id, z_index = contract
        images.append({
            "id": f"repair-s{slide:02d}-img-{index:02d}",
            "semantic": semantic,
            "operation": operation,
            "sourceBBox": box,
            "bbox": box,
            "localCropReference": crop,
            "placementConfidence": confidence,
            "parentId": parent_id,
            "zIndex": z_index,
            "prompt": prompt,
            "reason": str(action.get("reason") or "Qwen-VL source-vs-render repair"),
        })
    fixed: list[dict] = []
    for index, action in enumerate(value.get("fixedDecorationRepairs") or [], 1):
        if not isinstance(action, dict):
            continue
        contract = action_contract(action, coordinate_mode)
        semantic = re.sub(r"\s+", " ", str(action.get("semantic") or "")).strip()
        prompt = re.sub(r"\s+", " ", str(action.get("prompt") or "")).strip()
        operation = str(action.get("operation") or "")
        if not contract or operation != "add_fixed_decoration" or not semantic or not prompt:
            continue
        box, crop, confidence, parent_id, z_index = contract
        fixed.append({
            "id": f"repair-s{slide:02d}-fixed-{index:02d}",
            "semantic": semantic,
            "operation": operation,
            "sourceBBox": box,
            "bbox": box,
            "localCropReference": crop,
            "placementConfidence": confidence,
            "parentId": parent_id,
            "zIndex": z_index,
            "prompt": prompt,
            "reason": "permitted fixed decorative layer only",
        })
    return {
        **base,
        "status": "candidate_ready",
        "nativeRepairs": native[:8],
        "imageRepairs": images[:4],
        "fixedDecorationRepairs": fixed[:3],
        "notes": str(value.get("notes") or ""),
    }


def plan_one(job: tuple[int, Path, Path, Path, Path]) -> dict:
    slide, source, render, review, output = job
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("Qwen Vision credential is unavailable")
    base = (os.getenv("DASHSCOPE_VISION_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3-vl-plus"
    feedback = json.loads(review.read_text(encoding="utf-8")) if review.is_file() else {}
    compact_feedback = {
        "missingFirst": (feedback.get("missingFirst") or [])[:12],
        "wrong": (feedback.get("wrong") or [])[:12],
    }
    prompt = """You are the non-text repair planner for an editable PPTX reconstruction.
IMAGE A is the normalized 1600 x 900 source reference. IMAGE B is the actual
rendered editable PPTX. Create a conservative repair plan that improves only
non-text fidelity. Do NOT propose using IMAGE A as a whole-slide background,
do NOT reproduce text in images, and do NOT repair text.

Coordinate contract: set top-level `coordinateMode` to exactly
`normalized_1000_xyxy`. Every repair MUST contain `sourceBBox` as
[x1,y1,x2,y2] on a 0..1000 × 0..1000 normalized canvas. The executor will
deterministically convert it to the immutable 1600 × 900 source canvas.
Include `placementConfidence` (0 to 1), `parentId`, `zIndex`, and
`localCropReference` in the same normalized mode. Prioritize major missing or wrong visual elements from top to bottom,
left to right. Use `nativeRepairs` only for simple editable geometry (panels,
arrows, circles, separators, tabs, trapezoids). Every action MUST add an
`operation`: `replace_existing_native` only when it replaces a wrong existing
native object; `add_missing_native` only for a missing simple primitive;
`add_missing_asset` for one missing complex asset; `add_fixed_decoration` for
permitted nonselectable decoration. Never cover an existing component with a
second overlay. If a component has visible child primitives (for example, a
blue bar plus white dash, or a pill plus its chevrons), emit each primitive as
a related action. Never put an icon, logo,
watermark, ribbon, curved flow, skyline, mountain, chart, illustration, or
multi-stage progression into `nativeRepairs`. Use `imageRepairs` only for
those complex icons/illustrations/flows; each prompt must request ONE isolated
transparent-ready vector-like visual, no text/numbers/labels/cards. Use
`fixedDecorationRepairs` only for permitted non-selectable decoration such as
faint watermarks, skyline, ribbon, or abstract waves. Never include a logo or
any body text in fixedDecorationRepairs. Keep the total repair plan focused:
at most 8 native, 4 image, and 3 fixed-decoration actions. Prefer one major
semantic visual over several low-value construction fragments.

Return ONLY JSON:
{"coordinateMode":"normalized_1000_xyxy","nativeRepairs":[{"operation":"replace_existing_native|add_missing_native","semantic":"","kind":"roundRect|rect|ellipse|line|triangle|trapezoid|chevron|rightArrow|downArrow","sourceBBox":[0,0,1,1],"localCropReference":[0,0,1,1],"placementConfidence":0.95,"parentId":"slide","fill":"#RRGGBB|none","stroke":"#RRGGBB|none","strokeWidth":0,"zIndex":20,"reason":""}],"imageRepairs":[{"operation":"add_missing_asset","semantic":"","sourceBBox":[0,0,1,1],"localCropReference":[0,0,1,1],"placementConfidence":0.95,"parentId":"slide","zIndex":20,"prompt":"","reason":""}],"fixedDecorationRepairs":[{"operation":"add_fixed_decoration","semantic":"","sourceBBox":[0,0,1,1],"localCropReference":[0,0,1,1],"placementConfidence":0.95,"parentId":"slide","zIndex":5,"prompt":"","reason":""}],"notes":""}."""
    payload = {
        "model": model,
        "temperature": 0,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 3500,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "text", "text": "IMAGE A — normalized source reference (1600 x 900)"},
            {"type": "image_url", "image_url": {"url": data_url(source), "min_pixels": 65536, "max_pixels": 3200000}},
            {"type": "text", "text": "IMAGE B — current rendered editable candidate (1600 x 900)"},
            {"type": "image_url", "image_url": {"url": data_url(render), "min_pixels": 65536, "max_pixels": 3200000}},
            {"type": "text", "text": "Independent QA findings (use as evidence, verify visually):\n" + json.dumps(compact_feedback, ensure_ascii=False)},
        ]}],
    }
    started = time.time()
    parsed = None
    last_error: Exception | None = None
    for attempt in range(3):
        request = Request(base + "/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                reply = json.loads(response.read().decode("utf-8"))
            content = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            parsed = parse_json(content)
            break
        except Exception as error:
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    if parsed is None:
        raise RuntimeError(f"Qwen-VL repair plan could not be parsed after 3 attempts: {last_error}")
    result = normalize_plan(parsed, slide, source, render)
    result["elapsedMs"] = round((time.time() - started) * 1000)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"slide": slide, "status": "completed", "native": len(result["nativeRepairs"]), "images": len(result["imageRepairs"]), "fixed": len(result["fixedDecorationRepairs"]), "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--slides", default="", help="optional comma-separated one-based slide numbers for a bounded planner probe")
    args = parser.parse_args()
    names = ["李佳1.png", "李佳2.png", "李佳3.png", "识别1.png", "识别2.png", "识别3.png", "智慧养老.png", "b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png", "saas.png"]
    jobs = []
    selected = {int(value) for value in args.slides.split(",") if value.strip()} if args.slides.strip() else set(range(1, len(names) + 1))
    for index, name in enumerate(names, 1):
        if index not in selected:
            continue
        source = args.source_dir / name
        render = args.render_dir / f"slide-{index}.png"
        review = args.review_dir / f"slide-{index:02d}.review.json"
        output = args.output_dir / f"slide-{index:02d}.repair-plan.json"
        if not source.is_file() or not render.is_file():
            raise FileNotFoundError(f"missing source or render for slide {index}: {source} / {render}")
        jobs.append((index, source, render, review, output))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = list(executor.map(plan_one, jobs))
    manifest = {"schema": "qwen-visual-repair-plan-batch/v1", "model": os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3-vl-plus", "results": results}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
