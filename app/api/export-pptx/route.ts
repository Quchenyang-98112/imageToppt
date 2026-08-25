import PptxGenJS from 'pptxgenjs';
import type { CanvasElement } from '@/lib/types';
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { hasTextCorruption } from '@/lib/text-integrity';
import { assertV3Element, evaluateBackgroundGate, evaluateEditabilityGate, evaluateFusionGate, evaluateNontextGate, evaluateTextGate, requireIndependentRoutePasses, type BackgroundIntegrityEvidence, type EditabilityEvidence, type FusionEvidence, type NontextRouteEvidence, type RouteGate, type TextRouteEvidence } from '@/lib/reconstruction-v3';
import { assertExecutedGalleryElement } from '@/lib/gallery-asset-policy';
import type { RebuildProtocol } from '@/lib/rebuild-protocol';

const MAX_ELEMENTS = 180;
const execFile = promisify(execFileCallback);
const qaPython = () => process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python';
const safeNumber = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
/** PPTXGenJS expects RRGGBB, while the editor stores #RRGGBB. Accept both once normalized. */
const safeColor = (value: unknown, fallback: string) => {
  const raw = typeof value === 'string' ? value.replace(/^#/, '') : '';
  return /^[0-9a-fA-F]{6}$/.test(raw) ? raw.toUpperCase() : fallback;
};

function normalize(raw: unknown, index: number): CanvasElement | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const kind = String(item.kind);
  if (!['text', 'rectangle', 'ellipse', 'arrow', 'line', 'connector', 'icon', 'image', 'table', 'freeform', 'group'].includes(kind)) return null;
  const sourceBBox = Array.isArray(item.sourceBBox) && item.sourceBBox.length === 4 ? item.sourceBBox.map(Number) as [number, number, number, number] : undefined;
  return { id: String(item.id ?? `object-${index}`), kind: kind as CanvasElement['kind'], name: String(item.name ?? '对象'), x: safeNumber(item.x, 0), y: safeNumber(item.y, 0), w: safeNumber(item.w, 100), h: safeNumber(item.h, 40), text: typeof item.text === 'string' ? item.text.slice(0, 6000) : '', fontSize: safeNumber(item.fontSize, 24), fontWeight: safeNumber(item.fontWeight, 400), color: safeColor(item.color, '111111'), fill: safeColor(item.fill, 'FFFFFF'), stroke: safeColor(item.stroke, 'C90C10'), strokeWidth: safeNumber(item.strokeWidth, 1), align: item.align === 'center' || item.align === 'right' ? item.align : 'left', radius: safeNumber(item.radius, 0), opacity: safeNumber(item.opacity, 1), rows: safeNumber(item.rows, 2), columns: safeNumber(item.columns, 2), cells: Array.isArray(item.cells) ? item.cells.map((row) => Array.isArray(row) ? row.map((cell) => String(cell).slice(0, 300)) : []) : undefined, imageSrc: typeof item.imageSrc === 'string' && item.imageSrc.startsWith('data:image/') ? item.imageSrc : undefined,
    reconstructionClass: typeof item.reconstructionClass === 'string' ? item.reconstructionClass as CanvasElement['reconstructionClass'] : undefined,
    assetKind: typeof item.assetKind === 'string' ? item.assetKind as CanvasElement['assetKind'] : undefined,
    sourceBBox, zIndex: safeNumber(item.zIndex, index), placementConfidence: safeNumber(item.placementConfidence, 0), parentId: item.parentId === null || typeof item.parentId === 'string' ? item.parentId : undefined,
    semanticImpact: typeof item.semanticImpact === 'boolean' ? item.semanticImpact : undefined, qaStatus: typeof item.qaStatus === 'string' ? item.qaStatus as CanvasElement['qaStatus'] : undefined,
    galleryAssetId: typeof item.galleryAssetId === 'string' ? item.galleryAssetId : undefined, gallerySimilarity: typeof item.gallerySimilarity === 'number' ? item.gallerySimilarity : undefined,
    structuralVetoes: Array.isArray(item.structuralVetoes) ? item.structuralVetoes.map(String) : undefined,
    assetSource: typeof item.assetSource === 'string' && item.assetSource.startsWith('data:image/') ? item.assetSource : undefined,
    nativeComponentId: typeof item.nativeComponentId === 'string' ? item.nativeComponentId : undefined,
    sourceCropUsedAsFinal: item.sourceCropUsedAsFinal === true };
}

