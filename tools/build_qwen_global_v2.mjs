/* Global Qwen-only rebuild executor. Source image remains a BG reference layer;
 * foreground is reconstructed in separate clean-patch, native, asset and OCR layers. */
import fs from 'node:fs';
import path from 'node:path';
import PptxGenJS from 'pptxgenjs';

const outRoot = path.resolve(process.argv[2] || 'output/sources-9-qwen-global-v2');
const sourceDir = path.resolve(process.argv[3] || 'C:/Users/LENOVO/Desktop/sources');
const repoRoot = path.resolve(process.argv[4] || 'Image repository');
const ocrDir = path.resolve(process.argv[5] || 'output/sources-9-qwen-knight/ocr');
const W = 13.333333, H = 7.5;
const names = ['b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png', 'saas.png', '智慧养老.png', '李佳1.png', '李佳2.png', '李佳3.png', '识别1.png', '识别2.jpg', '识别3.png'];
const policy = JSON.parse(fs.readFileSync(path.resolve('config/qwen-global-policy.json'), 'utf8'));
if (policy.schema === 'qwen-global-reconstruction-policy/v3') {
  throw new Error('build_qwen_global_v2 is permanently disabled under policy v3: it places the full source image on the visible slide and uses preview PNGs as final assets. Use the v3 workbench/background/route/editability pipeline.');
}
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const n = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const hex = (value, fallback = 'FFFFFF') => /^[0-9a-f]{6}$/i.test(String(value || '').replace('#', '')) ? String(value).replace('#', '').toUpperCase() : fallback;
function sourceBox(value, sw, sh, label) {
  if (!Array.isArray(value) || value.length !== 4) throw new Error(`coordinate contract: ${label} is not [x,y,w,h]`);
  const [x, y, w, h] = value.map(Number);
  if (![x,y,w,h].every(Number.isFinite) || x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > sw + .1 || y + h > sh + .1) throw new Error(`coordinate contract: ${label} outside source`);
  return {x, y, w, h};
}
function contained(b, sw, sh) {
  const s = Math.min(W / sw, H / sh), ox = (W - sw * s) / 2, oy = (H - sh * s) / 2;
  return {x: ox + b.x * s, y: oy + b.y * s, w: b.w * s, h: b.h * s, s, ox, oy};
}
function fillFor(e) {
  const raw = e.fill;
  if (typeof raw === 'string') return hex(raw, 'FFFFFF');
  if (raw && typeof raw === 'object') return hex(raw.color || raw.primary, 'FFFFFF');
  const role = `${e.role || ''} ${e.semantic || ''} ${e.kind || ''}`.toLowerCase();
  if (/dark|深蓝|title|header|badge|arrow|flow/.test(role)) return '1768B5';
  if (/light|浅蓝|panel|card|background/.test(role)) return 'DDECF9';
  return 'FFFFFF';
}
function strokeFor(e, fill) { return hex(typeof e.stroke === 'string' ? e.stroke : e.stroke?.color, fill); }
function addNative(slide, e, sw, sh) {
  const b = contained(sourceBox(e.bbox, sw, sh, e.id), sw, sh), key = `${e.kind || ''} ${e.role || ''} ${e.semantic || ''}`.toLowerCase();
  if (/pagebackground|edge.?decoration|abstractbackground/.test(key) || String(e.kind||'').toLowerCase()==='path') return false;
  const fill = fillFor(e), stroke = strokeFor(e, fill), opacity = n(e.opacity, 1);
  const options = {x:b.x,y:b.y,w:b.w,h:b.h,fill:{color:fill,transparency:Math.max(0,Math.min(100,Math.round((1-opacity)*100)))},line:{color:stroke,width:Math.max(.25,n(e.strokeWidth,.6)),transparency:e.stroke ? 0 : 100}};
  if (/line|divider|connector/.test(key)) slide.addShape(pptx.ShapeType.line,{x:b.x,y:b.y+b.h/2,w:b.w,h:0,line:{color:stroke,width:Math.max(.5,n(e.strokeWidth,1))}});
  else if (/chevron|arrow|flow|ribbon/.test(key)) slide.addShape(pptx.ShapeType.chevron,options);
  else if (/ellipse|circle|arc/.test(key)) slide.addShape(pptx.ShapeType.ellipse,options);
  else if (/triangle/.test(key)) slide.addShape(pptx.ShapeType.triangle,options);
  else slide.addShape((n(e.radius,0)>0 || /card|panel|badge|pill/.test(key)) ? pptx.ShapeType.roundRect : pptx.ShapeType.rect,options);
  return true;
}
function rankedCandidate(matches, rerank, id) {
  const adjudicated=(rerank?.selections || []).find(x=>x.elementId===id && x.approved===true);
  if (adjudicated?.candidate?.preview) return {...adjudicated.candidate, score: adjudicated.visualSimilarity, adjudicated: true};
  const row = (matches?.results || []).find(x => x.elementId === id), first = row?.candidates?.[0];
  if (!first) return null;
  // Build only accepted gallery hits. Provisional hits are held out, not silently added.
  return first.score >= policy.quality_gates.library_direct_similarity ? first : null;
}
function addCleanPatch(slide, b, fill='FFFFFF') { slide.addShape(pptx.ShapeType.rect,{x:b.x,y:b.y,w:b.w,h:b.h,fill:{color:fill},line:{transparency:100}}); }
function overlap(a,b) { const iw=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)), ih=Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)); return iw*ih/(Math.max(1,a.w*a.h)); }
function addText(slide, line, sw, sh, index, nativeObjects) {
  const b = contained(sourceBox(line.bbox, sw, sh, line.id || `ocr-${index}`), sw, sh), text = String(line.text || '').trim(); if (!text) return false;
  const sourceB=sourceBox(line.bbox, sw, sh, line.id || `ocr-${index}`);
  const host=(nativeObjects||[]).find(e=>{ const eb=sourceBox(e.bbox,sw,sh,e.id); const cx=sourceB.x+sourceB.w/2,cy=sourceB.y+sourceB.h/2; return cx>=eb.x&&cx<=eb.x+eb.w&&cy>=eb.y&&cy<=eb.y+eb.h; });
  const hostFill=host?fillFor(host):'FFFFFF'; const dark=/^(0[0-9A-F]{5}|1[0-9A-F]{5}|2[0-9A-F]{5}|3[0-9A-F]{5}|4[0-9A-F]{5})$/.test(hostFill);
  // Source height is in image pixels; at a 1600x900 export, roughly .40 pt
  // per source pixel yields a single-line Chinese text run in its measured box.
  const preferredFontPt=Math.max(8,Math.min(36,sourceB.h*(/\d/.test(text)&&text.length<24?.55:.40)));
  // Keep CJK OCR runs single-line. Mixed-size source runs (notably KPI units)
  // are conservatively shrunk rather than wrapped into a visibly wrong layout.
  const maxSingleLinePt=Math.max(7,sourceB.w / Math.max(1,text.length) / 2.25 * .92);
  const sourceFontPx=Math.min(preferredFontPt,maxSingleLinePt);
  const title=sourceB.y<sourceB.h*3 && sourceB.h>=38;
  slide.addText(text,{x:b.x,y:b.y,w:b.w,h:Math.max(.06,b.h),margin:0,breakLine:false,fit:'shrink',valign:'mid',fontFace:'Microsoft YaHei',fontSize:sourceFontPx,bold:title || (/\d/.test(text)&&text.length<24),color:dark?'FFFFFF':(title?'135DA8':'202020'),lang:'zh-CN'});
  return true;
}
function hasOpaqueNativeHost(line, nativeObjects, sw, sh) {
  const b=sourceBox(line.bbox,sw,sh,line.id||'ocr'); const cx=b.x+b.w/2,cy=b.y+b.h/2;
  return (nativeObjects||[]).some(e=>{ const role=String(e.role||'').toLowerCase(), kind=String(e.kind||'').toLowerCase(); if (/pagebackground|edgedecoration|abstractbackground/.test(role) || kind==='path' || String(e.fill||'').toLowerCase()==='none') return false; const eb=sourceBox(e.bbox,sw,sh,e.id); return cx>=eb.x&&cx<=eb.x+eb.w&&cy>=eb.y&&cy<=eb.y+eb.h&&n(e.opacity,1)>.05; });
}

