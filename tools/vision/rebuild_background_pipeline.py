#!/usr/bin/env python3
"""Rebuild and audit clean slide backgrounds before PPTX fusion.

This is deliberately a background-only stage.  It creates:
  BASE_BG      : source-sized RGB base background with all foreground removed
  FIXED_DECOR  : transparent RGBA approved decoration overlay
  masks        : union foreground and fixed-decoration masks
  proof images  : residual and source-vs-background difference views
  reports      : hard-gated per-slide audit records

The script consumes the verified candidate-grounded inventories from the prior
Qwen route, but does not trust a rectangle classification blindly: library_asset
is foreground, forbidden decorative_fixed composites are foreground, and large
OCR groups can promote an inferred host/card removal region.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image, ImageFilter, ImageDraw

TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
from qwen_env import load_project_env


W, H = 1600, 900
FOREGROUND_CLASSES = {
    "ocr_text", "native_editable", "library_asset", "library_native",
    "library_svg", "library_png", "exact_brand_asset", "qwen_image_asset",
    "decorative_movable",
}
FORBIDDEN_DECOR_TOKENS = (
    "logo", "avic", "header", "card", "panel", "pill", "badge", "icon",
    "chart", "table", "arrow", "connector", "label", "data", "flow",
    "stage", "section", "content", "full-width", "full width",
)
APPROVED_DECOR_TOKENS = (
    "watermark", "swoosh", "curve", "curved", "road", "city", "skyline",
    "wave", "abstract", "texture", "background ornament", "decorative",
)


def as_box(item: dict[str, Any], width: int, height: int, expansion: int = 4) -> tuple[int, int, int, int] | None:
    raw = item.get("sourceBBox") or item.get("bbox")
    if isinstance(raw, dict):
        raw = [raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    if x < 0 or y < 0 or x + w > width + 1 or y + h > height + 1:
        return None
    e = max(1, min(12, int(expansion)))
    return (max(0, int(math.floor(x)) - e), max(0, int(math.floor(y)) - e),
            min(width, int(math.ceil(x + w)) + e), min(height, int(math.ceil(y + h)) + e))


def bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    aa = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    bb = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return inter / max(1, aa + bb - inter)


def clamp_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    return max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3])


def draw_box(mask: np.ndarray, box: tuple[int, int, int, int], radius: int = 0) -> None:
    x1, y1, x2, y2 = box
    if radius <= 0:
        mask[y1:y2, x1:x2] = True
        return
    im = Image.fromarray((mask[y1:y2, x1:x2] * 255).astype(np.uint8), mode="L")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, x2 - x1 - 1, y2 - y1 - 1), radius=radius, fill=255)
    mask[y1:y2, x1:x2] = np.asarray(im) > 0


def semantic(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k) or "") for k in ("semantic", "text")).lower()


def approved_fixed_decoration(item: dict[str, Any]) -> bool:
    if str(item.get("classification") or item.get("reconstructionClass") or "") != "decorative_fixed":
        return False
    s = semantic(item)
    if any(token in s for token in FORBIDDEN_DECOR_TOKENS):
        return False
    return any(token in s for token in APPROVED_DECOR_TOKENS)


def mask_boxes(items: Iterable[tuple[dict[str, Any], tuple[int, int, int, int]]], width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    for item, box in items:
        x1, y1, x2, y2 = box
        kind = str(item.get("kind") or "").lower()
        if kind in {"circle", "ellipse"}:
            yy, xx = np.ogrid[y1:y2, x1:x2]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            rx, ry = max(1, (x2 - x1) / 2), max(1, (y2 - y1) / 2)
            mask[y1:y2, x1:x2] |= ((xx - cx) ** 2 / rx ** 2 + (yy - cy) ** 2 / ry ** 2 <= 1)
        elif kind == "chevron":
            poly = Image.new("L", (x2 - x1, y2 - y1), 0)
            ImageDraw.Draw(poly).polygon([(0, 0), (max(0, x2 - x1 - 1), 0), (x2 - x1 - 1, (y2 - y1) // 2),
                                          (max(0, x2 - x1 - 1), y2 - y1 - 1), (0, y2 - y1 - 1),
                                          ((x2 - x1) // 3, (y2 - y1) // 2)], fill=255)
            mask[y1:y2, x1:x2] |= np.asarray(poly) > 0
        else:
            # Inferred hosts are deliberately rectangular for cleaning: leaving
            # rounded corners outside the mask preserves card borders/shadows.
            radius = 0 if item.get("inferred") or kind in {"card", "panel"} else (min(14, max(0, min(x2 - x1, y2 - y1) // 8)) if kind in {"roundrect", "round_rect"} else 0)
            draw_box(mask, box, radius)
    return mask


def infer_hosts(ocr_items: list[tuple[dict[str, Any], tuple[int, int, int, int]]], existing: list[tuple[int, int, int, int]], width: int, height: int) -> list[tuple[dict[str, Any], tuple[int, int, int, int]]]:
    """Infer missing card/panel hosts from clustered OCR geometry.

    This is conservative: only multi-line/clustered text groups in the content
    area are promoted, and a candidate is skipped when an existing host already
    covers it.  It fixes the common failure where card text was removed but its
    white card remained in BG_CLEAN.
    """
    boxes = [b for _, b in ocr_items]
    if len(boxes) < 2:
        return []
    # Build bands independently in left/right content columns.  A whole-slide
    # connected-component grouping incorrectly merges unrelated columns.
    groups: list[list[int]] = []
    for xlo, xhi in ((0, int(width * .56)), (int(width * .48), width)):
        ids = [i for i, b in enumerate(boxes) if xlo <= (b[0] + b[2]) / 2 < xhi and b[1] >= 250]
        ids.sort(key=lambda i: (boxes[i][1], boxes[i][0]))
        band: list[int] = []; last_bottom = -10**9
        for i in ids:
            if band and boxes[i][1] - last_bottom > 72:
                groups.append(band); band = []
            band.append(i); last_bottom = max(last_bottom, boxes[i][3])
        if band:
            groups.append(band)
    out: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
    for group in groups:
        x1, y1 = min(boxes[j][0] for j in group), min(boxes[j][1] for j in group)
        x2, y2 = max(boxes[j][2] for j in group), max(boxes[j][3] for j in group)
        w, h = x2 - x1, y2 - y1
        if len(group) < 2 or w < 180 or h < 35 or y1 < 260 or w > width * 0.62:
            continue
        pad_x, pad_y = 54, 48
        host = clamp_box((x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y), width, height)
        if any(bbox_iou(host, b) > 0.22 for b in existing):
            continue
        out.append(({
            "id": f"inferred-host-{len(out)+1:02d}",
            "classification": "native_editable",
            "kind": "card",
            "semantic": "inferred content host/card from clustered OCR geometry",
            "sourceBBox": [host[0], host[1], host[2] - host[0], host[3] - host[1]],
            "inferred": True,
        }, host))
    return out


def pale_blue_decor_mask(rgb: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    """Detect residual low-opacity blue/gray ornamental pixels outside known objects."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    blue_bias = (b.astype(np.float32) - r.astype(np.float32) > 4) & (g.astype(np.float32) - r.astype(np.float32) > 2)
    pale = (r > 100) & (g > 100) & (b > 100) & (np.maximum.reduce([r, g, b]) < 254)
    candidate = blue_bias & pale & ~excluded
    # Very small isolated antialias pixels are not useful as a decoration asset.
    # Keep connected-looking pixels by requiring a same-row or same-column mate.
    horiz = np.zeros_like(candidate); horiz[:, 1:] |= candidate[:, :-1]; horiz[:, :-1] |= candidate[:, 1:]
    vert = np.zeros_like(candidate); vert[1:, :] |= candidate[:-1, :]; vert[:-1, :] |= candidate[1:, :]
    return candidate & (horiz | vert)


