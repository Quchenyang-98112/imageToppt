#!/usr/bin/env python3
"""Generate and validate Qwen Image icon grids for Knight asset execution.

This is deliberately limited to generated visual assets: it requests a
magenta-background grid, derives a per-cell edge background model, converts
each cell to a standalone alpha PNG, removes unsafe edge fragments, centres by
alpha centroid, and records provenance for every final asset.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
from PIL import Image, ImageDraw

from qwen_env import load_project_env


load_project_env(Path(__file__))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def download_qwen_image(prompt: str, size: str, target: Path) -> dict:
    key = os.getenv("DASHSCOPE_IMAGE_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("Qwen Image credential is unavailable")
    model = os.getenv("DASHSCOPE_IMAGE_EDIT_MODEL") or "qwen-image-2.0-pro"
    endpoint = os.getenv("DASHSCOPE_IMAGE_EDIT_ENDPOINT") or "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    request_payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {"n": 1, "size": size},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    value = None
    last_error: Exception | None = None
    # Qwen Image limits burst traffic. Bounded retry keeps the complete batch
    # deterministic while avoiding an uncontrolled concurrent retry storm.
    for attempt, delay in enumerate((0, 15, 30), 1):
        if delay:
            time.sleep(delay)
        request = Request(
            endpoint,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                value = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            last_error = error
            if error.code != 429 or attempt == 3:
                raise
    if value is None:
        raise RuntimeError(str(last_error or "Qwen Image request failed"))
    contents = (((value.get("output") or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or []
    image_url = next((item.get("image") for item in contents if isinstance(item, dict) and item.get("image")), None)
    if not image_url:
        raise RuntimeError(str((value.get("error") or {}).get("message") or "Qwen Image returned no image URL"))
    with urlopen(image_url, timeout=300) as response:
        target.write_bytes(response.read())
    return {"model": model, "endpoint": endpoint, "elapsedMs": round((time.time() - started) * 1000), "source": str(target)}


def rgba_distance(rgb: np.ndarray, color: np.ndarray) -> np.ndarray:
    delta = rgb.astype(np.float32) - color.astype(np.float32)
    return np.sqrt(np.sum(delta * delta, axis=2))


def edge_samples(rgb: np.ndarray, border: int = 4) -> np.ndarray:
    h, w, _ = rgb.shape
    b = min(border, max(1, min(h, w) // 6))
    return np.concatenate((rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)), axis=0)


def alpha_bbox(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(alpha > 20)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def clear_edge_components(alpha: np.ndarray) -> np.ndarray:
    """Remove tiny disconnected border specks that often bleed from neighbours."""
    h, w = alpha.shape
    mask = alpha > 24
    seen = np.zeros_like(mask, dtype=bool)
    output = alpha.copy()
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(x, y)]
            seen[y, x] = True
            points: list[tuple[int, int]] = []
            touches = False
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                touches = touches or px in (0, w - 1) or py in (0, h - 1)
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((nx, ny))
            if touches and len(points) < max(16, int(w * h * 0.0015)):
                for px, py in points:
                    output[py, px] = 0
    return output


def cut_cell(source: Image.Image, box: tuple[int, int, int, int], output: Path, allow_adaptive_edge: bool = False) -> dict:
    cell = source.crop(box).convert("RGBA")
    array = np.asarray(cell)
    rgb = array[:, :, :3]
    sampled = edge_samples(rgb)
    bg = np.median(sampled, axis=0)
    # Strictly require the prompt's chroma key. White/gray grids are rejected
    # rather than processed into fake transparency.
    if not allow_adaptive_edge and not (bg[0] > 150 and bg[2] > 150 and bg[1] < 110):
        raise ValueError(f"cell edge is not magenta chroma key: {bg.round(1).tolist()}")
    distance = rgba_distance(rgb, bg)
    edge_distance = rgba_distance(sampled.reshape(1, -1, 3), bg).reshape(-1)
    # Ignore edge samples belonging to a model-added white tile or an icon that
    # violated the requested safe zone; derive the local background cutoff from
    # the dominant low-distance chroma-key cluster, then take its high tail.
    background_cluster = edge_distance[edge_distance <= np.percentile(edge_distance, 60)]
    cutoff = max(12.0, float(np.percentile(background_cluster, 98)) + 10.0)
    alpha = np.clip((distance - cutoff) / 20.0 * 255.0, 0, 255).astype(np.uint8)
    # Qwen occasionally adds a white tile behind each icon despite the
    # chroma-key instruction. It is not part of the requested asset and would
    # turn into a white square in PPT, so remove only neutral near-white pixels
    # while preserving blue strokes and light-blue fills.
    near_white = (np.min(rgb, axis=2) >= 220) & ((np.max(rgb, axis=2) - np.min(rgb, axis=2)) <= 14)
    alpha[near_white] = 0
    chroma_halo = (rgb[:, :, 0] > 170) & (rgb[:, :, 2] > 100) & (rgb[:, :, 1] < 120) & ((rgb[:, :, 0] - rgb[:, :, 1]) > 80)
    alpha[chroma_halo] = 0
    alpha = clear_edge_components(alpha)
    bounds = alpha_bbox(alpha)
    if bounds is None:
        raise ValueError("no foreground alpha after chroma key")
    x0, y0, x1, y1 = bounds
    # Reject a cell whose main foreground hits more than one edge: that is a
    # grid placement failure or a cross-cell fragment, not a valid final icon.
    h, w = alpha.shape
    edge_hits = sum((x0 <= 1, y0 <= 1, x1 >= w - 1, y1 >= h - 1))
    if edge_hits >= 2:
        raise ValueError("unsafe foreground touches multiple cell edges")
    crop = np.asarray(cell.crop((x0, y0, x1, y1))).copy()
    crop[:, :, 3] = alpha[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    pad = max(10, int(max(cw, ch) * 0.15))
    side = max(cw, ch) + 2 * pad
    # Centre by alpha-weighted centroid, not by simple crop bounds.
    weights = crop[:, :, 3].astype(np.float64)
    yy, xx = np.indices((ch, cw))
    centroid_x = float((weights * xx).sum() / max(1.0, weights.sum()))
    centroid_y = float((weights * yy).sum() / max(1.0, weights.sum()))
    target_c = (side - 1) / 2.0
    paste_x = round(target_c - centroid_x)
    paste_y = round(target_c - centroid_y)
    paste_x = max(pad, min(side - pad - cw, paste_x))
    paste_y = max(pad, min(side - pad - ch, paste_y))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(Image.fromarray(crop, mode="RGBA"), (paste_x, paste_y))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    return {
        "cellBox": list(box),
        "edgeBackground": [round(float(value), 1) for value in bg],
        "edgeCutoff": round(cutoff, 2),
        "visibleBBox": [x0, y0, x1 - x0, y1 - y0],
        "canvas": [side, side],
        "centroidDelta": [round((paste_x + centroid_x) - target_c, 2), round((paste_y + centroid_y) - target_c, 2)],
        "output": str(output),
    }


def make_contact(asset_paths: list[Path], output: Path) -> None:
    tile, label_h = 150, 24
    columns = 5
    rows = max(1, math.ceil(len(asset_paths) / columns))
    canvas = Image.new("RGBA", (columns * tile, rows * (tile + label_h)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, item in enumerate(asset_paths):
        image = Image.open(item).convert("RGBA")
        image.thumbnail((tile - 28, tile - 28))
        x = (index % columns) * tile + (tile - image.width) // 2
        y = (index // columns) * (tile + label_h) + 8 + (tile - 16 - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        draw.text(((index % columns) * tile + 4, (index // columns) * (tile + label_h) + tile), item.stem[:25], fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output)


def contiguous_ranges(values: np.ndarray) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(values.tolist() + [False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            ranges.append((start, index))
            start = None
    return ranges


def detect_content_grid(image: Image.Image, columns: int, rows: int) -> tuple[list[tuple[int, int, int, int]], dict]:
    """Detect actual grid centers before cutting instead of equal origin slices."""
    rgb = np.asarray(image.convert("RGB"))
    corner = np.concatenate((rgb[:8].reshape(-1, 3), rgb[-8:].reshape(-1, 3), rgb[:, :8].reshape(-1, 3), rgb[:, -8:].reshape(-1, 3)), axis=0)
    background = np.median(corner, axis=0)
    foreground = rgba_distance(rgb, background) > 50
    x_projection = foreground.sum(axis=0)
    y_projection = foreground.sum(axis=1)
    x_ranges = contiguous_ranges(x_projection > max(12, int(image.height * 0.06)))
    y_ranges = contiguous_ranges(y_projection > max(12, int(image.width * 0.06)))
    if len(x_ranges) != columns or len(y_ranges) != rows:
        raise ValueError(f"detected grid {len(x_ranges)}×{len(y_ranges)} does not match declared {columns}×{rows}")
    x_centers = [(start + end) / 2 for start, end in x_ranges]
    y_centers = [(start + end) / 2 for start, end in y_ranges]
    x_edges = [0] + [round((x_centers[i] + x_centers[i + 1]) / 2) for i in range(columns - 1)] + [image.width]
    y_edges = [0] + [round((y_centers[i] + y_centers[i + 1]) / 2) for i in range(rows - 1)] + [image.height]
    boxes = [(x_edges[col], y_edges[row], x_edges[col + 1], y_edges[row + 1]) for row in range(rows) for col in range(columns)]
    return boxes, {
        "background": [round(float(value), 1) for value in background],
        "xRanges": [list(value) for value in x_ranges],
        "yRanges": [list(value) for value in y_ranges],
        "xCenters": [round(value, 2) for value in x_centers],
        "yCenters": [round(value, 2) for value in y_centers],
        "xEdges": x_edges,
        "yEdges": y_edges,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--sheet-ids", default="", help="optional comma-separated sheet IDs; use to resume one rate-limited Qwen Image call at a time")
    args = parser.parse_args()
    plan = read_json(args.plan)
    sheets = plan.get("sheets") or []
    requested_sheets = {value.strip() for value in args.sheet_ids.split(",") if value.strip()}
    if requested_sheets:
        sheets = [sheet for sheet in sheets if sheet.get("id") in requested_sheets]
    output = args.output_dir
    source_dir, final_dir = output / "source", output / "final"
    prior_manifest_path = output / "asset-manifest.json"
    prior = read_json(prior_manifest_path) if prior_manifest_path.exists() else {}
    records_by_id: dict[str, dict] = {record.get("id"): record for record in prior.get("assets", []) if record.get("id")}
    failures: list[dict] = [record for record in prior.get("failures", []) if record.get("sheet") not in {sheet.get("id") for sheet in sheets}]
    for sheet in sheets:
        sheet_id = sheet["id"]
        columns, rows = int(sheet["columns"]), int(sheet["rows"])
        assets = sheet.get("assets") or []
        if len(assets) != columns * rows:
            failures.append({"sheet": sheet_id, "error": "asset count must equal columns × rows"})
            continue
        source_path = source_dir / f"{sheet_id}.png"
        try:
            provenance = {"reused": True} if args.resume and source_path.exists() else download_qwen_image(sheet["prompt"], sheet.get("size", "1328*1328"), source_path)
            image = Image.open(source_path).convert("RGBA")
            # A 1×1 request is an intentionally isolated visual, not a grid.
            # Running grid detection on it mistakes internal icon details for
            # rows/columns and can reject otherwise valid Qwen output.
            if columns == 1 and rows == 1:
                boxes = [(0, 0, image.width, image.height)]
                grid_alignment = {"mode": "single_asset_full_canvas", "xEdges": [0, image.width], "yEdges": [0, image.height], "xCenters": [image.width / 2], "yCenters": [image.height / 2]}
            else:
                boxes, grid_alignment = detect_content_grid(image, columns, rows)
            sheet_records = []
            for index, spec in enumerate(assets):
                box = boxes[index]
                destination = final_dir / f"{spec['id']}.png"
                diagnostics = cut_cell(image, box, destination, allow_adaptive_edge=(columns == 1 and rows == 1))
                sheet_records.append({
                    "id": spec["id"],
                    "semantic": spec["semantic"],
                    "colorRoles": spec.get("colorRoles", {}),
                    "finalPng": str(destination),
                    "generatedSource": str(source_path),
                    "prompt": sheet["prompt"],
                    "generation": provenance,
                    "cleanup": "per-cell edge-sampled adaptive chroma key; edge-fragment filtering; alpha-centroid square repack",
                    "gridAlignment": grid_alignment,
                    "qa": diagnostics,
                })
            make_contact([Path(record["finalPng"]) for record in sheet_records], output / "contact-sheets" / f"{sheet_id}.png")
            for record in sheet_records:
                records_by_id[record["id"]] = record
        except Exception as error:
            failures.append({"sheet": sheet_id, "error": str(error), "source": str(source_path)})
    manifest = {
        "schema": "qwen-generated-asset-grid/v1",
        "provider": "dashscope",
        "model": os.getenv("DASHSCOPE_IMAGE_EDIT_MODEL") or "qwen-image-2.0-pro",
        "assets": list(records_by_id.values()),
        "failures": failures,
        "status": "passed" if not failures else "blocked",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "asset-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "assets": len(records_by_id), "failures": failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
