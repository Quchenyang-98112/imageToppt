import fs from 'node:fs';
import path from 'node:path';
import PptxGenJS from 'pptxgenjs';

const root = path.resolve(process.argv[2] || 'output/sources-9-qwen-library');
const sourceDir = path.resolve(process.argv[3] || 'C:/Users/LENOVO/Desktop/sources');
const repo = path.resolve(process.argv[4] || 'Image repository');
const outPptx = path.join(root, 'Qwen-only-图库优先-9页可编辑草稿.pptx');
const globalPolicy = JSON.parse(fs.readFileSync(path.resolve('config/qwen-global-policy.json'), 'utf8'));
if (globalPolicy.schema === 'qwen-global-reconstruction-policy/v3') {
  throw new Error('build_qwen_library_draft is disabled globally under v3. Draft/count-based PPTX creation cannot bypass independent rendered route gates.');
}
const qaDir = path.join(root, 'qa');
fs.mkdirSync(qaDir, { recursive: true });

const sourceNames = ['b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png', 'saas.png', '智慧养老.png', '李佳1.png', '李佳2.png', '李佳3.png', '识别1.png', '识别2.jpg', '识别3.png'];
const manifest = JSON.parse(fs.readFileSync(path.join(repo, 'manifest.json'), 'utf8'));
const items = manifest.items || [];
const repoRoot = repo;