async function textFit(elements: CanvasElement[]) {
  const textElements = elements.filter((item) => item.kind === 'text' && item.text?.trim());
  const fallback = new Map(textElements.map((item) => [item.id, Math.max(5, (item.fontSize ?? 24) * .6)]));
  if (!textElements.length) return { sizes: fallback, called: 0, notfits: [] as string[], error: '' };
  const folder = await mkdtemp(join(tmpdir(), 'ppt-text-fit-'));
  const input = join(folder, 'input.json');
  try {
    await writeFile(input, JSON.stringify(textElements.map((item) => ({ id: item.id, text: item.text, w: item.w, h: item.h, sourceFontPx: item.fontSize ?? 24, intendedPt: Math.max(5, (item.fontSize ?? 24) * .6), bold: (item.fontWeight ?? 400) >= 600 }))), 'utf8');
    const script = join(process.cwd(), 'tools', 'qa', 'text_fit_batch.py');
    const { stdout } = await execFile(qaPython(), [script, `--input=${input}`], { timeout: 60_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    const payload = JSON.parse(stdout) as { results?: Array<{ id?: string; recommendedPt?: number; fits?: boolean }>; report?: { called?: number; notfits?: unknown[] } };
    for (const result of payload.results ?? []) if (result.id && typeof result.recommendedPt === 'number') fallback.set(result.id, Math.max(5, Math.min(fallback.get(result.id) ?? result.recommendedPt, result.recommendedPt)));
    const notfits = (payload.report?.notfits ?? []).map((item) => {
      if (item && typeof item === 'object' && 'id' in item) return String((item as { id?: unknown }).id ?? '');
      return '';
    }).filter(Boolean);
    return { sizes: fallback, called: payload.report?.called ?? 0, notfits, error: '' };
  } catch (error) {
    return { sizes: fallback, called: 0, notfits: textElements.map((item) => item.id), error: error instanceof Error ? error.message : '文本适配器执行失败' };
  } finally {
    await rm(folder, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function validateImageAssets(elements: CanvasElement[]) {
  const images = elements.filter((item) => item.kind === 'image' && item.imageSrc?.startsWith('data:image/'));
  if (!images.length) return { passed: true, checked: 0, errors: [] as string[] };
  const folder = await mkdtemp(join(tmpdir(), 'ppt-assets-'));
  try {
    await Promise.all(images.map((item, index) => writeFile(join(folder, `asset-${String(index + 1).padStart(3, '0')}.png`), Buffer.from((item.imageSrc ?? '').slice((item.imageSrc ?? '').indexOf(',') + 1), 'base64'))));
    const checker = join(process.cwd(), 'reference', 'knight-imagetopptx-skill', 'scripts', 'check_rebuild_assets.py');
    const { stdout } = await execFile(qaPython(), [checker, '--asset-dir', folder, '--min-padding', '8', '--json'], { timeout: 30_000, maxBuffer: 2_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    const result = JSON.parse(stdout) as { ok?: boolean; errors?: string[] };
    return { passed: result.ok === true, checked: images.length, errors: result.errors ?? [] };
  } catch (error) {
    const output = typeof error === 'object' && error && 'stdout' in error ? String((error as { stdout?: unknown }).stdout ?? '') : '';
    const candidate = output.slice(output.indexOf('{'), output.lastIndexOf('}') + 1);
    try { const result = JSON.parse(candidate) as { errors?: string[] }; return { passed: false, checked: images.length, errors: result.errors ?? ['图标资产校验失败'] }; }
    catch { return { passed: false, checked: images.length, errors: ['图标资产校验程序未正常完成'] }; }
  } finally {
    await rm(folder, { recursive: true, force: true }).catch(() => undefined);
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json() as {
      cleanBackground?: unknown;
      elements?: unknown;
      protocol?: RebuildProtocol;
      routeGates?: Partial<Record<'background' | 'text' | 'nontext' | 'fusion' | 'editability', RouteGate>>;
      backgroundEvidence?: BackgroundIntegrityEvidence;
      editabilityEvidence?: EditabilityEvidence;
      textEvidence?: TextRouteEvidence;
      nontextEvidence?: NontextRouteEvidence;
      fusionEvidence?: FusionEvidence;
    };
    const cleanBackground = typeof body.cleanBackground === 'string' ? body.cleanBackground : '';
    if (!cleanBackground.startsWith('data:image/')) return Response.json({ error: '缺少干净底图。系统拒绝使用含文字的原图导出，以避免元素重影。' }, { status: 422 });
    if (cleanBackground.length > 20_000_000) return Response.json({ error: '干净底图过大，请压缩到 15 MB 以下。' }, { status: 413 });
    const elements = Array.isArray(body.elements) ? body.elements.slice(0, MAX_ELEMENTS).map(normalize).filter((item): item is CanvasElement => item !== null) : [];
    if (body.protocol?.schema !== 'pptx-rebuild-protocol/v3') return Response.json({ error: '缺少 v3 重建协议；禁止按旧流程导出。' }, { status: 422 });
    if (!body.backgroundEvidence || !body.editabilityEvidence || !body.textEvidence || !body.nontextEvidence || !body.fusionEvidence) return Response.json({ error: '缺少背景、文字、非文本、融合或移动/删除可编辑性证据，禁止导出。' }, { status: 422 });
    const backgroundGate = evaluateBackgroundGate(body.backgroundEvidence);
    const editabilityGate = evaluateEditabilityGate(body.editabilityEvidence);
    const routeGates = {
      background: backgroundGate,
      text: evaluateTextGate(body.textEvidence),
      nontext: evaluateNontextGate(body.nontextEvidence),
      fusion: evaluateFusionGate(body.fusionEvidence),
      editability: editabilityGate,
    };
    try { requireIndependentRoutePasses(routeGates); } catch (error) { return Response.json({ error: error instanceof Error ? error.message : '独立路线未通过。', routeGates }, { status: 422 }); }
    for (const route of ['fusion', 'editability'] as const) if (!routeGates[route]?.passed) return Response.json({ error: `${route} 审核未通过，禁止导出。`, routeGates }, { status: 422 });
    try {
      for (const element of elements) {
        assertV3Element(element, 1600, 900, { requirePassed: true });
        assertExecutedGalleryElement(element);
        if (element.reconstructionClass === 'decorative_fixed') throw new Error(`${element.id}: fixed decoration must be merged into BG_CLEAN or the slide master, not left as a foreground object.`);
      }
    } catch (error) { return Response.json({ error: error instanceof Error ? error.message : 'v3 对象契约失败。' }, { status: 422 }); }
    const damagedText = elements.filter((item) => item.kind === 'text' && hasTextCorruption(item.text ?? ''));
    if (damagedText.length) return Response.json({ error: `检测到 ${damagedText.length} 个乱码文本框，系统已阻止导出。请重新执行 AI 识别。` }, { status: 422 });
    const assets = await validateImageAssets(elements);
    if (!assets.passed) return Response.json({ error: `图标资产未通过 Knight 质量门禁：${assets.errors.slice(0, 6).join('；')}` }, { status: 422 });
    const fitted = await textFit(elements);
    if (fitted.error) return Response.json({ error: '文本适配器未正常完成，系统已停止导出以避免生成溢出版面。' }, { status: 500 });
    if (fitted.notfits.length) return Response.json({ error: `以下文本框即使缩小字号仍无法在边界内安全排版：${fitted.notfits.slice(0, 8).join('、')}。请在画布中加宽或加高后再导出。`, elementIds: fitted.notfits }, { status: 422 });
    const pptx = new PptxGenJS(); pptx.defineLayout({ name: 'WIDE', width: 13.333333, height: 7.5 }); pptx.layout = 'WIDE'; pptx.author = '图转可编辑 PPT'; pptx.subject = '由干净底图和原生对象重建的幻灯片'; pptx.title = '可编辑幻灯片';
    const slide = pptx.addSlide(); const scale = 13.333333 / 1600;
    slide.addImage({ data: cleanBackground, x: 0, y: 0, w: 13.333333, h: 7.5 });
    const layerRank = (element: CanvasElement) => element.kind === 'connector' || element.kind === 'line' ? 0 : element.kind === 'text' ? 3 : element.kind === 'image' || element.kind === 'icon' ? 2 : 1;
    const orderedElements = elements.map((element, index) => ({ element, index })).sort((a, b) => (a.element.zIndex ?? layerRank(a.element)) - (b.element.zIndex ?? layerRank(b.element)) || a.index - b.index).map(({ element }) => element);
    for (const element of orderedElements) {
      const x = Math.max(0, element.x * scale), y = Math.max(0, element.y * scale), w = Math.max(.02, element.w * scale), h = Math.max(.02, element.h * scale);
      if (element.kind === 'text') slide.addText(element.text ?? '', { x, y, w, h, fontFace: 'Microsoft YaHei', fontSize: fitted.sizes.get(element.id) ?? Math.max(5, (element.fontSize ?? 24) * .6), bold: (element.fontWeight ?? 400) >= 600, color: safeColor(element.color, '111111'), align: element.align, margin: 0, paraSpaceAfter: 0, breakLine: false, fit: 'shrink', valign: 'top', lang: 'zh-CN' });
      else if (element.kind === 'arrow') slide.addShape(pptx.ShapeType.rightArrow, { x, y, w, h, rotate: element.rotation ?? 0, fill: { color: safeColor(element.fill, '2674C8') }, line: { color: safeColor(element.stroke, '2674C8'), width: Math.max(.25, element.strokeWidth ?? 1) } });
      else if (element.kind === 'ellipse') slide.addShape(pptx.ShapeType.ellipse, { x, y, w, h, fill: { color: safeColor(element.fill, 'FFFFFF'), transparency: Math.round((1 - Math.min(1, Math.max(0, element.opacity ?? 1))) * 100) }, line: { color: safeColor(element.stroke, '2674C8'), width: Math.max(.25, element.strokeWidth ?? 1) } });
      else if (element.kind === 'line' || element.kind === 'connector') slide.addShape(pptx.ShapeType.line, { x, y, w, h: 0, line: { color: safeColor(element.stroke, 'C90C10'), width: Math.max(.25, element.strokeWidth ?? 1) } });
      else if ((element.kind === 'image' || element.kind === 'icon') && (element.assetSource || element.imageSrc)) slide.addImage({ data: element.assetSource || element.imageSrc, x, y, w, h });
      else if (element.kind === 'table') { const rows = Math.max(1, element.rows ?? 2), cols = Math.max(1, element.columns ?? 2); slide.addTable(Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => ({ text: element.cells?.[r]?.[c] ?? '', options: { bold: r === 0, color: safeColor(element.color, '111111') } }))), { x, y, w, h, fontFace: 'Microsoft YaHei', fontSize: Math.max(5, (element.fontSize ?? 18) * .6), border: { type: 'solid', color: safeColor(element.stroke, 'C90C10'), pt: 1 }, fill: { color: safeColor(element.fill, 'FFFFFF') }, margin: .03 }); }
      else { slide.addShape((element.radius ?? 0) > 0 ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, { x, y, w, h, fill: { color: safeColor(element.fill, 'FFFFFF'), transparency: Math.round((1 - Math.min(1, Math.max(0, element.opacity ?? 1))) * 100) }, line: { color: safeColor(element.stroke, 'C90C10'), width: Math.max(.25, element.strokeWidth ?? 1) } }); }
    }
    const buffer = await pptx.write({ outputType: 'nodebuffer' }) as Buffer;
    return new Response(new Uint8Array(buffer), { headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'Content-Disposition': 'attachment; filename="editable-slide.pptx"', 'X-PPT-Text-Fit': `${fitted.called}`, 'X-PPT-Text-Notfit': '0', 'X-PPT-Assets-Checked': `${assets.checked}` } });
  } catch (error) { return Response.json({ error: error instanceof Error ? error.message : '导出失败。' }, { status: 500 }); }
}
