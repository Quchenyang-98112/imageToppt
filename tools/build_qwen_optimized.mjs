import fs from 'node:fs';
import path from 'node:path';
import PptxGenJS from 'pptxgenjs';

const outRoot = path.resolve(process.argv[2] || 'output/sources-9-qwen-optimized');
const sourceDir = path.resolve(process.argv[3] || 'C:/Users/LENOVO/Desktop/sources');
const repoRoot = path.resolve(process.argv[4] || 'Image repository');
const auditDir = path.join(outRoot, 'nontext');
const ocrDir = path.resolve('output/sources-9-qwen-knight/ocr');
const layoutDir = path.resolve('output/sources-9-qwen-knight/layout');
const analysisDir = path.resolve('output/sources-9-qwen-library/analysis');
fs.mkdirSync(path.join(outRoot, 'qa'), { recursive: true });

const sourceNames = ['b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png', 'saas.png', '智慧养老.png', '李佳1.png', '李佳2.png', '李佳3.png', '识别1.png', '识别2.jpg', '识别3.png'];
const policy = JSON.parse(fs.readFileSync(path.resolve('config/qwen-global-policy.json'), 'utf8'));
if (policy.schema === 'qwen-global-reconstruction-policy/v3') {
  throw new Error('build_qwen_optimized is a v2 executor and is disabled globally. It lacks BG_CLEAN residual proof, actual gallery asset execution, and move/delete QA.');
}
const gallery = JSON.parse(fs.readFileSync(path.join(repoRoot, 'manifest.json'), 'utf8')).items || [];
const WIDE_W = 13.333333, WIDE_H = 7.5;

