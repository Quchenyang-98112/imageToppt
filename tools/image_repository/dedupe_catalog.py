from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


CATEGORY_LABELS = {
    "cards": "卡片与容器",
    "icons": "图标",
    "lines": "线条",
    "connectors": "连接器",
    "arrows": "箭头与流程块",
    "badges": "徽章与标签",
    "basic_shapes": "基础形状",
    "decorative_shapes": "装饰形状",
    "decorative_visuals": "装饰视觉",
    "logos": "Logo 与品牌标志",
    "charts": "图表",
    "tables": "表格结构",
    "backgrounds": "背景",
    "components": "组合组件",
}

PICTURE_TYPES = {11, 13, 28, 29, 30, 31}


def load_font(size: int):
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass
    return ImageFont.load_default()


def alpha_bbox(image: Image.Image):
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        return bbox
    return (0, 0, rgba.width, rgba.height)


def normalized_rgba(image: Image.Image, size: int = 256) -> Image.Image:
    rgba = image.convert("RGBA")
    bbox = alpha_bbox(rgba)
    cropped = rgba.crop(bbox)
    if cropped.width < 1 or cropped.height < 1:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    max_extent = int(size * 0.84)
    scale = min(max_extent / cropped.width, max_extent / cropped.height)
    new_size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    cropped = cropped.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(cropped, ((size - new_size[0]) // 2, (size - new_size[1]) // 2))
    return canvas


def dhash_hex(image: Image.Image, hash_size: int = 12) -> str:
    rgba = normalized_rgba(image, 192)
    base = Image.new("RGB", rgba.size, (238, 241, 245))
    base.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
    gray = base.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS).convert("L")
    pixels = list(gray.getdata())
    bits = []
    for y in range(hash_size):
        row = y * (hash_size + 1)
        for x in range(hash_size):
            bits.append(pixels[row + x] > pixels[row + x + 1])
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    width = math.ceil(len(bits) / 4)
    return f"{value:0{width}x}"


def visible_color_signature(image: Image.Image):
    rgba = normalized_rgba(image, 128)
    pixels = []
    for r, g, b, a in rgba.getdata():
        if a >= 96:
            pixels.append((r, g, b))
    if not pixels:
        return (0, 0, 0, 0)
    sample = pixels[:: max(1, len(pixels) // 5000)]
    r = sum(x[0] for x in sample) / len(sample)
    g = sum(x[1] for x in sample) / len(sample)
    b = sum(x[2] for x in sample) / len(sample)
    coverage = len(pixels) / (128 * 128)
    return (round(r / 12) * 12, round(g / 12) * 12, round(b / 12) * 12, round(coverage, 2))


def style_signature(record: dict):
    style = record.get("style") or {}
    ratio = float(record.get("width", 1)) / max(float(record.get("height", 1)), 0.01)
    ratio_bucket = round(math.log(max(ratio, 0.02), 1.18))
    kind = "picture" if record.get("shape_type") in PICTURE_TYPES else "native"
    return {
        "kind": kind,
        "shape_type": record.get("shape_type"),
        "auto_shape_type": record.get("auto_shape_type"),
        "ratio_bucket": ratio_bucket,
        "rotation_bucket": round(float(record.get("rotation", 0)) / 5),
        "fill_type": style.get("fill_type"),
        "fill_color": style.get("fill_color"),
        "fill_back_color": style.get("fill_back_color"),
        "gradient_stops": style.get("gradient_stops") or [],
        "line_color": style.get("line_color"),
        "line_weight": style.get("line_weight"),
        "line_dash": style.get("line_dash"),
    }


def keywords(record: dict):
    raw = " ".join(
        str(record.get(k, ""))
        for k in ("source_shape_name", "source_alt_text", "source_deck_name")
    )
    tokens = []
    for token in raw.replace("_", " ").replace("-", " ").split():
        token = token.strip().lower()
        if token and token not in tokens:
            tokens.append(token)
    return tokens[:20]


def make_id(category: str, index: int) -> str:
    prefixes = {
        "cards": "card",
        "icons": "icon",
        "lines": "line",
        "connectors": "connector",
        "arrows": "arrow",
        "badges": "badge",
        "basic_shapes": "shape",
        "decorative_shapes": "decor-shape",
        "decorative_visuals": "decor-visual",
        "logos": "logo",
        "charts": "chart",
        "tables": "table",
        "backgrounds": "background",
        "components": "component",
    }
    return f"{prefixes.get(category, category)}-{index:04d}"


def write_contact_sheets(category_dir: Path, items: list[dict]):
    if not items:
        return []
    out_dir = category_dir / "contact_sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    cell_w, cell_h = 260, 222
    cols, rows = 4, 4
    per_page = cols * rows
    title_font = load_font(15)
    id_font = load_font(13)
    paths = []
    for page_index in range(math.ceil(len(items) / per_page)):
        chunk = items[page_index * per_page : (page_index + 1) * per_page]
        sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (242, 245, 248))
        draw = ImageDraw.Draw(sheet)
        for idx, item in enumerate(chunk):
            col = idx % cols
            row = idx // cols
            x, y = col * cell_w, row * cell_h
            draw.rounded_rectangle((x + 7, y + 7, x + cell_w - 7, y + cell_h - 7), 8, fill="white", outline=(218, 225, 232))
            preview = Image.open(category_dir / item["preview"]).convert("RGBA")
            preview.thumbnail((220, 160), Image.Resampling.LANCZOS)
            checker = Image.new("RGB", (228, 166), (248, 249, 250))
            checker.paste(preview.convert("RGB"), ((228 - preview.width) // 2, (166 - preview.height) // 2), preview.getchannel("A"))
            sheet.paste(checker, (x + 16, y + 14))
            draw.text((x + 16, y + 184), item["id"], font=title_font, fill=(14, 72, 128))
            source = f"{item['source_deck_name']} / S{item['source_slide']}"
            draw.text((x + 16, y + 203), source[:34], font=id_font, fill=(80, 91, 104))
        out_path = out_dir / f"contact-{page_index + 1:03d}.png"
        sheet.save(out_path)
        paths.append(str(out_path.relative_to(category_dir)).replace("\\", "/"))
    return paths


def generate_html(out_dir: Path, manifest: dict):
    cards = []
    for item in manifest["items"]:
        rel_preview = f"{item['category']}/{item['preview']}"
        tags = " ".join(item.get("keywords", []))
        cards.append(
            f'''<article class="asset" data-category="{html.escape(item['category'])}" data-search="{html.escape((item['id']+' '+tags+' '+item['source_deck_name']).lower())}">
              <div class="preview"><img loading="lazy" src="{html.escape(rel_preview)}" alt="{html.escape(item['id'])}"></div>
              <div class="meta"><strong>{html.escape(item['id'])}</strong><span>{html.escape(CATEGORY_LABELS.get(item['category'], item['category']))}</span></div>
              <small>{html.escape(item['source_deck_name'])} · 第 {item['source_slide']} 页 · 重复 {item['occurrences']} 次</small>
            </article>'''
        )
    category_buttons = ['<button class="active" data-category="all">全部</button>']
    for cat, count in manifest["category_counts"].items():
        category_buttons.append(f'<button data-category="{cat}">{html.escape(CATEGORY_LABELS.get(cat, cat))} <b>{count}</b></button>')
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PPT 非文本元素图库</title><style>
body{{margin:0;font-family:"Microsoft YaHei",Arial,sans-serif;background:#f3f6f9;color:#142033}}header{{position:sticky;top:0;z-index:3;background:#fff;border-bottom:1px solid #dce4ec;padding:18px 28px}}h1{{margin:0 0 10px;font-size:24px}}.summary{{color:#5f6f82;font-size:14px}}.tools{{display:flex;gap:12px;margin-top:14px;flex-wrap:wrap}}input{{min-width:320px;padding:10px 12px;border:1px solid #cbd6e2;border-radius:8px}}button{{padding:9px 12px;border:1px solid #cbd6e2;background:#fff;border-radius:8px;cursor:pointer}}button.active{{background:#0f64ae;color:#fff;border-color:#0f64ae}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;padding:22px 28px}}.asset{{background:#fff;border:1px solid #dce4ec;border-radius:12px;padding:12px;box-shadow:0 2px 7px #2c4d6a12}}.preview{{height:170px;display:flex;align-items:center;justify-content:center;background:#f8fafc;border-radius:8px}}.preview img{{max-width:95%;max-height:95%}}.meta{{display:flex;justify-content:space-between;gap:8px;margin-top:10px}}.meta span,small{{color:#6b798a}}small{{display:block;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.hidden{{display:none}}
</style></head><body><header><h1>PPT 非文本元素图库</h1><div class="summary">来源 {manifest['source_deck_count']} 个历史 PPTX、{manifest['source_slide_count']} 页；提取 {manifest['raw_record_count']} 个候选，去重后 {manifest['unique_count']} 个。</div><div class="tools"><input id="q" placeholder="搜索 ID、文件名或关键词">{''.join(category_buttons)}</div></header><main>{''.join(cards)}</main>
<script>const assets=[...document.querySelectorAll('.asset')],buttons=[...document.querySelectorAll('button')],q=document.querySelector('#q');let category='all';function run(){{const s=q.value.trim().toLowerCase();assets.forEach(x=>x.classList.toggle('hidden',!(category==='all'||x.dataset.category===category)||!x.dataset.search.includes(s)))}}buttons.forEach(b=>b.onclick=()=>{{buttons.forEach(x=>x.classList.remove('active'));b.classList.add('active');category=b.dataset.category;run()}});q.oninput=run;</script></body></html>'''
    (out_dir / "catalog.html").write_text(body, encoding="utf-8")


def generate_readme(out_dir: Path, manifest: dict):
    lines = [
        "# PPT 非文本元素图库",
        "",
        f"本图库从项目内 {manifest['source_deck_count']} 个历史 PPTX、{manifest['source_slide_count']} 页中提取。原始候选 {manifest['raw_record_count']} 个，视觉去重后 {manifest['unique_count']} 个。",
        "",
        "## 使用方式",
        "",
        "- 打开 `catalog.html` 搜索和浏览全部元素。",
        "- 每个分类文件夹中的 `components.pptx` 保存原生或原始 PowerPoint 对象，每页一个组件，可直接复制到其他 PPT。",
        "- `previews/` 是统一尺寸的视觉检索预览；图片类素材另存于 `assets/png/`。",
        "- `manifest.json` 与 `manifest.csv` 可用于后续向量化、相似度检索和视觉模型 Top-K 复核。",
        "- `packages/` 中提供各分类 ZIP 包。",
        "",
        "## 分类统计",
        "",
        "| 分类 | 数量 |",
        "|---|---:|",
    ]
    for cat, count in manifest["category_counts"].items():
        lines.append(f"| {CATEGORY_LABELS.get(cat, cat)} | {count} |")
    lines += [
        "",
        "## 注意",
        "",
        "- 图库来源于历史生成文件，包含生成式图标和近似品牌标志；正式对外使用 Logo 前应替换为官方授权文件。",
        "- 图标 PNG 可移动和缩放，但内部路径不可编辑；原生卡片、线条、箭头和基础形状在组件包中保持 PowerPoint 可编辑属性。",
        "- 卡片预览已清除原始文字，只保留非文本视觉结构。",
    ]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    raw_manifest = json.loads(Path(args.raw_manifest).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for record in raw_manifest["records"]:
        preview_path = Path(record["preview"])
        try:
            image = Image.open(preview_path).convert("RGBA")
            w_pt = float(record.get("width", 0))
            h_pt = float(record.get("height", 0))
            if record.get("category") != "connectors" and record.get("shape_type") not in PICTURE_TYPES and min(w_pt, h_pt) <= 5.0 and max(w_pt, h_pt) >= 20.0:
                record["category"] = "lines"
            # Small pictures anchored in a slide's top-right brand zone are
            # treated as logos even when PowerPoint discarded their alt text.
            if record.get("category") == "icons" and record.get("shape_type") in PICTURE_TYPES:
                slide_w = max(float(record.get("slide_width", 1)), 1.0)
                slide_h = max(float(record.get("slide_height", 1)), 1.0)
                right = float(record.get("left", 0)) + float(record.get("width", 0))
                if float(record.get("top", 0)) <= slide_h * 0.14 and right >= slide_w * 0.74:
                    record["category"] = "logos"
            normalized = normalized_rgba(image)
            norm_sha = hashlib.sha256(normalized.tobytes()).hexdigest()
            d_hash = dhash_hex(image)
            color_sig = visible_color_signature(image)
            style_sig = style_signature(record)
            key_payload = {
                "category": record["category"],
                "dhash": d_hash,
                "color": color_sig,
                "style": style_sig,
            }
            visual_key = hashlib.sha1(json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            candidates.append({
                "record": record,
                "image": image,
                "normalized": normalized,
                "norm_sha256": norm_sha,
                "dhash": d_hash,
                "color_signature": color_sig,
                "style_signature": style_sig,
                "visual_key": visual_key,
                "pixel_area": image.width * image.height,
            })
        except Exception as exc:
            raw_manifest.setdefault("errors", []).append({"raw_id": record.get("raw_id"), "error": f"image analysis: {exc}"})

    groups = defaultdict(list)
    for candidate in candidates:
        groups[(candidate["record"]["category"], candidate["visual_key"])].append(candidate)

    unique_items = []
    per_category_index = Counter()
    for (category, _), group in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        representative = max(group, key=lambda x: x["pixel_area"])
        per_category_index[category] += 1
        item_id = make_id(category, per_category_index[category])
        category_dir = out_dir / category
        preview_dir = category_dir / "previews"
        asset_dir = category_dir / "assets" / "png"
        preview_dir.mkdir(parents=True, exist_ok=True)
        representative["normalized"].save(preview_dir / f"{item_id}.png")
        asset_rel = None
        if representative["record"].get("shape_type") in PICTURE_TYPES or category in {"icons", "logos", "decorative_visuals", "backgrounds"}:
            asset_dir.mkdir(parents=True, exist_ok=True)
            source = Path(representative["record"]["preview"])
            shutil.copy2(source, asset_dir / f"{item_id}.png")
            asset_rel = f"assets/png/{item_id}.png"
        sources = []
        for candidate in group:
            record = candidate["record"]
            sources.append({
                "source_deck": record["source_deck"],
                "source_deck_name": record["source_deck_name"],
                "source_slide": record["source_slide"],
                "source_shape_id": record["source_shape_id"],
                "source_shape_name": record["source_shape_name"],
                "raw_id": record["raw_id"],
            })
        record = representative["record"]
        unique_items.append({
            "id": item_id,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, category),
            "asset_kind": "picture" if record.get("shape_type") in PICTURE_TYPES else "native_editable",
            "preview": f"previews/{item_id}.png",
            "asset_png": asset_rel,
            "source_deck": record["source_deck"],
            "source_deck_name": record["source_deck_name"],
            "source_slide": record["source_slide"],
            "source_shape_id": record["source_shape_id"],
            "source_shape_name": record["source_shape_name"],
            "source_alt_text": record.get("source_alt_text", ""),
            "shape_type": record.get("shape_type"),
            "auto_shape_type": record.get("auto_shape_type"),
            "width": record.get("width"),
            "height": record.get("height"),
            "left": record.get("left"),
            "top": record.get("top"),
            "slide_width": record.get("slide_width"),
            "slide_height": record.get("slide_height"),
            "rotation": record.get("rotation"),
            "style": record.get("style"),
            "keywords": keywords(record),
            "visual_dhash": representative["dhash"],
            "normalized_sha256": representative["norm_sha256"],
            "color_signature": representative["color_signature"],
            "occurrences": len(group),
            "sources": sources,
        })

    source_slide_count = 0
    for deck in raw_manifest["source_decks"]:
        slides = {r["source_slide"] for r in raw_manifest["records"] if r["source_deck"] == deck["path"]}
        source_slide_count += len(slides)
    category_counts = dict(sorted(Counter(x["category"] for x in unique_items).items()))
    manifest = {
        "schema": "ppt-image-repository/v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "project_root": raw_manifest["project_root"],
        "source_deck_count": len(raw_manifest["source_decks"]),
        "source_slide_count": source_slide_count,
        "raw_record_count": raw_manifest["record_count"],
        "unique_count": len(unique_items),
        "category_counts": category_counts,
        "source_decks": raw_manifest["source_decks"],
        "errors": raw_manifest.get("errors", []),
        "items": unique_items,
    }

    for category, items in defaultdict(list, {cat: [x for x in unique_items if x["category"] == cat] for cat in category_counts}).items():
        category_dir = out_dir / category
        contact_sheets = write_contact_sheets(category_dir, items)
        cat_manifest = {
            "category": category,
            "label": CATEGORY_LABELS.get(category, category),
            "count": len(items),
            "contact_sheets": contact_sheets,
            "items": items,
        }
        (category_dir / "manifest.json").write_text(json.dumps(cat_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "category", "category_label", "asset_kind", "source_deck_name", "source_deck", "source_slide",
            "source_shape_id", "source_shape_name", "shape_type", "auto_shape_type", "width", "height", "rotation",
            "occurrences", "preview", "asset_png", "keywords",
        ])
        writer.writeheader()
        for item in unique_items:
            row = {k: item.get(k) for k in writer.fieldnames}
            row["keywords"] = "|".join(item.get("keywords", []))
            writer.writerow(row)
    generate_html(out_dir, manifest)
    generate_readme(out_dir, manifest)
    print(json.dumps({"raw": manifest["raw_record_count"], "unique": manifest["unique_count"], "categories": category_counts, "errors": len(manifest["errors"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