const pptx = new PptxGenJS(); pptx.defineLayout({name:'WIDE',width:W,height:H}); pptx.layout='WIDE';
pptx.author='Qwen-only global reconstruction policy v2'; pptx.title='Qwen-only 9页全局策略重建版';
const reports=[], matchManifest=[], backgroundManifest=[];
for (let index=0; index<names.length; index++) {
  const name=names[index], stem=path.basename(name,path.extname(name)), source=path.join(sourceDir,name);
  const audit=read(path.join(outRoot,'nontext',`${stem}.nontext.audit.json`)), ocr=read(path.join(ocrDir,`${stem}.ocr.v1.json`));
  const matchesPath=path.join(outRoot,'gallery',`${stem}.matches.json`); const matches=fs.existsSync(matchesPath)?read(matchesPath):null;
  const rerankPath=path.join(outRoot,'gallery',`${stem}.rerank.json`); const rerank=fs.existsSync(rerankPath)?read(rerankPath):null;
  const sw=n(audit.source_width || ocr.source?.width), sh=n(audit.source_height || ocr.source?.height);
  if (!sw || !sh) throw new Error(`missing source dimensions: ${name}`);
  const base=contained({x:0,y:0,w:sw,h:sh},sw,sh), slide=pptx.addSlide(); slide.background={color:'F5F7FA'};
  // Layer 1: original reference retained at its undistorted contained bounds.
  slide.addImage({path:source,x:base.x,y:base.y,w:base.w,h:base.h});
  backgroundManifest.push({slide:index+1,source:name,layer:'BG_SOURCE_REFERENCE',mode:'contain_with_letterbox',sourceBounds:[0,0,sw,sh],slideBounds:[base.x,base.y,base.w,base.h]});
  const native=(audit.nativeObjects||[]).slice().sort((a,b)=>n(a.zIndex)-n(b.zIndex));
  const nativeBoxes=native.map(e=>sourceBox(e.bbox,sw,sh,e.id));
  let nativeCount=0; for(const e of native) if(addNative(slide,e,sw,sh)) nativeCount++;
  let accepted=0, provisional=0;
  for(const asset of (audit.imagegenAssets||[]).slice().sort((a,b)=>n(a.zIndex)-n(b.zIndex))) {
    const role=`${asset.role||''} ${asset.semantic||''}`.toLowerCase(); if(/pagebackground|abstractbackground/.test(role)) continue;
    const b=contained(sourceBox(asset.bbox,sw,sh,asset.id),sw,sh);
    const candidate=rankedCandidate(matches,rerank,asset.id);
    // Layer 2: clean patch. Cards added above normally provide their own patch;
    // assets otherwise get a neutral patch to stop source foreground showing through.
    if (!candidate) { provisional++; continue; }
    // Do not paint white over a reconstructed card/panel that is intentionally
    // behind the icon. A patch is only needed when the asset sits on bare source.
    if (!nativeBoxes.some(nb=>overlap(nb,asset.bbox ? sourceBox(asset.bbox,sw,sh,asset.id) : nb)>.75)) addCleanPatch(slide,b,'FFFFFF');
    slide.addImage({path:candidate.preview,x:b.x,y:b.y,w:b.w,h:b.h});
    accepted++; matchManifest.push({slide:index+1,source:name,element:asset.id,bbox:asset.bbox,candidate});
  }
  // Layer 5: only text whose source glyphs were actually covered by an opaque
  // reconstructed object is redrawn. Uncovered reference text is left visually
  // intact until the OCR route has a verified clean-patch replacement.
  let textCount=0; for(const [j,line] of (ocr.lines||[]).entries()) if(hasOpaqueNativeHost(line,native,sw,sh) && addText(slide,line,sw,sh,j,native)) textCount++;
  slide.addNotes?.(`Policy v2 layers: BG_SOURCE_REFERENCE, BG_CLEAN_PATCHES, NATIVE_NON_TEXT, GALLERY_OR_QWEN_ASSETS, OCR_TEXT. Source=${name}`);
  reports.push({slide:index+1,source:name,sourceSize:[sw,sh],transform:{mode:'contain_with_letterbox',scale:base.s,offset:[base.ox,base.oy]},background:{retained:true,mode:'source_reference_with_clean_patches'},diagnostics:{nativeCount,assetsAccepted:accepted,assetsPendingQwenOrRerank:provisional,textCount}});
}
fs.mkdirSync(outRoot,{recursive:true});
const file=path.join(outRoot,'Qwen-only-全局策略v2-9页重建版.pptx'); await pptx.writeFile({fileName:file});
fs.writeFileSync(path.join(outRoot,'background-layer-manifest.json'),JSON.stringify({schema:'background-retention/v1',policy:policy.background_policy,layers:backgroundManifest},null,2));
fs.writeFileSync(path.join(outRoot,'asset-match-manifest.json'),JSON.stringify({schema:'gallery-accepted-matches/v3',directThreshold:policy.quality_gates.library_direct_similarity,matches:matchManifest},null,2));
fs.writeFileSync(path.join(outRoot,'rebuild_execution_report.json'),JSON.stringify({schema:'qwen-global-rebuild/v3',status:'built_pending_render_and_local_crop_review',policy:policy.schema,slides:reports,hardFailsIfUnresolved:['assetsPendingQwenOrRerank','renderedComparisonRequired']},null,2));
console.log(JSON.stringify({output:file,slides:reports.length,acceptedAssets:matchManifest.length,pending:reports.reduce((x,r)=>x+r.diagnostics.assetsPendingQwenOrRerank,0)},null,2));