function readJson(file) { return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null; }
function num(v, d = 0) { const n = Number(v); return Number.isFinite(n) ? n : d; }
function box(v) {
  if (!Array.isArray(v) || v.length < 4) return null;
  const a = v.map(Number); if (!a.every(Number.isFinite)) return null;
  // OCR records are [x,y,w,h]; non-text audit records already carry this contract.
  return { x: a[0], y: a[1], w: a[2], h: a[3] };
}
function cornerBox(v) {
  if (!Array.isArray(v) || v.length < 4) return null;
  const a = v.map(Number); if (!a.every(Number.isFinite) || a[2] <= a[0] || a[3] <= a[1]) return null;
  return { x: a[0], y: a[1], w: a[2] - a[0], h: a[3] - a[1] };
}
function overlap(a, b) {
  const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y), r = Math.min(a.x + a.w, b.x + b.w), d = Math.min(a.y + a.h, b.y + b.h);
  const inter = Math.max(0, r - x) * Math.max(0, d - y), union = a.w * a.h + b.w * b.h - inter;
  return union > 0 ? inter / union : 0;
}
function hex(v, fallback = 'FFFFFF') {
  const s = String(v || '').replace('#', '').trim();
  return /^[0-9a-f]{6}$/i.test(s) ? s.toUpperCase() : fallback;
}
function semanticColor(e) {
  const s = `${e.semantic || ''} ${e.role || ''} ${e.kind || ''}`.toLowerCase();
  if (/red|error|warning|失败|风险|警告/.test(s)) return 'D9534F';
  if (/dark|深蓝|标题|header|badge|label|阶段|flow|arrow/.test(s)) return '1768B5';
  if (/light|浅蓝|background|swoosh|panel|card|container/.test(s)) return 'DDECF9';
  return 'FFFFFF';
}
function styleFill(e) {
  const raw = e.fill;
  if (typeof raw === 'string') return hex(raw, semanticColor(e));
  if (raw && typeof raw === 'object') return hex(raw.color || raw.primary, semanticColor(e));
  const roles = e.colorRoles;
  if (Array.isArray(roles) && roles.length) return hex(roles[0], semanticColor(e));
  if (roles && typeof roles === 'object') return hex(roles.primary, semanticColor(e));
  return semanticColor(e);
}
function styleStroke(e) {
  const s = hex(typeof e.stroke === 'string' ? e.stroke : e.stroke?.color, styleFill(e));
  // Qwen analysis uses red as a QA annotation stroke on several objects; it is
  // not part of the source styling, so neutralize it in the editable build.
  if (/^(C90C10|FF0000|D00000)$/i.test(s)) return 'D9E2EF';
  return s;
}
function toks(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ').split(/\s+/).filter(x => x.length > 1); }
function chooseGallery(e) {
  const s = `${e.semantic || ''} ${e.role || ''} ${e.kind || ''}`.toLowerCase();
  const direct = /pie|饼图|piechart/.test(s) ? 'icon-0007' : /lightbulb|bulb|灯泡|idea/.test(s) ? 'icon-0026' : /bar chart|barchart|柱状|growthchart|upward/.test(s) ? 'icon-0004' : /brain|aihead|human head|大脑/.test(s) ? 'icon-0012' : /target|crosshair|靶|目标/.test(s) ? 'icon-0008' : null;
  if (direct) {
    const hit = gallery.find(x => x.id === direct);
    if (hit) {
      const preview = path.join(repoRoot, hit.category, hit.preview.replaceAll('/', path.sep));
      if (fs.existsSync(preview)) return { id: hit.id, category: hit.category, preview, score: 99 };
    }
  }
  const wanted = /logo|brand|企业|品牌/.test(s) ? ['logos'] : /arrow|flow|阶段|ribbon/.test(s) ? ['arrows', 'badges', 'cards'] : /card|panel|container|badge|pill/.test(s) ? ['cards', 'badges', 'basic_shapes'] : ['icons', 'decorative_visuals', 'badges', 'arrows'];
  let best = null, bestScore = -1;
  for (const item of gallery) {
    if (!wanted.includes(item.category)) continue;
    const key = [...(item.keywords || []), item.source_shape_name || '', item.category || ''].join(' ').toLowerCase();
    let score = 0;
    for (const t of toks(s)) if (key.includes(t)) score += 2;
    if (/pie|饼|渗透/.test(s) && /pie|饼/.test(key)) score += 12;
    if (/chart|bar|growth|增长|趋势|柱状/.test(s) && /chart|bar|growth|rising|趋势/.test(key)) score += 12;
    if (/bulb|idea|灯/.test(s) && /bulb|idea|light/.test(key)) score += 12;
    if (/car|汽车|vehicle/.test(s) && /car|vehicle/.test(key)) score += 12;
    if (/brain|ai|智能|大脑/.test(s) && /brain|ai/.test(key)) score += 12;
    if (score > bestScore) { bestScore = score; best = item; }
  }
  if (!best) return null;
  const preview = path.join(repoRoot, best.category, best.preview.replaceAll('/', path.sep));
  return fs.existsSync(preview) ? { id: best.id, category: best.category, preview, score: bestScore } : null;
}
function findLayoutAsset(e, layoutAssets) {
  const et = toks(`${e.semantic || ''} ${e.role || ''}`);
  let best = null, score = 0;
  for (const a of layoutAssets || []) {
    const at = toks(a.semantic || '');
    const s = et.filter(t => at.includes(t)).length;
    if (s > score && cornerBox(a.bbox)) { best = a; score = s; }
  }
  return score >= 1 ? best : null;
}
function addNative(slide, e, W, H) {
  const b = box(e.bbox); if (!b || b.w < 2 || b.h < 2 || e.kind === 'pageBackground') return false;
  const x = b.x / W * WIDE_W, y = b.y / H * WIDE_H, w = b.w / W * WIDE_W, h = b.h / H * WIDE_H;
  const fill = styleFill(e), stroke = styleStroke(e);
  const line = { color: stroke, width: Math.max(.25, num(e.strokeWidth, 0.7)), transparency: e.stroke ? 0 : 100 };
  const base = { x, y, w, h, fill: { color: fill, transparency: num(e.opacity, 1) < 1 ? Math.round((1 - num(e.opacity, 1)) * 100) : 0 }, line };
  const k = `${e.kind || ''} ${e.role || ''}`.toLowerCase();
  const ST = slide._slideObjects ? slide._slideObjects : null;
  if (/line|divider|connector|headerdecoration/.test(k)) slide.addShape(pptx.ShapeType.line, { x, y: y + h / 2, w, h: 0, line: { color: stroke, width: Math.max(.5, num(e.strokeWidth, 1)), transparency: 0 } });
  else if (/arrow|chevron|flow|ribbon/.test(k)) slide.addShape(pptx.ShapeType.chevron, base);
  else if (/circle|ellipse|arc/.test(k)) slide.addShape(pptx.ShapeType.ellipse, base);
  else if (/triangle/.test(k)) slide.addShape(pptx.ShapeType.triangle, base);
  else slide.addShape(num(e.radius, 0) > 0 || /card|panel|badge|pill/.test(k) ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, base);
  return true;
}
function addText(slide, e, W, H, idx) {
  const b = box(e.bbox); const text = String(e.text || '').trim(); if (!b || !text) return null;
  const x = b.x / W * WIDE_W, y = b.y / H * WIDE_H, w = b.w / W * WIDE_W, h = Math.max(.08, b.h / H * WIDE_H);
  const title = idx === 0 || b.y < H * .2;
  const numeric = /\d/.test(text) && text.length < 24;
  const px = Math.max(9, Math.min(48, b.h * (title ? 1.05 : numeric ? .64 : .72)));
  slide.addText(text, { x, y, w, h, fontFace: 'Microsoft YaHei', fontSize: px * .6, bold: title || numeric || b.h > 38, color: title || numeric ? '135DA8' : '202020', margin: 0, fit: 'shrink', valign: 'mid', breakLine: false, lang: 'zh-CN' });
  return { id: e.id || `ocr-${idx + 1}`, text, bbox: [b.x, b.y, b.w, b.h], fontPx: px, score: 1.0 };
}

const pptx = new PptxGenJS();
pptx.defineLayout({ name: 'WIDE', width: WIDE_W, height: WIDE_H }); pptx.layout = 'WIDE';
pptx.author = 'Qwen-only dual-route reconstruction'; pptx.subject = 'Global OCR/non-text policy'; pptx.title = 'Qwen-only 图库优先 9页优化版';
const slideReports = [], matchManifest = [];
for (let i = 0; i < sourceNames.length; i++) {
  const name = sourceNames[i], stem = path.basename(name, path.extname(name));
  const source = path.join(sourceDir, name), ocr = readJson(path.join(ocrDir, `${stem}.ocr.v1.json`));
  const audit = readJson(path.join(auditDir, `${stem}.nontext.audit.json`));
  const layout = readJson(path.join(layoutDir, `${stem}.layout.v1.json`));
  const analysis = readJson(path.join(analysisDir, `${stem}.analysis.json`));
  if (!ocr || !audit) throw new Error(`missing route record for ${name}`);
  const W = num(audit.source_width || ocr.source?.width, 1600), H = num(audit.source_height || ocr.source?.height, 900);
  const slide = pptx.addSlide(); slide.background = { color: 'FFFFFF' };
  const analysisNative = (analysis?.elements || []).filter(e => !['text', 'icon', 'image'].includes(e.kind)).map((e, j) => ({ ...e, id: e.id || `analysis-native-${j + 1}`, zIndex: num(e.zIndex, j), bbox: [e.x, e.y, e.w, e.h], _analysis: true }));
  const layoutNative = (analysisNative.length ? [] : (layout?.objects || []).map((e, j) => ({ ...e, id: e.id || `layout-native-${j + 1}`, zIndex: num(e.zIndex, j), bbox: e.bbox, _layout: true })));
  const native = [...analysisNative, ...layoutNative];
  // The legacy layout route already supplies the stable geometry for native
  // containers. The audit route remains authoritative for missing-element
  // inventory and asset semantics, but its coarse container boxes are not
  // layered a second time (which previously created oversized outlines).
  native.sort((a, b) => num(a.zIndex) - num(b.zIndex));
  let nativeCount = 0; for (const e of native) if (addNative(slide, e, W, H)) nativeCount++;
  const analysisAssets = (analysis?.elements || []).filter(e => ['icon', 'image'].includes(e.kind)).map((e, j) => ({ ...e, id: e.id || `analysis-asset-${j + 1}`, role: e.role || 'asset', kind: e.kind, bbox: [e.x, e.y, e.w, e.h], semantic: e.name || e.role || '' }));
  const assets = [...(analysisAssets.length ? analysisAssets : (audit.imagegenAssets || []))].sort((a, b) => num(a.zIndex) - num(b.zIndex));
  let assetCount = 0, unmatched = 0;
  for (const e of assets) {
    const la = analysisAssets.length ? null : findLayoutAsset(e, layout?.assets || []);
    let b = la ? cornerBox(la.bbox) : box(e.bbox); if (!b) continue;
    const role = `${e.role || ''} ${e.kind || ''}`.toLowerCase();
    if (!la && /logo|brand/.test(role) && (b.w > 220 || b.h > 180)) { b = { x: W - 95, y: 12, w: 70, h: 60 }; }
    if (!la && /icon|pictogram|glyph/.test(role) && (b.w > 180 || b.h > 180)) {
      const cw = Math.max(24, Math.min(82, b.w > 0 ? b.w * 0.22 : 48));
      const ch = Math.max(24, Math.min(82, b.h > 0 ? b.h * 0.22 : 48));
      b = { x: b.x, y: b.y, w: cw, h: ch };
    }
    if (/abstractbackground|background/.test(role)) continue;
    const m = chooseGallery(e);
    if (!m) { unmatched++; continue; }
    slide.addImage({ path: m.preview, x: b.x / W * WIDE_W, y: b.y / H * WIDE_H, w: Math.max(.03, b.w / W * WIDE_W), h: Math.max(.03, b.h / H * WIDE_H) });
    assetCount++; matchManifest.push({ slide: i + 1, source: name, element: e.id, semantic: e.semantic, gallery: m, bbox: [b.x, b.y, b.w, b.h] });
  }
  const textRecords = []; for (let j = 0; j < (ocr.lines || []).length; j++) { const r = addText(slide, ocr.lines[j], W, H, j); if (r) textRecords.push(r); }
  slide.addNotes?.(`Qwen-only双路线：OCR文字 ${textRecords.length}；Qwen非文本原生对象 ${nativeCount}；图库资产 ${assetCount}；未匹配资产 ${unmatched}。来源：${name}`);
  const textScore = textRecords.length === (ocr.lines || []).length ? 1 : textRecords.length / Math.max(1, (ocr.lines || []).length);
  const nontextScore = Math.min(1, num(audit.review?.reviewScore, 0) * (assetCount + nativeCount) / Math.max(1, assets.length + native.length));
  slideReports.push({ slide: i + 1, source, source_width: W, source_height: H, text: { objects: textRecords.length, expected: (ocr.lines || []).length, score: textScore, gate: textScore >= policy.quality_gates.text_min_score }, nontext: { nativeObjects: nativeCount, auditedNativeObjects: native.length, galleryAssets: assetCount, auditedAssets: assets.length, unmatchedAssets: unmatched, auditScore: num(audit.review?.reviewScore), score: nontextScore, gate: nontextScore >= policy.quality_gates.nontext_min_score }, fusion: { score: Math.min(textScore, nontextScore), gate: Math.min(textScore, nontextScore) >= policy.quality_gates.fusion_min_score } });
  fs.writeFileSync(path.join(outRoot, 'qa', `slide-${String(i + 1).padStart(2, '0')}-routes.json`), JSON.stringify(slideReports.at(-1), null, 2), 'utf8');
}
const outPptx = path.join(outRoot, 'Qwen-only-图库优先-9页优化版.pptx');
await pptx.writeFile({ fileName: outPptx });
fs.writeFileSync(path.join(outRoot, 'asset-match-manifest.json'), JSON.stringify({ schema: 'qwen-library-match/v2', matches: matchManifest }, null, 2), 'utf8');
fs.writeFileSync(path.join(outRoot, 'text_route_report.json'), JSON.stringify({ schema: 'qwen-text-route/v1', model: 'qwen3.5-ocr', threshold: policy.quality_gates.text_min_score, slides: slideReports.map(x => ({ slide: x.slide, ...x.text })), allPass: slideReports.every(x => x.text.gate) }, null, 2), 'utf8');
fs.writeFileSync(path.join(outRoot, 'nontext_route_report.json'), JSON.stringify({ schema: 'qwen-nontext-route/v2', model: 'qwen3-vl-plus', threshold: policy.quality_gates.nontext_min_score, galleryRoot: repoRoot, slides: slideReports.map(x => ({ slide: x.slide, ...x.nontext })), allPass: slideReports.every(x => x.nontext.gate) }, null, 2), 'utf8');
fs.writeFileSync(path.join(outRoot, 'rebuild_execution_report.json'), JSON.stringify({ schema: 'qwen-global-rebuild/v2', status: 'built_pending_render_review', input_prepared: { source_dir: sourceDir, slide_count: 9 }, visual_inventory_done: true, asset_classification_done: true, imagegen_assets_done: { mode: 'gallery_first_qwen_only', unmatched_assets: slideReports.reduce((n, x) => n + x.nontext.unmatchedAssets, 0), generated_assets_promoted_after_qa: false }, text_fit_done: true, pptx_built: true, render_qa_done: false, local_crop_qa_done: false, validation_done: false, model_profile: { ocr: 'qwen3.5-ocr', vision: 'qwen3-vl-plus', image_generation: 'qwen-image-2.0-pro', uses_openai_or_gpt: false }, thresholds: policy.quality_gates, slides: slideReports }, null, 2), 'utf8');
console.log(JSON.stringify({ output: outPptx, slides: 9, matches: matchManifest.length, unmatched: slideReports.reduce((n, x) => n + x.nontext.unmatchedAssets, 0) }, null, 2));
