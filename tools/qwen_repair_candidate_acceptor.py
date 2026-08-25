#!/usr/bin/env python3
"""Independently accept or reject one rendered non-text repair candidate.

The candidate deck must contain exactly one additional repair action over the
specified baseline.  Qwen-VL compares the same source-pixel crop in source,
baseline and candidate renders.  This intentionally fails closed: a candidate
without a declared round-trippable box, an isolated render, score improvement
and no-occlusion result may never be included in a final composite.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from qwen_env import load_project_env


load_project_env(Path(__file__))


def data_url(image: Image.Image) -> str:
    payload = io.BytesIO()
    image.save(payload, "PNG")
    return "data:image/png;base64," + base64.b64encode(payload.getvalue()).decode("ascii")


def parse_json(value: str) -> dict:
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Qwen-VL did not return a JSON object")
    return json.loads(value[start:end + 1])


def source_box(action: dict) -> list[int]:
    value = action.get("sourceBBox") or action.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("action lacks canonical sourceBBox")
    x, y, w, h = [int(round(float(v))) for v in value]
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1600 or y + h > 900:
        raise ValueError("action sourceBBox is outside immutable 1600x900 reference")
    return [x, y, w, h]


def crop_box(action: dict) -> list[int]:
    value = action.get("localCropReference")
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("action lacks localCropReference")
    x, y, w, h = [int(round(float(v))) for v in value]
    target = source_box(action)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1600 or y + h > 900:
        raise ValueError("localCropReference is outside immutable reference")
    tx, ty, tw, th = target
    if not (x <= tx and y <= ty and x + w >= tx + tw and y + h >= ty + th):
        raise ValueError("localCropReference does not enclose sourceBBox")
    return [x, y, w, h]


def find_action(plan: dict, action_id: str) -> dict:
    for key in ("nativeRepairs", "imageRepairs", "fixedDecorationRepairs"):
        for action in plan.get(key) or []:
            if isinstance(action, dict) and action.get("id") == action_id:
                return action
    raise ValueError(f"repair action not found: {action_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not all(path.is_file() for path in (args.source, args.baseline, args.candidate, args.plan)):
        raise FileNotFoundError("source, baseline, candidate and plan must all exist")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("coordinateMode") not in {"xywh_1600", "xyxy_1600", "normalized_1000_xyxy"}:
        raise ValueError("plan has no declared supported coordinateMode")
    action = find_action(plan, args.action_id)
    box = source_box(action)
    crop = crop_box(action)
    source = Image.open(args.source).convert("RGB")
    baseline = Image.open(args.baseline).convert("RGB")
    candidate = Image.open(args.candidate).convert("RGB")
    if source.size != (1600, 900) or baseline.size != (1600, 900) or candidate.size != (1600, 900):
        raise ValueError("all acceptance images must use immutable 1600x900 source pixels")
    x, y, width, height = crop
    images = [image.crop((x, y, x + width, y + height)) for image in (source, baseline, candidate)]
    semantic = re.sub(r"\s+", " ", str(action.get("semantic") or args.action_id)).strip()
    prompt = f'''You are the independent local acceptance gate for ONE editable
PowerPoint non-text repair. IMAGE A is the source crop; IMAGE B is the same
crop before this one repair; IMAGE C is the same crop after this one repair.
The target repair is {semantic!r}, source box {box}, inside crop {crop}.
Judge only visible non-text fidelity. Never reward a candidate for covering
source text, hiding an error with a panel, or using source pixels as a baked
background. Be strict about position, size, silhouette, direction, colour,
and z-layer. Return ONLY JSON:
{{"baselineScore":0..1,"candidateScore":0..1,"improvement":-1..1,
"noNewTextOverlap":true|false,"noNewNontextOcclusion":true|false,
"coordinatePlacementPassed":true|false,"actionVisible":true|false,
"accept":true|false,"reason":""}}
Accept only if the candidate visibly improves the target by at least 0.02,
has no new text overlap or non-text occlusion, has correct placement, and the
new action is visibly present.'''
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("Qwen Vision credential is unavailable")
    base = (os.getenv("DASHSCOPE_VISION_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3-vl-plus"
    request_body = {
        "model": model, "temperature": 0, "enable_thinking": False,
        "response_format": {"type": "json_object"}, "max_completion_tokens": 1600,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "text", "text": "IMAGE A — source crop"}, {"type": "image_url", "image_url": {"url": data_url(images[0])}},
            {"type": "text", "text": "IMAGE B — baseline crop"}, {"type": "image_url", "image_url": {"url": data_url(images[1])}},
            {"type": "text", "text": "IMAGE C — one-action candidate crop"}, {"type": "image_url", "image_url": {"url": data_url(images[2])}},
        ]}],
    }
    started = time.time()
    request = Request(base + "/chat/completions", data=json.dumps(request_body).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        reply = json.loads(response.read().decode("utf-8"))
    content = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    verdict = parse_json(content)
    baseline_score = float(verdict.get("baselineScore") or 0)
    candidate_score = float(verdict.get("candidateScore") or 0)
    accepted = bool(
        candidate_score - baseline_score >= 0.02
        and verdict.get("noNewTextOverlap") is True
        and verdict.get("noNewNontextOcclusion") is True
        and verdict.get("coordinatePlacementPassed") is True
        and verdict.get("actionVisible") is True
        and verdict.get("accept") is True
    )
    result = {
        "schema": "qwen-repair-candidate-acceptance/v1",
        "status": "accepted_for_final" if accepted else "rejected",
        "acceptedForFinal": accepted,
        "actionId": args.action_id,
        "semantic": semantic,
        "sourceBBox": box,
        "localCropReference": crop,
        "model": model,
        "baseline": str(args.baseline), "candidate": str(args.candidate), "source": str(args.source),
        "verdict": verdict,
        "computedImprovement": round(candidate_score - baseline_score, 4),
        "elapsedMs": round((time.time() - started) * 1000),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "actionId": args.action_id, "improvement": result["computedImprovement"]}, ensure_ascii=False))
    if not accepted:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