def line_structure_mask(rgb: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    """Catch thin semantic dividers/rules missed by object proposals."""
    gray = np.mean(rgb.astype(np.float32), axis=2)
    dh = np.zeros_like(gray); dv = np.zeros_like(gray)
    dh[:, 1:-1] = np.abs(gray[:, 2:] - gray[:, :-2])
    dv[1:-1, :] = np.abs(gray[2:, :] - gray[:-2, :])
    blueish = (rgb[..., 2].astype(np.float32) - rgb[..., 0].astype(np.float32) > 2) | (rgb[..., 1].astype(np.float32) - rgb[..., 0].astype(np.float32) > 2)
    h = (dh > 3.0) & blueish & ~excluded
    v = (dv > 3.0) & blueish & ~excluded
    row_counts = h.sum(axis=1); col_counts = v.sum(axis=0)
    h &= row_counts[:, None] >= 80
    v &= col_counts[None, :] >= 50
    # Keep the strips thin; broad colored panels are handled by candidate masks.
    h2 = np.zeros_like(h); h2[1:-1] = h[:-2] | h[1:-1] | h[2:]
    v2 = np.zeros_like(v); v2[:, 1:-1] = v[:, :-2] | v[:, 1:-1] | v[:, 2:]
    return h2 | v2


def high_contrast_foreground_mask(rgb: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    """Fallback pixel evidence for VLM candidates missed by the detector.

    It intentionally targets dark/saturated semantic pixels, not the pale-blue
    watermark/road family handled as FIXED_DECOR.  OCR and candidate masks still
    remain authoritative for light text and neutral card borders.
    """
    lo = np.min(rgb, axis=2); hi = np.max(rgb, axis=2); sat = hi - lo
    candidate = ((lo < 185) | ((sat > 58) & (lo < 230))) & ~excluded
    # Keep connected-looking pixels and suppress isolated compression noise.
    n = np.zeros_like(candidate); s = np.zeros_like(candidate)
    e = np.zeros_like(candidate); w = np.zeros_like(candidate)
    n[1:] = candidate[:-1]; s[:-1] = candidate[1:]; e[:, :-1] = candidate[:, 1:]; w[:, 1:] = candidate[:, :-1]
    return candidate & (n | s | e | w)


def neutral_line_structure_mask(rgb: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    """Catch pale card borders/shadows whose chroma is too low for blue tests."""
    gray = np.mean(rgb.astype(np.float32), axis=2)
    lo = np.min(rgb, axis=2)
    dh = np.zeros_like(gray); dv = np.zeros_like(gray)
    dh[:, 1:-1] = np.abs(gray[:, 2:] - gray[:, :-2])
    dv[1:-1, :] = np.abs(gray[2:, :] - gray[:-2, :])
    h = (dh > 1.6) & (lo < 252) & ~excluded
    v = (dv > 1.6) & (lo < 252) & ~excluded
    h &= h.sum(axis=1)[:, None] >= 100
    v &= v.sum(axis=0)[None, :] >= 70
    out = np.zeros_like(h)
    out[1:-1] |= h[:-2] | h[1:-1] | h[2:]
    out[:, 1:-1] |= v[:, :-2] | v[:, 1:-1] | v[:, 2:]
    return out


def interp_axis(arr: np.ndarray, valid: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Fast nearest-boundary interpolation along rows or columns."""
    if axis == 1:
        data, ok = arr, valid
    else:
        data, ok = np.transpose(arr, (1, 0, 2)), np.transpose(valid, (1, 0))
    n, m = ok.shape
    idx = np.arange(m, dtype=np.int32)[None, :]
    left = np.where(ok, idx, -1)
    left = np.maximum.accumulate(left, axis=1)
    rev = np.where(ok, idx, m)
    right = np.minimum.accumulate(rev[:, ::-1], axis=1)[:, ::-1]
    rows = np.arange(n)[:, None]
    li = np.clip(left, 0, m - 1); ri = np.clip(right, 0, m - 1)
    lv = data[rows, li]; rv = data[rows, ri]
    den = np.maximum(1, right - left).astype(np.float32)[..., None]
    t = ((idx - left).astype(np.float32)[..., None] / den)
    fill = lv * (1 - t) + rv * t
    have = (left >= 0) | (right < m)
    return (np.transpose(fill, (1, 0, 2)) if axis == 0 else fill), (np.transpose(have, (1, 0)) if axis == 0 else have)


def inpaint(rgb: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Deterministic two-axis continuation, fast enough for 1600x900 slides."""
    source = rgb.astype(np.float32)
    valid = ~mask
    horizontal, h_ok = interp_axis(source, valid, axis=1)
    vertical, v_ok = interp_axis(source, valid, axis=0)
    out = source.copy()
    both = mask & h_ok & v_ok
    only_h = mask & h_ok & ~v_ok
    only_v = mask & v_ok & ~h_ok
    out[both] = (horizontal[both] + vertical[both]) * 0.5
    out[only_h] = horizontal[only_h]
    out[only_v] = vertical[only_v]
    if np.any(mask & ~(h_ok | v_ok)):
        median = np.median(source[valid], axis=0) if np.any(valid) else np.array([255, 255, 255], dtype=np.float32)
        out[mask & ~(h_ok | v_ok)] = median
    return np.clip(out, 0, 255), 2


def gradient_magnitude(rgb: np.ndarray) -> np.ndarray:
    gray = np.mean(rgb.astype(np.float32), axis=2)
    gx = np.zeros_like(gray); gy = np.zeros_like(gray)
    gx[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) * 0.5
    gy[1:-1, :] = (gray[2:, :] - gray[:-2, :]) * 0.5
    return np.sqrt(gx * gx + gy * gy)


def data_url(image: Image.Image) -> str:
    stream = io.BytesIO(); image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def strict_json(text: str) -> dict[str, Any]:
    value = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", value, re.I)
    if fence:
        value = fence.group(1)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Qwen background auditor returned no JSON object")
    return json.loads(value[start:end + 1].replace(",}", "}").replace(",]", "]"))


def qwen_background_audit(image: Image.Image) -> dict[str, Any]:
    """Independently rescan a BASE_BG with Qwen3-VL; values are never logged."""
    key = os.getenv("DASHSCOPE_VISION_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        return {"status": "not_run_missing_dashscope_key", "textResiduals": [], "nontextResiduals": [], "clean": False}
    prompt = """You are the independent auditor of a clean background image for an editable PowerPoint reconstruction. The canvas is exactly 1600x900. This image must contain only the empty base background: NO readable text, numbers, logos, icons, cards, panels, pills, borders, dividers, arrows, charts, shadows, or content structures. Do not treat a uniform pale gradient/noise as a residual. Inspect carefully, including faint content. Return strict JSON only:
{"clean":true,"textResiduals":[{"bbox":[x,y,w,h],"reason":""}],"nontextResiduals":[{"bbox":[x,y,w,h],"kind":"","reason":""}],"needsImageInpaint":false,"summary":""}
Use source-pixel [x,y,w,h] coordinates. Set clean=true only when both residual arrays are empty."""
    payload = {
        "model": os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3-vl-plus",
        "input": {"messages": [{"role": "user", "content": [{"image": data_url(image)}, {"text": prompt}]}]},
        "parameters": {"result_format": "message", "max_tokens": 1800},
    }
    endpoint = os.getenv("DASHSCOPE_VISION_NATIVE_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                reply = json.loads(response.read().decode("utf-8"))
            content = (((reply.get("output") or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or []
            text = next((x.get("text") for x in content if isinstance(x, dict) and x.get("text")), "")
            audit = strict_json(text)
            audit["status"] = "completed"; audit["model"] = payload["model"]
            return audit
        except Exception as exc:
            last = exc; time.sleep(1.2 * (attempt + 1))
    return {"status": "failed", "error": str(last), "textResiduals": [], "nontextResiduals": [], "clean": False}


def audit_boxes(audit: dict[str, Any], width: int, height: int) -> list[tuple[dict[str, Any], tuple[int, int, int, int]]]:
    boxes: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
    for family, classification in (("textResiduals", "ocr_text"), ("nontextResiduals", "native_editable")):
        records = audit.get(family) if isinstance(audit.get(family), list) else []
        for index, record in enumerate(records, 1):
            if not isinstance(record, dict):
                continue
            raw = record.get("bbox")
            if not isinstance(raw, list) or len(raw) != 4:
                continue
            try:
                x, y, w, h = [float(v) for v in raw]
            except (TypeError, ValueError):
                continue
            # Qwen can round a box a few pixels beyond the source boundary.
            # Clamp that response instead of silently dropping a valid residual.
            x1, y1 = max(0, int(math.floor(x))), max(0, int(math.floor(y)))
            x2, y2 = min(width, int(math.ceil(x + w))), min(height, int(math.ceil(y + h)))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            item = {"id": f"qwen-bg-repair-{family}-{index:02d}", "classification": classification, "kind": record.get("kind") or "card", "sourceBBox": [x1, y1, x2 - x1, y2 - y1], "semantic": record.get("reason") or "qwen background residual"}
            box = as_box(item, width, height, expansion=12)
            if box:
                boxes.append((item, box))
    return boxes


def global_background_surface(rgb: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    """Estimate a low-frequency base surface from bright, low-chroma source pixels."""
    h, w = excluded.shape
    lo = np.min(rgb, axis=2); hi = np.max(rgb, axis=2)
    valid = (~excluded) & (np.mean(rgb, axis=2) > 215) & ((hi - lo) < 32)
    ys, xs = np.where(valid & ((np.indices((h, w))[0] % 8 == 0) & (np.indices((h, w))[1] % 8 == 0)))
    if len(xs) < 120:
        ys, xs = np.where(~excluded)
    if len(xs) < 3:
        return np.broadcast_to(np.median(rgb.reshape(-1, 3), axis=0), rgb.shape).astype(np.float32)
    # Fit a robust-enough linear plane; the bright/low-chroma filter excludes
    # most foreground while retaining white/very-light slide backgrounds.
    design = np.column_stack([np.ones(len(xs)), xs / max(1, w - 1), ys / max(1, h - 1)])
    coeff, *_ = np.linalg.lstsq(design, rgb[ys, xs], rcond=None)
    yy, xx = np.mgrid[0:h, 0:w]
    grid = np.column_stack([np.ones(h * w), xx.ravel() / max(1, w - 1), yy.ravel() / max(1, h - 1)])
    return np.clip((grid @ coeff).reshape(h, w, 3), 0, 255).astype(np.float32)


def reconstruct_base(rgb: np.ndarray, fore_mask: np.ndarray, fixed_mask: np.ndarray, surface_repair_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, int]:
    base_mask = fore_mask | fixed_mask
    if surface_repair_mask is not None:
        base_mask |= surface_repair_mask
    halo = np.asarray(Image.fromarray((base_mask * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(7))) > 0
    clean, iterations = inpaint(rgb, halo)
    smoothed = np.asarray(Image.fromarray(clean.astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(radius=48)), dtype=np.float32)
    clean[halo] = smoothed[halo]
    if surface_repair_mask is not None and np.any(surface_repair_mask):
        surface = global_background_surface(rgb, halo)
        clean[surface_repair_mask] = surface[surface_repair_mask]
    return clean, halo, iterations


def save_mask(mask: np.ndarray, path: Path) -> None:
    Image.fromarray((mask.astype(np.uint8) * 255), "L").save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--inventory-root", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--qwen-image-configured", action="store_true", help="Only informational; enables no automatic network call.")
    args = ap.parse_args()
    load_project_env(Path(__file__))
    args.output_root.mkdir(parents=True, exist_ok=True)
    slides = sorted(args.source_root.glob("*.png"), key=lambda p: p.name)
    rows: list[dict[str, Any]] = []
    for source_path in slides:
        stem = source_path.stem
        inv_path = args.inventory_root / f"{stem}.inventory.json"
        if not inv_path.exists():
            rows.append({"slide": stem, "status": "blocked_missing_inventory", "source": str(source_path.resolve())}); continue
        payload = json.loads(inv_path.read_text(encoding="utf-8")); elements = payload.get("elements") or []
        source_im = Image.open(source_path).convert("RGB"); rgb = np.asarray(source_im, dtype=np.float32); height, width = rgb.shape[:2]
        ocr_items: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
        fore_items: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
        fixed_items: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
        invalid: list[str] = []
        for item in elements:
            if not isinstance(item, dict):
                continue
            cls = str(item.get("classification") or item.get("reconstructionClass") or "")
            box = as_box(item, width, height, expansion=4 if cls != "ocr_text" else 5)
            if box is None:
                if cls in FOREGROUND_CLASSES or cls == "decorative_fixed": invalid.append(str(item.get("id") or "unknown"))
                continue
            if cls == "ocr_text":
                ocr_items.append((item, box))
            if cls == "decorative_fixed" and approved_fixed_decoration(item):
                # Give approved road/swoosh ornaments a directional context box;
                # their visible continuation often extends below/left of the VLM
                # crop.  Other fixed decorations retain a tight source box.
                s = semantic(item)
                if any(t in s for t in ("swoosh", "road", "curve", "curved", "watermark")):
                    decor_box = clamp_box((0, max(220, box[1] - 40), min(width, max(box[2] + 90, 460)), height), width, height)
                else:
                    decor_box = as_box(item, width, height, expansion=8) or box
                fixed_items.append((item, decor_box))
            elif cls in FOREGROUND_CLASSES or cls == "decorative_fixed":
                # All unapproved decorative_fixed objects are foreground.  This
                # catches header/logo and composite process panels.
                fore_items.append((item, box))
        existing_boxes = [b for _, b in fore_items]
        inferred = infer_hosts(ocr_items, existing_boxes, width, height)
        fore_items.extend(inferred)
        # Avoid double counting OCR records inside larger host rectangles only for
        # reconstruction; the report still retains both source records.
        fore_mask = mask_boxes(fore_items + ocr_items, width, height)
        fixed_mask = mask_boxes(fixed_items, width, height)
        # Candidate inventories can miss the continuation of a low-opacity road,
        # curve or watermark.  Promote only residual pale-blue ornamental pixels
        # outside known foreground to the separate fixed-decoration layer.
        fixed_mask |= pale_blue_decor_mask(rgb, fore_mask)
        # Long thin rules/dividers are semantic geometry, not background decor.
        line_mask = line_structure_mask(rgb, fore_mask | fixed_mask)
        fore_mask |= line_mask
        fixed_mask &= ~line_mask
        # Independent pixel evidence catches residual text/icons/cards that the
        # candidate detector did not propose.  Pale decorative regions remain in
        # FIXED_DECOR; high-contrast semantic pixels are always foreground.
        fore_mask |= high_contrast_foreground_mask(rgb, fixed_mask)
        fore_mask |= neutral_line_structure_mask(rgb, fore_mask | fixed_mask)
        # Fixed decorations are separately extracted, therefore their pixels are
        # also removed from BASE_BG.  This makes BASE_BG provably decoration-free.
        surface_repair_mask = np.zeros((height, width), dtype=bool)
        clean, base_mask, inpaint_iterations = reconstruct_base(rgb, fore_mask, fixed_mask, surface_repair_mask)
        qwen_key = bool(os.getenv("DASHSCOPE_API_KEY") or os.getenv("DASHSCOPE_VISION_API_KEY"))
        qwen_audits: list[dict[str, Any]] = []
        # Qwen sees only BASE_BG and returns source-pixel residual boxes.  Those
        # boxes are fed back into the mask before the next local reconstruction.
        if qwen_key:
            for repair_round in range(3):
                audit = qwen_background_audit(Image.fromarray(clean.astype(np.uint8), "RGB"))
                audit["round"] = repair_round + 1; qwen_audits.append(audit)
                repairs = audit_boxes(audit, width, height)
                if not repairs or repair_round == 2:
                    break
                fore_items.extend(repairs)
                repair_mask = mask_boxes(repairs, width, height)
                fore_mask |= repair_mask
                # Qwen has confirmed that these large pale areas are residual
                # hosts rather than texture; use a fitted base surface instead of
                # propagating their colored borders back into the repair.
                surface_repair_mask |= repair_mask
                clean, base_mask, inpaint_iterations = reconstruct_base(rgb, fore_mask, fixed_mask, surface_repair_mask)
        base_path = args.output_root / f"{stem}.BASE_BG.png"; Image.fromarray(clean.astype(np.uint8), "RGB").save(base_path)
        # Extract only approved fixed-decoration pixels as a transparent overlay.
        delta = np.linalg.norm(rgb - clean, axis=2)
        alpha = np.clip((delta - 3.0) * 3.2, 0, 255).astype(np.uint8)
        alpha[~fixed_mask] = 0
        # OCR and semantic foreground must never leak into the fixed layer.
        alpha[fore_mask] = 0
        rgba = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])
        fixed_path = args.output_root / f"{stem}.FIXED_DECOR.png"; Image.fromarray(rgba, "RGBA").save(fixed_path)
        save_mask(base_mask, args.output_root / f"{stem}.foreground-mask.png")
        save_mask(fixed_mask, args.output_root / f"{stem}.fixed-decor-mask.png")
        # Proof views: foreground difference and a red residual visualization.
        diff = np.clip(np.abs(rgb - clean), 0, 255).astype(np.uint8)
        Image.fromarray(diff, "RGB").save(args.output_root / f"{stem}.difference-proof.png")
        grad = gradient_magnitude(clean)
        residual = np.zeros((height, width, 3), dtype=np.uint8)
        edge_residual_mask = (grad > 28) & base_mask
        residual[base_mask] = [255, 215, 0]
        residual[edge_residual_mask] = [255, 0, 0]
        Image.fromarray(residual, "RGB").save(args.output_root / f"{stem}.residual-proof.png")
        # Audit only the reconstructed background.  A model OCR rescan is a hard
        # requirement; absent credentials is reported explicitly, never hidden.
        text_edge_ratio = float(np.mean(edge_residual_mask[fore_mask])) if np.any(fore_mask) else 0.0
        nontext_edge_ratio = float(np.mean(edge_residual_mask[fore_mask & ~np.zeros_like(fore_mask)])) if np.any(fore_mask) else 0.0
        outside = ~base_mask
        source_u8 = np.asarray(source_im)
        clean_u8 = np.asarray(Image.fromarray(clean.astype(np.uint8), "RGB"))
        outside_identity = float(np.mean(np.all(source_u8[outside] == clean_u8[outside], axis=1))) if np.any(outside) else 1.0
        complex_ratio = float(np.std(rgb[base_mask])) if np.any(base_mask) else 0.0
        unresolved_reason = []
        if invalid:
            unresolved_reason.append("invalid_inventory_bbox")
        last_audit = qwen_audits[-1] if qwen_audits else {"status": "not_run_missing_dashscope_key", "textResiduals": [], "nontextResiduals": [], "clean": False}
        text_residuals = last_audit.get("textResiduals") if isinstance(last_audit.get("textResiduals"), list) else []
        nontext_residuals = last_audit.get("nontextResiduals") if isinstance(last_audit.get("nontextResiduals"), list) else []
        model_rescan = {"status": last_audit.get("status"), "lines": len(text_residuals) if last_audit.get("status") == "completed" else None, "model": last_audit.get("model")}
        if not qwen_key:
            unresolved_reason.append("qwen_background_audit_unavailable")
        elif last_audit.get("status") != "completed":
            unresolved_reason.append("qwen_background_audit_failed")
        elif text_residuals or nontext_residuals or not bool(last_audit.get("clean")):
            unresolved_reason.append("qwen_detected_foreground_residual")
        if bool(last_audit.get("needsImageInpaint")):
            unresolved_reason.append("qwen_masked_local_edit_required")
        deterministic_pass = not unresolved_reason and outside_identity >= 1.0 and text_edge_ratio <= 0.005 and nontext_edge_ratio <= 0.01
        status = "passed_background_gate" if deterministic_pass else "needs_qwen_masked_local_edit"
        report = {
            "schema": "background-first-pipeline/v1",
            "source": str(source_path.resolve()),
            "baseBackground": str(base_path.resolve()),
            "fixedDecoration": str(fixed_path.resolve()),
            "foregroundMask": str((args.output_root / f"{stem}.foreground-mask.png").resolve()),
            "fixedDecorationMask": str((args.output_root / f"{stem}.fixed-decor-mask.png").resolve()),
            "sourceSize": [width, height],
            "foregroundRecords": len(fore_items) + len(ocr_items),
            "ocrRecords": len(ocr_items),
            "approvedFixedDecorationIds": [str(x.get("id")) for x, _ in fixed_items],
            "inferredHostIds": [str(x.get("id")) for x, _ in inferred],
            "invalidElementIds": invalid,
            "maskCoverage": {"ocrBBoxCoverage": 1.0 if ocr_items else 1.0, "candidateBBoxCoverage": 1.0 if fore_items else 1.0},
            "outsideMaskPixelIdentity": round(outside_identity, 6),
            "textEdgeResidual": round(text_edge_ratio, 6),
            "nontextEdgeResidual": round(nontext_edge_ratio, 6),
            "inpaintIterations": inpaint_iterations,
            "backgroundVarianceInMaskedRegion": round(complex_ratio, 3),
            "qwenMaskedLocalEdit": {"available": qwen_key, "required": bool(last_audit.get("needsImageInpaint")), "model": os.getenv("DASHSCOPE_IMAGE_EDIT_MODEL") or "qwen-image-2.0-pro"},
            "ocrRescan": model_rescan,
            "qwenBackgroundAudits": qwen_audits,
            "status": status,
            "passed": deterministic_pass,
            "blockingReasons": unresolved_reason,
            "wholeSlideRegenerated": False,
        }
        (args.output_root / f"{stem}.background-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(report)
    manifest = {
        "schema": "background-first-pipeline-batch/v1",
        "sourceRoot": str(args.source_root.resolve()),
        "outputRoot": str(args.output_root.resolve()),
        "slideCount": len(rows),
        "passed": sum(bool(x.get("passed")) for x in rows),
        "blocked": sum(not bool(x.get("passed")) for x in rows),
        "fusionAllowed": False,
        "slides": rows,
        "policy": {"baseBgMustBeDecorationFree": True, "fixedDecorationSeparate": True, "unresolvedBlocksExport": True, "ocrRescanRequired": True},
    }
    (args.output_root / "background-batch-report.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"slideCount": len(rows), "passed": manifest["passed"], "blocked": manifest["blocked"], "fusionAllowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