function bbox(v) {
  if (Array.isArray(v)) {
    const a = v.map(Number); if (a.length >= 4 && a.every(Number.isFinite)) {
      const [x1, y1, x2, y2] = a; return x2 > x1 && y2 > y1 ? { x: x1, y: y1, w: x2 - x1, h: y2 - y1 } : null;
    }
  }
  if (typeof v === 'string') { const a = v.trim().split(/[ ,]+/).map(Number); return bbox(a); }
  return null;
}
function color(v, fallback = 'FFFFFF') { const s = String(v || '').replace('#', ''); return /^[0-9A-Fa-f]{6}$/.test(s) ? s.toUpperCase() : fallback; }
function opacity(v) { return typeof v === 'number' ? Math.max(0, Math.min(1, v)) : 1; }
function tokens(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ').split(/\s+/).filter(Boolean); }
function assetScore(item, semantic) {
  const text = String(semantic || '').toLowerCase();
  const key = [...(item.keywords || []), item.source_shape_name, item.category].join(' ').toLowerCase();
  let score = 0;
  for (const t of tokens(text)) if (key.includes(t)) score += 3;
  const rules = [
    [/target|靶|目标/, /target/], [/chart|graph|growth|trend|market|规模|增长|柱状|趋势/, /chart|graph|rising|bar|trend/],
    [/pie|饼|渗透率/, /pie/], [/bulb|idea|灯|思考|洞察/, /bulb|idea|light/], [/car|汽车|vehicle/, /car/],
    [/brain|智能|大脑/, /brain/], [/warning|alert|预警|警告/, /warning|triangle/], [/check|success|完成|对勾/, /check|good|success/],
    [/logo|avic|fastcode|品牌/, /logo/], [/arrow|chevron|流程|阶段/, /arrow|chevron/], [/pie|circle|环形/, /pie|circle/]
  ];
  for (const [a, b] of rules) if (a.test(text) && b.test(key)) score += 12;
  if (item.category === 'icons') score += 2;
  return score;
}
function chooseAsset(semantic, preferred = 'icons') {
  const pool = items.filter((x) => x.category === preferred || (preferred === 'icons' && ['logos', 'decorative_visuals'].includes(x.category)));
  const sorted = [...pool].sort((a, b) => assetScore(b, semantic) - assetScore(a, semantic));
  const hit = sorted[0] || items.find((x) => x.category === 'icons');
  if (!hit) return null;
  const p = path.join(repoRoot, hit.category, hit.preview.replaceAll('/', path.sep));
  return { id: hit.id, category: hit.category, preview: p, score: assetScore(hit, semantic), semantic };
}

const pptx = new PptxGenJS();
pptx.defineLayout({ name: 'WIDE', width: 13.333333, height: 7.5 }); pptx.layout = 'WIDE';
pptx.author = 'Qwen-only 图库优先批处理'; pptx.subject = 'Qwen OCR/VL + 非文本元素图库'; pptx.title = '9张原图可编辑PPTX草稿';
const matches = []; const slideReports = [];

function addNative(slide, e, W, H) {
  const b = bbox(e.bbox) || { x: e.x ?? 0, y: e.y ?? 0, w: e.w ?? 0, h: e.h ?? 0 };
  if (!(b.w > 0 && b.h > 0)) return false;
  const x = b.x / W * 13.333333, y = b.y / H * 7.5, w = b.w / W * 13.333333, h = b.h / H * 7.5;
  const fill = color(e.fill, 'FFFFFF'), stroke = color(e.stroke, e.stroke ? 'C7D2E0' : fill);
  const trans = Math.round((1 - opacity(e.opacity)) * 100);
  const line = { color: stroke, width: Math.max(.25, Number(e.strokeWidth ?? 1)), transparency: e.stroke ? 0 : 100 };
  if (e.kind === 'ellipse') slide.addShape(pptx.ShapeType.ellipse, { x, y, w, h, fill: { color: fill, transparency: trans }, line });
  else if (e.kind === 'arrow') slide.addShape(pptx.ShapeType.rightArrow, { x, y, w, h, fill: { color: fill, transparency: trans }, line });
  else if (e.kind === 'line' || e.kind === 'connector') slide.addShape(pptx.ShapeType.line, { x, y, w, h: Math.max(.001, h), line: { color: stroke, width: Math.max(.25, Number(e.strokeWidth ?? 1)) } });
  else if (e.kind === 'table') slide.addShape(pptx.ShapeType.rect, { x, y, w, h, fill: { color: fill, transparency: trans }, line });
  else slide.addShape(Number(e.radius ?? 0) > 0 ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, { x, y, w, h, fill: { color: fill, transparency: trans }, line });
  return true;
}
function addText(slide, e, W, H, index) {
  const b = bbox(e.bbox) || { x: e.x ?? 0, y: e.y ?? 0, w: e.w ?? 100, h: e.h ?? 30 };
  if (!e.text || !(b.w > 0 && b.h > 0)) return false;
  const x = b.x / W * 13.333333, y = b.y / H * 7.5, w = b.w / W * 13.333333, h = Math.max(.08, b.h / H * 7.5);
  const px = Number(e.fontSize ?? Math.max(16, b.h * .75));
  slide.addText(String(e.text), { x, y, w, h, fontFace: 'Microsoft YaHei', fontSize: Math.max(5, Math.min(44, px * .6)), bold: Number(e.fontWeight ?? 400) >= 600, color: color(e.color, '202020'), align: e.align === 'center' || e.align === 'right' ? e.align : 'left', margin: 0, paraSpaceAfterPt: 0, breakLine: false, fit: 'shrink', valign: 'mid', lang: 'zh-CN' });
  return { id: e.id || `ocr-${index}`, text: String(e.text), w: b.w, h: b.h, sourceFontPx: px, intendedPt: px * .6, bold: Number(e.fontWeight ?? 400) >= 600 };
}

for (let index = 0; index < sourceNames.length; index++) {
  const name = sourceNames[index], stem = path.basename(name, path.extname(name));
  const sourcePath = path.join(sourceDir, name);
  const analysisPath = path.join(root, 'analysis', `${stem}.analysis.json`);
  const layoutPath = path.join('output', 'sources-9-qwen-knight', 'layout', `${stem}.layout.v1.json`);
  const ocrPath = path.join('output', 'sources-9-qwen-knight', 'ocr', `${stem}.ocr.v1.json`);
  const analysis = fs.existsSync(analysisPath) ? JSON.parse(fs.readFileSync(analysisPath, 'utf8')) : null;
  const layout = fs.existsSync(layoutPath) ? JSON.parse(fs.readFileSync(layoutPath, 'utf8')) : null;
  const ocr = fs.existsSync(ocrPath) ? JSON.parse(fs.readFileSync(ocrPath, 'utf8')) : null;
  const W = analysis?.protocol?.canvas?.width || layout?.source?.width || ocr?.source?.width || 1600;
  const H = analysis?.protocol?.canvas?.height || layout?.source?.height || ocr?.source?.height || 900;
  const slide = pptx.addSlide(); slide.background = { color: 'FFFFFF' };
  const textFit = []; let nativeCount = 0, assetCount = 0, textCount = 0;
  if (analysis?.cleanBackground) slide.addImage({ data: analysis.cleanBackground, x: 0, y: 0, w: 13.333333, h: 7.5 });
  const elements = Array.isArray(analysis?.elements) ? analysis.elements : [];
  if (elements.length) {
    for (const e of elements.filter((x) => !['text', 'icon', 'image'].includes(x.kind))) if (addNative(slide, e, 1600, 900)) nativeCount++;
    for (const e of elements.filter((x) => x.kind === 'icon' || x.kind === 'image')) {
      const b = { x: Number(e.x || 0), y: Number(e.y || 0), w: Number(e.w || 24), h: Number(e.h || 24) };
      const match = chooseAsset(`${e.name || ''} ${e.role || ''}`); if (!match) continue;
      slide.addImage({ path: match.preview, x: b.x / 1600 * 13.333333, y: b.y / 900 * 7.5, w: Math.max(.04, b.w / 1600 * 13.333333), h: Math.max(.04, b.h / 900 * 7.5), transparency: 0 });
      matches.push({ slide: index + 1, source: name, element: e.id, semantic: e.name, match }); assetCount++;
    }
    for (const e of elements.filter((x) => x.kind === 'text')) { const f = addText(slide, e, 1600, 900, textCount); if (f) { textFit.push(f); textCount++; } }
  } else {
    for (const e of (layout?.objects || [])) if (addNative(slide, e, W, H)) nativeCount++;
    for (const a of (layout?.assets || []).filter((x) => x.classification !== 'rejected')) {
      const b = bbox(a.bbox); if (!b) continue; const match = chooseAsset(`${a.semantic || ''} ${a.role || ''}`);
      if (!match) continue;
      slide.addImage({ path: match.preview, x: b.x / W * 13.333333, y: b.y / H * 7.5, w: Math.max(.04, b.w / W * 13.333333), h: Math.max(.04, b.h / H * 7.5) });
      matches.push({ slide: index + 1, source: name, element: a.id, semantic: a.semantic, match }); assetCount++;
    }
    for (const e of (ocr?.lines || [])) { const f = addText(slide, e, W, H, textCount); if (f) { textFit.push(f); textCount++; } }
  }
  slide.addNotes?.(`Qwen-only 图库优先草稿；来源：${name}；原生对象 ${nativeCount}，图库资产 ${assetCount}，OCR 文本 ${textCount}。`);
  fs.writeFileSync(path.join(qaDir, `slide-${String(index + 1).padStart(2, '0')}-inventory.json`), JSON.stringify({ source: sourcePath, source_width: W, source_height: H, mode: analysis ? 'qwen_semantic_reuse' : 'qwen_vl_layout_draft', nativeCount, assetCount, textCount, textFit }, null, 2), 'utf8');
  slideReports.push({ slide: index + 1, source: sourcePath, mode: analysis ? 'qwen_semantic_reuse' : 'qwen_vl_layout_draft', native_objects: nativeCount, library_assets: assetCount, ocr_text: textCount, missing_complex_assets: analysis ? 0 : (layout?.assetAudit?.added || layout?.assets?.length || 0) });
}

await pptx.writeFile({ fileName: outPptx });
fs.writeFileSync(path.join(root, 'asset-match-manifest.json'), JSON.stringify(matches, null, 2), 'utf8');
fs.writeFileSync(path.join(root, 'rebuild_execution_report.json'), JSON.stringify({ input_prepared: { source_dir: sourceDir, slide_count: sourceNames.length, output: outPptx }, visual_inventory_done: true, asset_classification_done: true, imagegen_assets_done: { status: 'needs_review', note: 'Qwen-only batch reused approved local gallery assets; unmatched complex assets remain flagged rather than silently generated.' }, text_fit_done: { status: 'pending_external_fit', objects: slideReports.reduce((n, x) => n + x.ocr_text, 0) }, pptx_built: true, render_qa_done: false, local_crop_qa_done: false, validation_done: false, model_profile: { ocr: 'qwen3.5-ocr', vision: 'qwen3-vl-plus', image_generation: 'qwen-image-2.0-pro', uses_openai_or_gpt: false }, slides: slideReports }, null, 2), 'utf8');
console.log(JSON.stringify({ output: outPptx, slides: sourceNames.length, matches: matches.length, reports: slideReports }, null, 2));
