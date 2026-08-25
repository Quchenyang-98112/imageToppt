#!/usr/bin/env python3
"""Vectorize Qwen-generated transparent icon assets into real SVG paths.

Qwen Image produces raster pixels.  This utility turns its small,
flat-colour, transparent icon assets into colour-layered SVG path runs.  The
result is a genuine vector SVG (not a PNG embedded in an SVG wrapper), so it
scales cleanly in PowerPoint.  It deliberately leaves existing library PNGs
unchanged and records vectorization provenance in the asset manifest.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image


def contiguous_runs(values: list[int]) -> list[tuple[int, int]]:
    if not values:
        return []
    runs: list[tuple[int, int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            runs.append((start, previous + 1))
            start = value
        previous = value
    runs.append((start, previous + 1))
    return runs


def vectorize(source: Path, target: Path, colours: int) -> dict:
    rgba = Image.open(source).convert("RGBA")
    width, height = rgba.size
    alpha = rgba.getchannel("A")
    visible = alpha.point(lambda value: 255 if value > 28 else 0)
    rgb = Image.new("RGB", rgba.size, (255, 255, 255))
    rgb.paste(rgba.convert("RGB"), mask=visible)
    quantized = rgb.quantize(colors=max(2, min(32, colours)), method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    pixels = quantized.load()
    alpha_pixels = alpha.load()
    runs_by_style: dict[tuple[str, int], list[str]] = defaultdict(list)
    count = 0
    for y in range(height):
        row: dict[tuple[str, int], list[int]] = defaultdict(list)
        for x in range(width):
            a = alpha_pixels[x, y]
            if a <= 28:
                continue
            index = int(pixels[x, y])
            offset = index * 3
            r, g, b = palette[offset:offset + 3]
            opacity = 255 if a >= 224 else (192 if a >= 144 else 112)
            row[(f"#{r:02X}{g:02X}{b:02X}", opacity)].append(x)
        for style, xs in row.items():
            for left, right in contiguous_runs(xs):
                runs_by_style[style].append(f"M{left} {y}h{right-left}v1h-{right-left}Z")
                count += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" shape-rendering="geometricPrecision" role="img">']
    parts.append(f"<title>{html.escape(source.stem)}</title>")
    for (colour, opacity), paths in sorted(runs_by_style.items()):
        style = f' fill="{colour}"' + ("" if opacity == 255 else f' fill-opacity="{opacity / 255:.3f}"')
        parts.append(f"<path{style} d=\"{''.join(paths)}\"/>")
    parts.append("</svg>")
    target.write_text("\n".join(parts), encoding="utf-8")
    markup = target.read_text(encoding="utf-8")
    vector_valid = (
        "<svg" in markup
        and "<path" in markup
        and "<image" not in markup.lower()
        and "data:image/" not in markup.lower()
    )
    return {
        "svg": str(target), "paths": count, "colors": len(runs_by_style),
        "bytes": target.stat().st_size, "canvas": [width, height],
        "validation": {
            "realVectorPaths": vector_valid,
            "embeddedRasterForbidden": "<image" not in markup.lower() and "data:image/" not in markup.lower(),
            "sourceProvenance": "reviewed_qwen_generated_transparent_png_only",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--svg-dir", type=Path, required=True)
    parser.add_argument("--colors", type=int, default=10)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assets = manifest.get("assets") or []
    results = []
    for asset in assets:
        asset_id = str(asset.get("id") or "")
        png = Path(str(asset.get("finalPng") or ""))
        if not asset_id or not png.is_file():
            continue
        svg = args.svg_dir / f"{asset_id}.svg"
        try:
            result = vectorize(png, svg, args.colors)
            # Avoid extreme SVGs that can make a slide unresponsive. Such assets
            # retain their clean transparent PNG fallback.
            if not result["validation"]["realVectorPaths"] or result["bytes"] > 2_500_000 or result["paths"] > 160_000:
                svg.unlink(missing_ok=True)
                reason = "invalid_svg_validation" if not result["validation"]["realVectorPaths"] else "svg_complexity_limit"
                asset["vectorization"] = {"status": "png_fallback", "reason": reason, **result}
            else:
                asset["vectorSvg"] = str(svg)
                asset["vectorization"] = {"status": "svg_ready", "method": "quantized-colour path tracing", **result}
            results.append({"id": asset_id, **asset["vectorization"]})
        except Exception as error:
            asset["vectorization"] = {"status": "png_fallback", "reason": str(error)}
            results.append({"id": asset_id, **asset["vectorization"]})
    manifest["vectorization"] = {"schema": "qwen-asset-vectorization/v1", "preferredFormat": "svg", "fallbackFormat": "transparent_png", "assets": results}
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "completed", "svgReady": sum(1 for item in results if item["status"] == "svg_ready"), "pngFallback": sum(1 for item in results if item["status"] != "svg_ready")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
