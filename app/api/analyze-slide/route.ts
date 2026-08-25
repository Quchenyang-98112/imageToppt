import { dashScopeKey, dashScopeOcrKey, dashScopeOcrModel, dashScopeOcrNativeBaseUrl, dashScopeVisionModel, guardApiSecret } from '@/lib/ai-config';
import type { CanvasElement, ElementKind } from '@/lib/types';
import { buildRebuildProtocol } from '@/lib/rebuild-protocol';
import { hasTextCorruption } from '@/lib/text-integrity';
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

export const runtime = 'nodejs';
// Dense PPT infographics can require a long vision pass plus JSON repair.
export const maxDuration = 600;
const kinds = new Set<ElementKind>(['text', 'rectangle', 'ellipse', 'arrow', 'line', 'connector', 'icon', 'image', 'table', 'freeform', 'group']);
const execFile = promisify(execFileCallback);
const number = (value: unknown, fallback: number, min = 0, max = 5000) => typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, Math.round(value))) : fallback;
const color = (value: unknown, fallback: string) => typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toUpperCase() : fallback;

async function fetchWithRetry(input: string, init?: RequestInit, attempts = 3, stage = 'remote-model') {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(input, init);
      if (response.ok || ![408, 425, 429, 500, 502, 503, 504].includes(response.status) || attempt === attempts - 1) return response;
      lastError = new Error(`远程模型暂时返回 ${response.status}`);
    } catch (error) {
      lastError = error;
      if (attempt === attempts - 1) break;
    }
    await new Promise((resolve) => setTimeout(resolve, 900 * (attempt + 1)));
  }
  const detail = lastError instanceof Error ? lastError.message : String(lastError || 'unknown error');
  throw new Error(`[${stage}] connection failed after ${attempts} attempts: ${detail}`);
}

function repairModelJson(input: string) {
  let result = '', inString = false, escaped = false;
  for (const char of input) {
    if (!inString) { result += char; if (char === '"') inString = true; continue; }
    if (escaped) { result += char; escaped = false; continue; }
    if (char === '\\') { result += char; escaped = true; continue; }
    if (char === '"') { result += char; inString = false; continue; }
    if (char === '\n') { result += '\\n'; continue; }
    if (char === '\r') { result += '\\r'; continue; }
    if (char === '\t') { result += '\\t'; continue; }
    result += char;
  }
  // Common VL-model slips in very long arrays: trailing commas and missing object separators.
  return result.replace(/,\s*([}\]])/g, '$1').replace(/}\s*{/g, '},{').replace(/]\s*\[/g, '],[');
}

function parseWithLocalRepair(json: string) {
  let candidate = repairModelJson(json);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { return JSON.parse(candidate) as { elements?: unknown[]; note?: unknown }; }
    catch (error) {
      const message = error instanceof Error ? error.message : '', position = Number(message.match(/position (\d+)/)?.[1]);
      if (!Number.isFinite(position) || position < 1 || position >= candidate.length || !/Expected ',' or '[}\]]' after|Expected ',' or '}' after/.test(message)) throw error;
      // The parser has reached the next valid value but the preceding separator was omitted.
      candidate = `${candidate.slice(0, position)},${candidate.slice(position)}`;
    }
  }
  throw new Error('本地 JSON 修复超过最大尝试次数。');
}

function jsonFromModel(content: string) {
  const candidate = content.match(/```(?:json)?\s*([\s\S]*?)```/i)?.[1] || content;
  const start = candidate.indexOf('{'), end = candidate.lastIndexOf('}');
  if (start < 0 || end < start) throw new Error('视觉模型未返回可解析的对象 JSON。');
  const json = candidate.slice(start, end + 1);
  return parseWithLocalRepair(json);
}

async function askForJsonRepair(key: string, malformed: string) {
  const prompt = `Repair the following malformed JSON only. Preserve all values, Chinese text, numbers and array items exactly. Return one valid JSON object and no markdown.\n\n${malformed}`;
  const response = await fetch('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: dashScopeVisionModel(), temperature: 0, response_format: { type: 'json_object' }, messages: [{ role: 'user', content: prompt }] }) });
  const payload = await response.json() as { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string } };
  if (!response.ok || !payload.choices?.[0]?.message?.content) throw new Error(payload.error?.message || 'JSON 修复模型未返回内容。');
  return jsonFromModel(payload.choices[0].message.content);
}

function normalize(raw: unknown, index: number): CanvasElement | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>, kind = String(item.kind) as ElementKind;
  if (!kinds.has(kind)) return null;
  const rows = number(item.rows, 2, 1, 20), columns = number(item.columns, 2, 1, 20);
  const reconstructionClass = ['decorative_fixed', 'decorative_movable'].includes(String(item.reconstructionClass)) ? item.reconstructionClass as CanvasElement['reconstructionClass'] : undefined;
  return { id: `ai-${Date.now().toString(36)}-${index}`, kind, name: String(item.name || `${kind} ${index + 1}`).slice(0, 120), x: number(item.x, 80, 0, 1598), y: number(item.y, 80, 0, 898), w: number(item.w, 220, 2, 1600), h: number(item.h, kind === 'line' || kind === 'connector' ? 2 : 80, 2, 900), text: typeof item.text === 'string' ? item.text.slice(0, 6000) : '', sourceText: typeof item.text === 'string' ? item.text.slice(0, 6000) : '', sourceElement: false, ocrIndex: typeof item.ocrIndex === 'number' && Number.isInteger(item.ocrIndex) ? item.ocrIndex : undefined, role: ['decoration', 'container', 'label', 'data', 'body', 'icon', 'photo', 'brand', 'chart', 'flow'].includes(String(item.role)) ? item.role as CanvasElement['role'] : undefined, reconstructionClass, semanticImpact: typeof item.semanticImpact === 'boolean' ? item.semanticImpact : undefined, containsOcr: Array.isArray(item.containsOcr) ? [...new Set(item.containsOcr.map(Number).filter((value) => Number.isInteger(value) && value >= 0 && value < 180))].slice(0, 80) : undefined, fontSize: number(item.fontSize, 24, 6, 160), fontWeight: number(item.fontWeight, 400, 100, 900), color: color(item.color, '#202020'), fill: color(item.fill, '#FFFFFF'), stroke: color(item.stroke, '#C90C10'), strokeWidth: number(item.strokeWidth, 1, 0, 24), align: item.align === 'center' || item.align === 'right' ? item.align : 'left', radius: number(item.radius, 0, 0, 100), opacity: typeof item.opacity === 'number' ? Math.max(0, Math.min(1, item.opacity)) : 1, rows: kind === 'table' ? rows : undefined, columns: kind === 'table' ? columns : undefined, cells: Array.isArray(item.cells) ? item.cells.slice(0, rows).map((row) => Array.isArray(row) ? row.slice(0, columns).map((cell) => String(cell).slice(0, 300)) : []) : undefined };
}

type OcrLine = { ocrIndex?: number; text: string; x: number; y: number; w: number; h: number; fontSize?: number; fontWeight?: number; color?: string; align?: 'left' | 'center' | 'right'; role?: CanvasElement['role'] };
type ImageSize = { width: number; height: number };

function normalizedText(value: string) { return value.replace(/[\s，。、,.：:；;（）()“”"'`·]/g, '').toLowerCase(); }

function hasMojibake(value: string) {
  return !value.trim() || hasTextCorruption(value);
}

function assertTextIntegrity(elements: CanvasElement[], stage: string) {
  const damaged = elements.filter((item) => item.kind === 'text' && item.text && hasTextCorruption(item.text));
  if (damaged.length) throw new Error(`${stage}检测到 ${damaged.length} 个乱码文本框，系统已停止生成。请重新识别；损坏文字不会进入画布或导出。`);
}

const pythonUtf8Env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };

async function normalizeWorkbenchSource(requestImage: string) {
  const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const input = join(tmpdir(), `ppt-source-input-${token}.img`), output = join(tmpdir(), `ppt-source-reference-${token}.png`), report = join(tmpdir(), `ppt-source-reference-${token}.json`);
  try {
    await writeFile(input, Buffer.from(requestImage.slice(requestImage.indexOf(',') + 1), 'base64'));
    const script = join(process.cwd(), 'tools', 'vision', 'normalize_workbench_source.py');
    const { stdout } = await execFile(process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python', [script, '--source', input, '--output', output, '--report', report], { timeout: 30_000, maxBuffer: 2_000_000, windowsHide: true, env: pythonUtf8Env });
    const bytes = await readFile(output);
    return { image: `data:image/png;base64,${bytes.toString('base64')}`, normalization: JSON.parse(stdout) as unknown };
  } finally {
    await Promise.all([input, output, report].map((path) => unlink(path).catch(() => undefined)));
  }
}

function applyTextMeasurement(item: CanvasElement, raw: Partial<CanvasElement>) {
  // Python is allowed to measure appearance and geometry only.  OCR text,
  // object identity and names remain owned by the original UTF-16 JS object.
  const patch: Partial<CanvasElement> = {};
  for (const key of ['x', 'y', 'w', 'h', 'fontSize', 'fontWeight', 'color', 'align'] as const) {
    const value = raw[key];
    if (value !== undefined) Object.assign(patch, { [key]: value });
  }
  return { ...item, ...patch, id: item.id, name: item.name, text: item.text, sourceText: item.sourceText };
}

function imageSizeFromDataUrl(image: string): ImageSize | null {
  try {
    const comma = image.indexOf(','), bytes = Buffer.from(image.slice(comma + 1), 'base64');
    if (image.startsWith('data:image/png') && bytes.length >= 24 && bytes.toString('ascii', 1, 4) === 'PNG') {
      return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
    }
    if (image.startsWith('data:image/jpeg') || image.startsWith('data:image/jpg')) {
      let offset = 2;
      while (offset + 9 < bytes.length) {
        if (bytes[offset] !== 0xff) { offset += 1; continue; }
        const marker = bytes[offset + 1], length = bytes.readUInt16BE(offset + 2);
        if (marker >= 0xc0 && marker <= 0xc3) return { width: bytes.readUInt16BE(offset + 7), height: bytes.readUInt16BE(offset + 5) };
        offset += Math.max(2, length + 2);
      }
    }
  } catch { return null; }
  return null;
}

function overlapRatio(a: CanvasElement, b: CanvasElement) {
  const left = Math.max(a.x, b.x), top = Math.max(a.y, b.y), right = Math.min(a.x + a.w, b.x + b.w), bottom = Math.min(a.y + a.h, b.y + b.h);
  return right <= left || bottom <= top ? 0 : ((right - left) * (bottom - top)) / Math.max(1, Math.min(a.w * a.h, b.w * b.h));
}

/** Deterministic safety pass: clamp geometry and remove only provable duplicate text fragments. */
function sanitizeInventory(elements: CanvasElement[]) {
  const bounded = elements.map((item) => {
    const x = Math.max(0, Math.min(1598, item.x)), y = Math.max(0, Math.min(898, item.y));
    return { ...item, x, y, w: Math.max(2, Math.min(item.w, 1600 - x)), h: Math.max(2, Math.min(item.h, 900 - y)) };
  });
  const result: CanvasElement[] = [];
  for (const item of bounded) {
    if (item.kind !== 'text' || !item.text?.trim()) { result.push(item); continue; }
    const current = normalizedText(item.text);
    const duplicateIndex = result.findIndex((prior) => {
      if (prior.kind !== 'text' || overlapRatio(prior, item) <= .58) return false;
      const previous = normalizedText(prior.text ?? '');
      return Boolean(previous && current && (previous === current || previous.includes(current) || current.includes(previous)));
    });
    if (duplicateIndex < 0) { result.push(item); continue; }
    const prior = result[duplicateIndex], previous = normalizedText(prior.text ?? '');
    if (current.length > previous.length) result[duplicateIndex] = item;
  }
  // Different labels in the same card often receive boxes that include their
  // shared whitespace. Tighten only the facing edges; keep both texts and their
  // original outer bounds. Truly coincident boxes remain for the quality gate.
  for (let pass = 0; pass < 3; pass += 1) {
    let changed = false;
    for (let i = 0; i < result.length; i += 1) for (let j = i + 1; j < result.length; j += 1) {
      const a = result[i], b = result[j];
      if (a.kind !== 'text' || b.kind !== 'text' || overlapRatio(a, b) <= .58) continue;
      const acx = a.x + a.w / 2, bcx = b.x + b.w / 2, acy = a.y + a.h / 2, bcy = b.y + b.h / 2;
      const dx = bcx - acx, dy = bcy - acy;
      if (Math.abs(dx) < 2 && Math.abs(dy) < 2) continue;
      if (Math.abs(dx) >= Math.abs(dy)) {
        const leftIndex = dx > 0 ? i : j, rightIndex = dx > 0 ? j : i;
        const left = result[leftIndex], right = result[rightIndex], boundary = Math.round((left.x + left.w / 2 + right.x + right.w / 2) / 2);
        const rightEdge = right.x + right.w;
        result[leftIndex] = { ...left, w: Math.max(2, boundary - 2 - left.x) };
        result[rightIndex] = { ...right, x: Math.max(right.x, boundary + 2), w: Math.max(2, rightEdge - Math.max(right.x, boundary + 2)) };
      } else {
        const topIndex = dy > 0 ? i : j, bottomIndex = dy > 0 ? j : i;
        const top = result[topIndex], bottom = result[bottomIndex], boundary = Math.round((top.y + top.h / 2 + bottom.y + bottom.h / 2) / 2);
        const bottomEdge = bottom.y + bottom.h;
        result[topIndex] = { ...top, h: Math.max(2, boundary - 2 - top.y) };
        result[bottomIndex] = { ...bottom, y: Math.max(bottom.y, boundary + 2), h: Math.max(2, bottomEdge - Math.max(bottom.y, boundary + 2)) };
      }
      changed = true;
    }
    if (!changed) break;
  }
  return result;
}

function enrichV3Inventory(elements: CanvasElement[]) {
  return elements.map((element, index): CanvasElement => {
    const reconstructionClass = element.kind === 'text'
      ? 'ocr_text' as const
      : element.role === 'decoration' && element.reconstructionClass === 'decorative_fixed' && element.semanticImpact === false
        ? 'decorative_fixed' as const
      : element.role === 'decoration'
        ? 'decorative_movable' as const
        : ['rectangle', 'ellipse', 'arrow', 'line', 'connector', 'table', 'freeform'].includes(element.kind)
          ? 'native_editable' as const
          : element.role === 'brand'
            ? 'exact_brand_asset' as const
            : 'library_png' as const;
    const assetKind = reconstructionClass === 'native_editable' ? 'native_editable' as const
      : reconstructionClass === 'exact_brand_asset' || reconstructionClass === 'library_png' || reconstructionClass === 'decorative_movable' ? 'png' as const
        : undefined;
    return {
      ...element,
      reconstructionClass,
      assetKind,
      sourceBBox: [element.x, element.y, element.w, element.h],
      zIndex: index,
      placementConfidence: element.kind === 'text' ? .95 : .65,
      parentId: null,
      semanticImpact: reconstructionClass !== 'decorative_movable' && reconstructionClass !== 'decorative_fixed',
      classificationReason: element.kind === 'text' ? 'OCR-authoritative text route' : reconstructionClass === 'native_editable' ? 'PowerPoint-native geometry' : 'Requires gallery retrieval or Qwen asset reconstruction',
      qaStatus: 'pending',
    };
  });
}

function parseOcrLines(raw: unknown, imageSize: ImageSize): OcrLine[] {
  const data = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const ocrResult = data.ocr_result && typeof data.ocr_result === 'object' ? data.ocr_result as Record<string, unknown> : undefined;
  const candidates = Array.isArray(data.lines) ? data.lines : Array.isArray(data.texts) ? data.texts : Array.isArray(data.words_info) ? data.words_info : Array.isArray(data.ocr_result) ? data.ocr_result : Array.isArray(ocrResult?.words_info) ? ocrResult.words_info : [];
  return candidates.flatMap((value) => {
    if (!value || typeof value !== 'object') return [];
    const item = value as Record<string, unknown>, text = String(item.text ?? item.words ?? item.content ?? '').trim();
    if (!text || hasMojibake(text)) return [];
    const location = Array.isArray(item.location) ? item.location.map(Number) : [];
    const rotateRect = Array.isArray(item.rotate_rect) ? item.rotate_rect.map(Number) : [];
    let rawX = -1, rawY = -1, rawW = -1, rawH = -1;
    if (location.length >= 8 && location.every(Number.isFinite)) {
      const xs = [location[0], location[2], location[4], location[6]], ys = [location[1], location[3], location[5], location[7]];
      rawX = Math.min(...xs); rawY = Math.min(...ys); rawW = Math.max(...xs) - rawX; rawH = Math.max(...ys) - rawY;
    } else if (rotateRect.length >= 4 && rotateRect.slice(0, 4).every(Number.isFinite)) {
      rawX = rotateRect[0] - rotateRect[2] / 2; rawY = rotateRect[1] - rotateRect[3] / 2; rawW = rotateRect[2]; rawH = rotateRect[3];
    } else {
      const box = item.box && typeof item.box === 'object' ? item.box as Record<string, unknown> : item.location && typeof item.location === 'object' ? item.location as Record<string, unknown> : item;
      rawX = Number(box.x ?? box.left ?? box.x1); rawY = Number(box.y ?? box.top ?? box.y1); rawW = Number(box.w ?? box.width); rawH = Number(box.h ?? box.height);
      if ((!Number.isFinite(rawW) || rawW <= 0) && Number.isFinite(Number(box.x2))) rawW = Number(box.x2) - rawX;
      if ((!Number.isFinite(rawH) || rawH <= 0) && Number.isFinite(Number(box.y3 ?? box.y2))) rawH = Number(box.y3 ?? box.y2) - rawY;
    }
    if (![rawX, rawY, rawW, rawH].every(Number.isFinite) || rawW < 2 || rawH < 2) return [];
    const sx = 1600 / Math.max(1, imageSize.width), sy = 900 / Math.max(1, imageSize.height);
    const x = Math.max(0, Math.round(rawX * sx)), y = Math.max(0, Math.round(rawY * sy));
    const w = Math.min(1600 - x, Math.max(2, Math.round(rawW * sx))), h = Math.min(900 - y, Math.max(2, Math.round(rawH * sy)));
    const align: OcrLine['align'] = item.align === 'center' || item.align === 'right' ? item.align : 'left';
    return [{ text: text.slice(0, 6000), x, y, w, h, fontSize: number(item.fontSize, Math.max(10, Math.round(h * .9)), 6, 160), fontWeight: number(item.fontWeight, 400, 100, 900), color: color(item.color, '#202020'), align }];
  }).slice(0, 180).map((line, ocrIndex) => ({ ...line, ocrIndex }));
}

async function extractPreciseOcr(image: string) {
  const ocrModel = dashScopeOcrModel(), key = dashScopeOcrKey();
  if (!ocrModel || !key) throw new Error('专用 OCR 模型或密钥未配置。');
  const imageSize = imageSizeFromDataUrl(image);
  if (!imageSize) throw new Error('无法读取上传图片尺寸，OCR 坐标不能安全换算。');
  const endpoint = `${dashScopeOcrNativeBaseUrl()}/services/aigc/multimodal-generation/generation`;
  const response = await fetchWithRetry(endpoint, {
    method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: ocrModel, input: { messages: [{ role: 'user', content: [{ image, min_pixels: 3072, max_pixels: 8_388_608, enable_rotate: false }] }] }, parameters: { ocr_options: { task: 'advanced_recognition' } } }),
  }, 3, 'ocr');
  const payload = await response.json() as { output?: { choices?: Array<{ message?: { content?: unknown } }> }; message?: string; error?: { message?: string } };
  if (!response.ok) throw new Error(payload.error?.message || payload.message || `OCR 高精识别请求失败（${response.status}）。`);
  const rawContent = payload.output?.choices?.[0]?.message?.content;
  const content = Array.isArray(rawContent) ? rawContent : rawContent && typeof rawContent === 'object' ? [rawContent] : [];
  const ocrContent = content.find((item) => item && typeof item === 'object' && 'ocr_result' in item) as { ocr_result?: unknown } | undefined;
  const lines = parseOcrLines(ocrContent?.ocr_result ?? ocrContent, imageSize);
  if (!lines.length) throw new Error('OCR 高精识别未返回任何带坐标的文字。请检查 OCR 工作空间端点与模型权限。');
  return lines;
}

function mergeOcrText(vision: CanvasElement[], lines: OcrLine[]) {
  const visualText = vision.filter((item) => item.kind === 'text');
  return lines.map((line, index): CanvasElement => {
    const query = normalizedText(line.text);
    const indexed = visualText.find((candidate) => candidate.ocrIndex === line.ocrIndex);
    const closest = indexed ?? visualText.reduce<CanvasElement | undefined>((best, candidate) => {
      const text = normalizedText(candidate.text ?? ''), score = (text === query ? 100000 : (text.includes(query) || query.includes(text)) ? 50000 : 0) - Math.abs(candidate.x - line.x) - Math.abs(candidate.y - line.y);
      const priorText = normalizedText(best?.text ?? ''), prior = best ? (priorText === query ? 100000 : (priorText.includes(query) || query.includes(priorText)) ? 50000 : 0) - Math.abs(best.x - line.x) - Math.abs(best.y - line.y) : -Infinity;
      return score > prior ? candidate : best;
    }, undefined);
    return { id: `ocr-${Date.now().toString(36)}-${index}`, kind: 'text', name: closest?.name || `OCR 文本 ${index + 1}`, x: line.x, y: line.y, w: line.w, h: line.h, text: line.text, sourceText: line.text, sourceElement: false, ocrIndex: line.ocrIndex, role: closest?.role ?? line.role ?? 'body', fontSize: closest?.fontSize ?? line.fontSize ?? Math.max(10, Math.round(line.h * .82)), fontWeight: closest?.fontWeight ?? line.fontWeight ?? 400, color: closest?.color ?? line.color ?? '#202020', align: closest?.align ?? line.align ?? 'left', opacity: 1 };
  });
}

function nearestVisualText(line: OcrLine, visualText: CanvasElement[]) {
  const cx = line.x + line.w / 2, cy = line.y + line.h / 2;
  return visualText.reduce<{ item?: CanvasElement; score: number }>((best, item) => {
    const ix = item.x + item.w / 2, iy = item.y + item.h / 2;
    const score = Math.abs(ix - cx) + Math.abs(iy - cy) * 1.35;
    return score < best.score ? { item, score } : best;
  }, { score: Number.POSITIVE_INFINITY }).item;
}

function styleOcrTextFromVision(lines: OcrLine[], vision: CanvasElement[]) {
  const visualText = vision.filter((item) => item.kind === 'text' && (item.ocrIndex !== undefined || !hasMojibake(item.text ?? '')));
  return lines.map((line) => {
    const nearby = visualText.find((item) => item.ocrIndex === line.ocrIndex) ?? nearestVisualText(line, visualText);
    const closeEnough = nearby && Math.abs(nearby.y + nearby.h / 2 - (line.y + line.h / 2)) <= Math.max(48, line.h * 2.5);
    return {
      ...line,
      fontSize: closeEnough ? nearby.fontSize : line.fontSize,
      fontWeight: closeEnough ? nearby.fontWeight : line.fontWeight,
      color: closeEnough ? nearby.color : line.color,
      align: closeEnough ? nearby.align : line.align,
      role: closeEnough ? nearby.role : 'body',
    };
  });
}

function calibrateSemanticGeometry(elements: CanvasElement[], textElements: CanvasElement[]) {
  const byOcr = new Map(textElements.filter((item) => item.ocrIndex !== undefined).map((item) => [item.ocrIndex as number, item]));
  return elements.map((item) => {
    const owned = (item.containsOcr ?? []).map((index) => byOcr.get(index)).filter((text): text is CanvasElement => Boolean(text));
    if (!owned.length || (item.kind !== 'rectangle' && item.kind !== 'ellipse' && item.kind !== 'table' && item.kind !== 'freeform')) return item;
    const left = Math.min(...owned.map((text) => text.x));
    const top = Math.min(...owned.map((text) => text.y));
    const right = Math.max(...owned.map((text) => text.x + text.w));
    const bottom = Math.max(...owned.map((text) => text.y + text.h));
    const name = item.name.toLowerCase();
    if (/^card-\d\d$/.test(name)) {
      const x = Math.max(0, left - 18), y = Math.max(0, top - 12);
      return { ...item, x, y, w: Math.min(1600 - x, Math.max(item.w, right - x + 38)), h: Math.min(900 - y, Math.max(90, bottom - y + 28)) };
    }
    if (name.includes('header') && right - left > 700) {
      const x = Math.max(0, left - 55), y = Math.max(0, top - 12);
      return { ...item, x, y, w: Math.min(1600 - x, right - x + 45), h: Math.min(900 - y, bottom - y + 34) };
    }
    if (name.includes('footer')) {
      const x = Math.max(0, left - 135), y = Math.max(0, top - 10);
      return { ...item, x, y, w: Math.min(1600 - x, right - x + 42), h: Math.min(900 - y, bottom - y + 24) };
    }
    // Generic containment correction: preserve the model's styling and size
    // when possible, but never leave an owned OCR center outside the host.
    const padX = Math.max(8, Math.min(28, Math.round((bottom - top) * .18)));
    const padY = Math.max(6, Math.min(20, Math.round((bottom - top) * .12)));
    const x = Math.min(item.x, left - padX), y = Math.min(item.y, top - padY);
    const correctedX = Math.max(0, x), correctedY = Math.max(0, y);
    return { ...item, x: correctedX, y: correctedY, w: Math.min(1600 - correctedX, Math.max(item.w, right + padX - correctedX)), h: Math.min(900 - correctedY, Math.max(item.h, bottom + padY - correctedY)) };
  });
}

function keepForegroundSemanticObjects(elements: CanvasElement[]) {
  return elements.filter((item) => {
    const name = item.name.toLowerCase();
    const nearWhite = ['#FFFFFF', '#FAFAFA', '#F8F8F8', '#F5F5F5'].includes(item.fill ?? '');
    const whiteFill = nearWhite && ['#FFFFFF', '#FAFAFA', '#F8F8F8', '#F5F5F5'].includes(item.stroke ?? '') && (item.strokeWidth ?? 0) === 0;
    if (whiteFill && (name.includes('title-bar') || name.includes('subtitle-bar') || name.includes('footer-note') || name.includes('footer-right-label'))) return false;
    if (nearWhite && item.role === 'container' && item.y < 210 && (item.containsOcr?.length ?? 0) <= 2) return false;
    if (item.fill === '#FFFFFF' && /(header-banner|header-title|header-subtitle|title-bar|title-bg|subtitle-bg|footer-note|footer-right-label)/i.test(name)) return false;
    if (/background|page-deco|city|skyline|sun(?:-|_)icon|corner|abstract-wave|ribbon|birds?|wave-deco/i.test(name)) return false;
    if (item.role !== 'decoration' || item.containsOcr?.length) return true;
    if (/divider/i.test(name) && (item.w > 600 || item.h > 150)) return false;
    if (item.w >= 1200 && item.h >= 50 && (item.y < 220 || item.y + item.h > 835)) return false;
    return true;
  });
}

function normalizeSemanticAssetKinds(elements: CanvasElement[]) {
  return elements.map((item) => {
    const name = item.name.toLowerCase();
    if ((item.kind === 'rectangle' || item.kind === 'ellipse' || item.kind === 'freeform') && /(?:^|[-_])(star|logo|pictogram|illustration)(?:$|[-_])/i.test(name)) {
      return { ...item, kind: 'icon' as const, role: 'icon' as const, containsOcr: item.containsOcr ?? [] };
    }
    return item;
  });
}

function normalizeSemanticOwnership(elements: CanvasElement[], textElements: CanvasElement[]) {
  const editableOcr = new Set(textElements.flatMap((item) => item.ocrIndex === undefined ? [] : [item.ocrIndex]));
  return elements.map((item) => item.containsOcr ? { ...item, containsOcr: item.containsOcr.filter((index) => editableOcr.has(index)) } : item);
}

function reconstructRepeatedCards(elements: CanvasElement[], textElements: CanvasElement[]) {
  const byOcr = new Map(textElements.filter((item) => item.ocrIndex !== undefined).map((item) => [item.ocrIndex as number, item]));
  const hosts = elements.filter((item) => item.role === 'container' && /^card-\d\d(?:-host)?$/i.test(item.name) && (item.containsOcr?.length ?? 0) >= 2);
  if (hosts.length < 2) return elements;
  const anchors = hosts.map((host) => {
    const owned = (host.containsOcr ?? []).map((index) => byOcr.get(index)).filter((item): item is CanvasElement => Boolean(item));
    const numberText = owned.find((item) => /^\d{1,2}$/.test((item.text ?? '').trim())) ?? owned[0];
    return { host, owned, numberText };
  }).filter((item) => item.numberText);
  if (anchors.length < 2) return elements;
  const rowTops = [...new Set(anchors.map(({ numberText }) => numberText.y))].sort((a, b) => a - b).reduce<number[]>((rows, value) => {
    const matched = rows.findIndex((row) => Math.abs(row - value) <= 28);
    if (matched < 0) rows.push(value); else rows[matched] = Math.round((rows[matched] + value) / 2);
    return rows;
  }, []);
  const boxes = new Map<string, { x: number; y: number; w: number; h: number }>();
  const additions: CanvasElement[] = [];
  for (const { host, owned, numberText } of anchors) {
    const rowTop = rowTops.reduce((best, value) => Math.abs(value - numberText.y) < Math.abs(best - numberText.y) ? value : best, rowTops[0]);
    const rowAnchors = anchors.filter((candidate) => Math.abs(candidate.numberText.y - rowTop) <= 28);
    // A vertical step list may also be named card-01/card-02/card-03 by the
    // model.  Repeated-card snapping only applies to an actual multi-column
    // row; do not invent horizontal cards for a vertical timeline.
    if (rowAnchors.length < 2) continue;
    const x = Math.max(0, numberText.x - 16);
    const nextX = rowAnchors.map((candidate) => candidate.numberText.x - 16).filter((value) => value > x + 80).sort((a, b) => a - b)[0];
    const previousWidths = rowAnchors.map((candidate) => candidate.host.w).filter((value) => value >= 260 && value <= 650);
    const inferredWidth = nextX ? nextX - x - 34 : previousWidths.length ? Math.round(previousWidths.reduce((sum, value) => sum + value, 0) / previousWidths.length) : 460;
    const rowOwned = rowAnchors.flatMap((candidate) => candidate.owned);
    const bottom = Math.max(...rowOwned.map((item) => item.y + item.h));
    boxes.set(host.id, { x, y: Math.max(0, rowTop - 12), w: Math.min(1600 - x, Math.max(320, inferredWidth)), h: Math.max(110, bottom - (rowTop - 12) + 14) });
    const title = owned.find((item) => item.id !== numberText.id && !/^\d{1,2}$/.test((item.text ?? '').trim()));
    const cardNumber = host.name.match(/card-(\d\d)/i)?.[1];
    const hasExplicitTitleBar = cardNumber && elements.some((item) => new RegExp(`^card-${cardNumber}-title-bar$`, 'i').test(item.name));
    if (title && !hasExplicitTitleBar) {
      const box = boxes.get(host.id)!;
      additions.push({ id: `${host.id}-title-underline`, kind: 'line', name: `${host.name}-title-underline`, role: 'decoration', x: box.x + 112, y: title.y + title.h + 11, w: Math.max(120, box.w - 142), h: 2, stroke: title.color || '#C60000', strokeWidth: 2, opacity: 1, sourceElement: false });
    }
  }
  const rebuilt = elements.map((item) => {
    const host = anchors.find((candidate) => candidate.host.id === item.id);
    if (host && boxes.has(item.id)) {
      const box = boxes.get(item.id)!;
      return { ...item, ...box, fill: '#FFFFFF', stroke: item.stroke === '#FFFFFF' ? '#E8C9B5' : item.stroke, strokeWidth: Math.max(1, item.strokeWidth ?? 1), radius: Math.max(10, item.radius ?? 0) };
    }
    const numberMatch = item.name.match(/^card-(\d\d)-number-tab$/i);
    if (numberMatch) {
      const hostItem = anchors.find((candidate) => candidate.host.name.toLowerCase().includes(`card-${numberMatch[1]}`));
      const box = hostItem ? boxes.get(hostItem.host.id) : undefined;
      if (!box) return item;
      const goldId = `${item.id}-accent`;
      additions.push({ id: goldId, kind: 'rectangle', name: `${item.name}-gold-accent`, role: 'decoration', x: box.x + 100, y: box.y, w: 18, h: 68, fill: '#F5B746', stroke: '#F5B746', strokeWidth: 0, radius: 0, opacity: 1, sourceElement: false });
      return { ...item, x: box.x, y: box.y, w: 100, h: 68, radius: Math.max(8, item.radius ?? 0) };
    }
    const titleMatch = item.name.match(/^card-(\d\d)-title-bar$/i);
    if (titleMatch) {
      const hostItem = anchors.find((candidate) => candidate.host.name.toLowerCase().includes(`card-${titleMatch[1]}`));
      const box = hostItem ? boxes.get(hostItem.host.id) : undefined;
      const title = hostItem?.owned.find((text) => !/^\d{1,2}$/.test((text.text ?? '').trim()));
      if (!box || !title) return item;
      return { ...item, kind: 'line' as const, name: `${item.name}-underline`, role: 'decoration' as const, x: box.x + 112, y: title.y + title.h + 9, w: Math.max(120, box.w - 142), h: 2, fill: undefined, stroke: title.color || item.stroke || '#C60000', strokeWidth: 2, radius: 0 };
    }
    return item;
  });
  return [...rebuilt, ...additions];
}

function reconstructNumberedCardGrid(elements: CanvasElement[], textElements: CanvasElement[]) {
  const numbered = textElements.filter((item) => /^\d{2}$/.test((item.text ?? '').trim()) && item.y > 250 && item.y < 800);
  if (numbered.length < 4) return elements;
  const rows = numbered.slice().sort((a, b) => a.y - b.y).reduce<CanvasElement[][]>((groups, item) => {
    const group = groups.find((candidate) => Math.abs(candidate[0].y - item.y) <= 32);
    if (group) group.push(item); else groups.push([item]);
    return groups;
  }, []).filter((row) => row.length >= 2);
  if (!rows.length) return elements;
  const gridNumbers = rows.flat();
  if (gridNumbers.length < 4) return elements;
  const gridNumberIds = new Set(gridNumbers.map((item) => item.id));
  const originalCards = elements.filter((item) => /^card-\d\d/i.test(item.name));
  const firstCard = originalCards.find((item) => item.role === 'container');
  const red = originalCards.find((item) => item.fill && item.fill !== '#FFFFFF')?.fill || gridNumbers[0].color || '#C60000';
  const border = firstCard?.stroke && firstCard.stroke !== '#FFFFFF' ? firstCard.stroke : '#E7C9B7';
  const icons = elements.filter((item) => item.kind === 'icon' || item.kind === 'image');
  const nonCardElements = elements.filter((item) => !/^card-\d\d/i.test(item.name) && item.kind !== 'icon' && item.kind !== 'image');
  const unrelatedAssets = icons.filter((item) => !/(card|icon|flag|book|group|people|house|shield|clipboard|checklist)/i.test(item.name));
  const rebuilt: CanvasElement[] = [];
  const assignedAssets = new Set<string>();
  for (const row of rows) {
    row.sort((a, b) => a.x - b.x);
    const rowTop = Math.round(row.reduce((sum, item) => sum + item.y, 0) / row.length);
    const nextRowTop = rows.map((candidate) => candidate[0].y).filter((value) => value > rowTop + 40).sort((a, b) => a - b)[0];
    for (let column = 0; column < row.length; column += 1) {
      const numberText = row[column];
      const number = (numberText.text ?? '').trim();
      const x = Math.max(0, numberText.x - 16);
      const nextX = row[column + 1] ? row[column + 1].x - 16 : undefined;
      const priorWidths = row.slice(0, -1).map((item, index) => row[index + 1].x - item.x - 34).filter((value) => value >= 320 && value <= 650);
      const width = nextX ? nextX - x - 34 : priorWidths.length ? Math.round(priorWidths.reduce((sum, value) => sum + value, 0) / priorWidths.length) : 455;
      const titleCandidates = textElements.filter((item) => !gridNumberIds.has(item.id) && item.y >= rowTop - 8 && item.y <= rowTop + 25 && item.x > numberText.x + 60 && item.x < x + width);
      const title = titleCandidates.sort((a, b) => a.x - b.x)[0];
      const body = textElements.filter((item) => !gridNumberIds.has(item.id) && item.y > (title?.y ?? rowTop) + 30 && item.y < (nextRowTop ? nextRowTop - 20 : Math.min(780, rowTop + 190)) && item.x > x + 105 && item.x < x + width - 10);
      if (!title || !body.length) continue;
      const bottom = Math.max(...body.map((item) => item.y + item.h));
      const y = rowTop - 12;
      const h = Math.max(118, bottom - y + 16);
      const containsOcr = [numberText, title, ...body].flatMap((item) => item.ocrIndex === undefined ? [] : [item.ocrIndex]);
      const prefix = `grid-card-${number}`;
      rebuilt.push(
        { id: `${prefix}-host`, kind: 'rectangle', name: `card-${number}-host`, role: 'container', containsOcr, x, y, w: width, h, fill: '#FFFFFF', stroke: border, strokeWidth: 1, radius: 12, opacity: 1, sourceElement: false },
        { id: `${prefix}-tab`, kind: 'rectangle', name: `card-${number}-number-tab`, role: 'decoration', containsOcr: numberText.ocrIndex === undefined ? [] : [numberText.ocrIndex], x, y, w: 100, h: 68, fill: red, stroke: red, strokeWidth: 0, radius: 10, opacity: 1, sourceElement: false },
        { id: `${prefix}-accent`, kind: 'rectangle', name: `card-${number}-gold-accent`, role: 'decoration', x: x + 100, y, w: 18, h: 68, fill: '#F5B746', stroke: '#F5B746', strokeWidth: 0, radius: 0, opacity: 1, sourceElement: false },
        { id: `${prefix}-underline`, kind: 'line', name: `card-${number}-title-underline`, role: 'decoration', x: x + 112, y: title.y + title.h + 10, w: Math.max(120, width - 142), h: 2, stroke: title.color || red, strokeWidth: 2, opacity: 1, sourceElement: false },
      );
      const nameMatch = new RegExp(`(?:card|icon)[-_]?${number}|${number}[-_](?:icon|asset)`, 'i');
      let asset = icons.find((item) => !assignedAssets.has(item.id) && nameMatch.test(item.name));
      if (!asset) {
        const expectedX = x + 60, expectedY = title.y + title.h + 58;
        asset = icons.filter((item) => !assignedAssets.has(item.id) && !unrelatedAssets.some((other) => other.id === item.id)).sort((a, b) => (Math.abs(a.x - expectedX) + Math.abs(a.y - expectedY)) - (Math.abs(b.x - expectedX) + Math.abs(b.y - expectedY)))[0];
      }
      if (asset) {
        assignedAssets.add(asset.id);
        const bodyLeft = Math.min(...body.map((item) => item.x));
        rebuilt.push({ ...asset, name: `card-${number}-icon`, x: x + 26, y: Math.min(...body.map((item) => item.y)) + 4, w: Math.max(56, Math.min(112, bodyLeft - x - 52)), h: Math.max(58, Math.min(h - 86, 105)) });
      }
    }
  }
  if (rebuilt.filter((item) => item.role === 'container').length < 4) return elements;
  return [...nonCardElements, ...unrelatedAssets, ...rebuilt];
}

function reconstructWideLabelCards(elements: CanvasElement[], textElements: CanvasElement[]) {
  const byOcr = new Map(textElements.filter((item) => item.ocrIndex !== undefined).map((item) => [item.ocrIndex as number, item]));
  const additions: CanvasElement[] = [];
  const rebuilt = elements.map((item) => {
    if (item.role !== 'container' || item.w < 900 || (item.containsOcr?.length ?? 0) < 2 || (item.containsOcr?.length ?? 0) > 5 || item.h > 180) return item;
    const name = item.name.toLowerCase();
    if (!/(section|summary|overview|judgment|header-card|core)/i.test(name) || /(title|footer)/i.test(name)) return item;
    const owned = (item.containsOcr ?? []).map((index) => byOcr.get(index)).filter((text): text is CanvasElement => Boolean(text));
    if (owned.length < 2) return item;
    const leftText = owned.slice().sort((a, b) => a.x - b.x)[0];
    if (leftText.color !== '#FFFFFF') return item;
    const existingLabelHost = elements.some((candidate) => candidate.id !== item.id && candidate.containsOcr?.includes(leftText.ocrIndex as number) && candidate.fill && !['#FFFFFF', '#FAFAFA', '#F8F8F8', '#F5F5F5'].includes(candidate.fill));
    if (existingLabelHost) return item;
    const left = Math.min(...owned.map((text) => text.x));
    const rightGroup = owned.filter((text) => text.x > left + 180).sort((a, b) => a.x - b.x);
    if (!rightGroup.length) return item;
    const rightStart = rightGroup[0].x;
    const x = Math.max(0, Math.min(item.x, left - 45));
    const top = Math.min(...owned.map((text) => text.y));
    const bottom = Math.max(...owned.map((text) => text.y + text.h));
    const y = Math.max(0, top - 13);
    const w = Math.min(1600 - x, Math.max(item.w, Math.max(...owned.map((text) => text.x + text.w)) - x + 45));
    const h = bottom - y + 12;
    additions.push({ id: `${item.id}-label-block`, kind: 'rectangle', name: `${item.name}-label-block`, role: 'decoration', x: x + 8, y: y + 8, w: Math.max(180, rightStart - x - 48), h: Math.max(40, h - 16), fill: item.fill && item.fill !== '#FFFFFF' ? item.fill : '#C60000', stroke: item.fill && item.fill !== '#FFFFFF' ? item.fill : '#C60000', strokeWidth: 0, radius: Math.max(8, item.radius ?? 0), opacity: 1, sourceElement: false } as CanvasElement);
    return { ...item, x, y, w, h, fill: '#FFFFFF', stroke: item.stroke === '#FFFFFF' ? '#C60000' : item.stroke, strokeWidth: Math.max(1, item.strokeWidth ?? 1), radius: Math.max(12, item.radius ?? 0) };
  });
  return [...rebuilt, ...additions];
}

function calibrateCardAssetLocations(elements: CanvasElement[], textElements: CanvasElement[]) {
  const byOcr = new Map(textElements.filter((item) => item.ocrIndex !== undefined).map((item) => [item.ocrIndex as number, item]));
  const hosts = elements.filter((item) => item.role === 'container' && /^card-\d\d(?:-host)?$/i.test(item.name));
  return elements.map((item) => {
    if (item.kind !== 'icon' && item.kind !== 'image') return item;
    const cardNumber = item.name.match(/card-(\d\d)/i)?.[1] ?? item.name.match(/(?:icon|asset)[-_]?(\d\d)/i)?.[1];
    if (!cardNumber) return item;
    const host = hosts.find((candidate) => candidate.name.toLowerCase().includes(`card-${cardNumber}`));
    if (!host) return item;
    const owned = (host.containsOcr ?? []).map((index) => byOcr.get(index)).filter((text): text is CanvasElement => Boolean(text));
    const numberText = owned.find((text) => /^\d{1,2}$/.test((text.text ?? '').trim()));
    const title = owned.filter((text) => text.id !== numberText?.id).sort((a, b) => a.y - b.y)[0];
    const body = owned.filter((text) => text.id !== numberText?.id && text.id !== title?.id && text.y > (title?.y ?? host.y) + 16);
    if (!body.length) return item;
    const bodyLeft = Math.min(...body.map((text) => text.x));
    const x = Math.max(host.x + 12, Math.min(bodyLeft - 150, host.x + Math.round(host.w * .08)));
    const y = Math.max((title?.y ?? host.y) + (title?.h ?? 25) + 18, Math.min(...body.map((text) => text.y)) + 2);
    const w = Math.max(52, Math.min(125, bodyLeft - x - 18));
    const h = Math.max(52, Math.min(host.y + host.h - y - 12, Math.max(72, Math.max(...body.map((text) => text.y + text.h)) - y)));
    return { ...item, x, y, w, h };
  });
}

function styleTextBySemanticHosts(textElements: CanvasElement[], structures: CanvasElement[]) {
  const hosts = structures.filter((item) => item.containsOcr?.length && item.fill && item.fill !== '#FFFFFF');
  return textElements.map((text) => {
    if (text.ocrIndex === undefined) return text;
    const host = hosts.filter((item) => item.containsOcr?.includes(text.ocrIndex as number)).sort((a, b) => a.w * a.h - b.w * b.h)[0];
    if (!host?.fill) return text;
    const raw = host.fill.replace('#', '');
    const red = parseInt(raw.slice(0, 2), 16), green = parseInt(raw.slice(2, 4), 16), blue = parseInt(raw.slice(4, 6), 16);
    const luminance = .2126 * red + .7152 * green + .0722 * blue;
    const saturation = Math.max(red, green, blue) - Math.min(red, green, blue);
    if (luminance < 175 && saturation > 35) return { ...text, color: '#FFFFFF', fontWeight: Math.max(600, text.fontWeight ?? 400) };
    return text;
  });
}

function isEmbeddedGraphicText(line: OcrLine, visualElements: CanvasElement[]) {
  const compact = line.text.replace(/\s/g, '');
  // Single Han glyphs inside report pictograms (for example 廉 in a shield)
  // are commonly part of the artwork.  Preserve them in the base rather than
  // punching a hole in the icon.  Ordinary numeric tabs remain editable.
  if (/^[\u3400-\u9FFF]$/.test(compact) && line.h >= 28 && line.w <= line.h * 1.45) return true;
  const lineArea = Math.max(1, line.w * line.h);
  return visualElements.some((item) => {
    if (item.kind !== 'icon' && item.kind !== 'image' && item.kind !== 'freeform') return false;
    const left = Math.max(line.x, item.x), top = Math.max(line.y, item.y);
    const right = Math.min(line.x + line.w, item.x + item.w), bottom = Math.min(line.y + line.h, item.y + item.h);
    const coverage = right > left && bottom > top ? ((right - left) * (bottom - top)) / lineArea : 0;
    return coverage >= .72;
  });
}

async function refineTextBoxes(image: string, elements: CanvasElement[]) {
  const text = elements.filter((item) => item.kind === 'text' && item.text?.trim());
  if (!text.length) return elements;
  const base64 = image.slice(image.indexOf(',') + 1);
  const temporaryImage = join(tmpdir(), `ppt-text-measure-${Date.now()}-${Math.random().toString(36).slice(2)}.img`);
  try {
    await writeFile(temporaryImage, Buffer.from(base64, 'base64'));
    const script = join(process.cwd(), 'tools', 'vision', 'refine_text_boxes.py');
    const { stdout } = await execFile('python', [script, `--image=${temporaryImage}`, `--elements-json=${JSON.stringify(text)}`], { timeout: 25_000, maxBuffer: 4_000_000, windowsHide: true, env: pythonUtf8Env });
    const refined = JSON.parse(stdout) as Array<Partial<CanvasElement>>;
    if (!Array.isArray(refined) || refined.length !== text.length) return elements;
    let index = 0;
    return elements.map((item) => item.kind !== 'text' ? item : applyTextMeasurement(item, refined[index++]));
  } catch {
    return elements;
  } finally {
    await unlink(temporaryImage).catch(() => undefined);
  }
}

async function styleOcrTextBoxes(image: string, elements: CanvasElement[]) {
  const text = elements.filter((item) => item.kind === 'text' && item.text?.trim());
  if (!text.length) return elements;
  const base64 = image.slice(image.indexOf(',') + 1);
  const temporaryImage = join(tmpdir(), `ppt-ocr-style-${Date.now()}-${Math.random().toString(36).slice(2)}.img`);
  try {
    await writeFile(temporaryImage, Buffer.from(base64, 'base64'));
    const script = join(process.cwd(), 'tools', 'vision', 'style_ocr_lines.py');
    const { stdout } = await execFile('python', [script, `--image=${temporaryImage}`, `--elements-json=${JSON.stringify(text)}`], { timeout: 25_000, maxBuffer: 4_000_000, windowsHide: true, env: pythonUtf8Env });
    const styled = JSON.parse(stdout) as Array<Partial<CanvasElement>>;
    if (!Array.isArray(styled) || styled.length !== text.length) return elements;
    let index = 0;
    return elements.map((item) => item.kind !== 'text' ? item : applyTextMeasurement(item, styled[index++]));
  } catch {
    return elements;
  } finally {
    await unlink(temporaryImage).catch(() => undefined);
  }
}

async function refineAssetBoxes(image: string, elements: CanvasElement[]) {
  const assets = elements.filter((item) => item.kind === 'icon' || item.kind === 'image');
  if (!assets.length) return elements;
  const base64 = image.slice(image.indexOf(',') + 1);
  const temporaryImage = join(tmpdir(), `ppt-asset-refine-${Date.now()}-${Math.random().toString(36).slice(2)}.img`);
  const temporaryElements = join(tmpdir(), `ppt-asset-refine-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
  try {
    await writeFile(temporaryImage, Buffer.from(base64, 'base64'));
    const script = join(process.cwd(), 'tools', 'vision', 'refine_asset_boxes.py');
    const compactAssets = assets.map(({ imageSrc: _imageSrc, ...item }) => item);
    await writeFile(temporaryElements, JSON.stringify(compactAssets), 'utf8');
    const { stdout } = await execFile('python', [script, `--image=${temporaryImage}`, `--elements-file=${temporaryElements}`], { timeout: 30_000, maxBuffer: 4_000_000, windowsHide: true, env: pythonUtf8Env });
    const refined = JSON.parse(stdout) as Array<Partial<CanvasElement>>;
    if (!Array.isArray(refined) || refined.length !== assets.length) return elements;
    let index = 0;
    return elements.map((item) => item.kind !== 'icon' && item.kind !== 'image' ? item : { ...item, x: number(refined[index].x, item.x, 0, 1598), y: number(refined[index].y, item.y, 0, 898), w: number(refined[index].w, item.w, 2, 1600), h: number(refined[index++].h, item.h, 2, 900) });
  } catch {
    return elements;
  } finally {
    await Promise.all([temporaryImage, temporaryElements].map((path) => unlink(path).catch(() => undefined)));
  }
}

async function detectAccentAssets(image: string, elements: CanvasElement[]) {
  const base64 = image.slice(image.indexOf(',') + 1);
  const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const temporaryImage = join(tmpdir(), `ppt-accent-detect-${token}.img`);
  const temporaryElements = join(tmpdir(), `ppt-accent-detect-${token}.json`);
  try {
    await Promise.all([
      writeFile(temporaryImage, Buffer.from(base64, 'base64')),
      writeFile(temporaryElements, JSON.stringify(elements), 'utf8'),
    ]);
    const script = join(process.cwd(), 'tools', 'vision', 'detect_accent_assets.py');
    const { stdout } = await execFile('python', [script, `--image=${temporaryImage}`, `--elements-file=${temporaryElements}`], { timeout: 20_000, maxBuffer: 2_000_000, windowsHide: true, env: pythonUtf8Env });
    const detected = JSON.parse(stdout) as unknown[];
    const additions = Array.isArray(detected) ? detected.map(normalize).filter((item): item is CanvasElement => item?.kind === 'icon') : [];
    const hasNearby = (candidate: CanvasElement) => elements.some((item) => (item.kind === 'icon' || item.kind === 'image') && Math.abs(item.x + item.w / 2 - (candidate.x + candidate.w / 2)) < 18 && Math.abs(item.y + item.h / 2 - (candidate.y + candidate.h / 2)) < 18);
    return [...elements, ...additions.filter((item) => !hasNearby(item))];
  } catch {
    return elements;
  } finally {
    await Promise.all([temporaryImage, temporaryElements].map((path) => unlink(path).catch(() => undefined)));
  }
}

async function repairLowQualityLayout(key: string, image: string, elements: CanvasElement[], issues: string[]) {
  const compact = elements.map(({ id: _id, sourceText: _sourceText, sourceElement: _sourceElement, ...item }) => item);
  const prompt = `You are the second-pass QA fixer in an image-to-editable-PPT reconstruction pipeline. The first pass failed its quality gate.
Return ONLY one strict JSON object: {"elements":[...]}. Return the COMPLETE corrected inventory, not a patch.

Quality failures: ${issues.join('; ') || 'layout quality score below threshold'}
First-pass inventory: ${JSON.stringify(compact)}

Fix rules:
1. Use the attached source slide as ground truth and 1600x900 coordinates.
2. No two distinct text boxes may overlap. Merge duplicated or split fragments into the correct complete text object, or tighten/reposition their boxes to the visible glyph bounds.
3. Keep every visible title, label, number, unit and paragraph exactly once.
4. Preserve cards, dividers, arrows and ellipses as native rectangle/line/connector/arrow/ellipse objects.
5. Preserve logos, pictograms and illustrations as kind=icon with tight boxes containing only the graphic and no nearby text, bullets, rules or whitespace.
6. Every object must stay inside x=0..1600 and y=0..900. Do not invent content.`;
  const response = await fetch('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: dashScopeVisionModel(), temperature: 0, enable_thinking: false, response_format: { type: 'json_object' }, max_completion_tokens: 12000, messages: [{ role: 'user', content: [{ type: 'text', text: prompt }, { type: 'image_url', image_url: { url: image, min_pixels: 65536, max_pixels: 1600000 } }] }] }) });
  const payload = await response.json() as { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string } };
  if (!response.ok || !payload.choices?.[0]?.message?.content) throw new Error(payload.error?.message || '二次布局校正未返回内容。');
  let parsed: { elements?: unknown[] };
  try { parsed = jsonFromModel(payload.choices[0].message.content); }
  catch { parsed = await askForJsonRepair(key, payload.choices[0].message.content); }
  return sanitizeInventory(await refineTextBoxes(image, (Array.isArray(parsed.elements) ? parsed.elements : []).slice(0, 180).map(normalize).filter((item): item is CanvasElement => item !== null)));
}

export async function POST(request: Request) {
  const denied = guardApiSecret(request); if (denied) return denied;
  const key = dashScopeKey(), ocrKey = dashScopeOcrKey();
  if (!ocrKey) return Response.json({ error: '未配置 DASHSCOPE_OCR_API_KEY。请在 .env.local 配置后重启服务。' }, { status: 503 });
  try {
    const requestImage = (await request.json() as { image?: unknown }).image;
    if (typeof requestImage !== 'string' || !requestImage.startsWith('data:image/') || requestImage.length > 20_000_000) return Response.json({ error: '请提交 20 MB 以下的图片 Data URL。' }, { status: 400 });
    if (!key) throw new Error('完整语义重建需要 DASHSCOPE_API_KEY；当前不再降级为擦字覆盖模式。');
    const normalizedSource = await normalizeWorkbenchSource(requestImage);
    const image = normalizedSource.image;
    // Stage 1: OCR owns every character and exact text rectangle.
    const allOcrLines = await extractPreciseOcr(image);
    const ocrInventory = allOcrLines.map(({ ocrIndex, text, x, y, w, h }) => ({ ocrIndex, text, x, y, w, h }));
    // Stage 2: the vision model receives the authoritative OCR inventory and
    // owns semantic grouping, native shapes, asset boxes and typography roles.
    const stylePrompt = `You are the typography planner for a high-quality image-to-editable-PowerPoint reconstruction. The attached slide is visual ground truth. OCR text and boxes are authoritative. Return ONLY JSON: {"elements":[...]}. Return exactly one kind=text object for every OCR item and no non-text objects. Each object must carry the exact ocrIndex, a short semantic name, role=label|data|body, and faithful fontSize/fontWeight/color/align. Copy OCR text exactly or leave it empty; never transcribe or correct characters. Use strict 1600x900 coordinates.

OCR_INVENTORY=${JSON.stringify(ocrInventory)}

Text schema: {"kind":"text","name":"semantic name","role":"label|data|body","ocrIndex":0,"x":0,"y":0,"w":0,"h":0,"text":"","fontSize":24,"fontWeight":400,"color":"#RRGGBB","align":"left|center|right"}`;
    const structurePrompt = `You are the structure and asset planner for a high-quality image-to-editable-PowerPoint reconstruction. The attached slide is visual ground truth. OCR text boxes are authoritative anchors. Return ONLY one JSON object, no markdown, with NO kind=text objects.

OCR_INVENTORY=${JSON.stringify(ocrInventory)}

Schema:
{"elements":[{"kind":"rectangle|ellipse|arrow|line|connector|icon|image|table|freeform","name":"stable semantic name","role":"decoration|container|icon|photo|brand|chart|flow","reconstructionClass":"decorative_fixed|decorative_movable only when role=decoration","semanticImpact":false,"containsOcr":[0,1],"x":0,"y":0,"w":0,"h":0,"fill":"#RRGGBB","stroke":"#RRGGBB","strokeWidth":1,"radius":0,"opacity":1}],"note":"short reconstruction note"}

Mandatory reconstruction contract:
1. Use strict 1600x900 coordinates and return objects in background-to-foreground order.
2. Rebuild every visible foreground structure: outer cards, rounded panels, coloured number tabs, title bars, data panels, borders, dividers, rules, circular hosts, arrows, chevrons and table cells. Decompose compound cards into the real component shapes. Never use a full-width coloured header when only a small number tab is coloured.
3. Every role=container object MUST declare containsOcr with all OCR indices visually inside that host. Card-01..Card-06 must each own their matching number, title and body OCR indices. Header and footer containers must own their corresponding OCR indices. This relationship is quality-gated.
4. Return each logo, pictogram, illustration, chart artwork or photo as one independent kind=icon or kind=image with a tight box containing only that asset. Exclude nearby text, bullet dots, rules and excess whitespace. Icons have containsOcr=[] unless a character is inseparable from the icon artwork.
5. Prefer rectangle/ellipse/arrow/line/connector/table; use freeform only for a truly nonstandard host. Do not return the whole slide as an image.
6. Include pure decoration too. Use role=decoration only when it carries no data, meaning, navigation or branding; otherwise default to semantic foreground. For large low-opacity roads, city silhouettes, watermarks or waves spanning content regions, set reconstructionClass=decorative_fixed and semanticImpact=false. For an independently movable decoration, set decorative_movable. When uncertain, do not classify it as decoration.
7. Preserve exact colours, corner radii, stroke widths, repeated spacing and z-order. No commentary outside JSON.`;
    const [styleResponse, structureResponse] = await Promise.all([
      fetchWithRetry('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: dashScopeVisionModel(), temperature: 0, enable_thinking: false, response_format: { type: 'json_object' }, max_completion_tokens: 12000, messages: [{ role: 'user', content: [{ type: 'text', text: stylePrompt }, { type: 'image_url', image_url: { url: image, min_pixels: 65536, max_pixels: 3200000 } }] }] }) }, 3, 'vision-text-style'),
      fetchWithRetry('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: dashScopeVisionModel(), temperature: 0, enable_thinking: false, response_format: { type: 'json_object' }, max_completion_tokens: 18000, messages: [{ role: 'user', content: [{ type: 'text', text: structurePrompt }, { type: 'image_url', image_url: { url: image, min_pixels: 65536, max_pixels: 3200000 } }] }] }) }, 3, 'vision-structure'),
    ]);
    const stylePayload = await styleResponse.json() as { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string } };
    const structurePayload = await structureResponse.json() as { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string } };
    if (!styleResponse.ok || !structureResponse.ok) throw new Error(stylePayload.error?.message || structurePayload.error?.message || `视觉语义规划失败（样式 ${styleResponse.status}，结构 ${structureResponse.status}）。`);
    const styleContent = stylePayload.choices?.[0]?.message?.content, structureContent = structurePayload.choices?.[0]?.message?.content;
    if (!styleContent || !structureContent) throw new Error('视觉模型未返回完整的文字样式或结构清单。');
    let styleParsed: { elements?: unknown[] }, structureParsed: { elements?: unknown[]; note?: unknown };
    try { styleParsed = jsonFromModel(styleContent); } catch { styleParsed = await askForJsonRepair(key, styleContent); }
    try { structureParsed = jsonFromModel(structureContent); } catch { structureParsed = await askForJsonRepair(key, structureContent); }
    const visualText = (Array.isArray(styleParsed.elements) ? styleParsed.elements : []).slice(0, 180).map(normalize).filter((item): item is CanvasElement => item?.kind === 'text');
    const visualStructure = (Array.isArray(structureParsed.elements) ? structureParsed.elements : []).slice(0, 180).map(normalize).filter((item): item is CanvasElement => item !== null && item.kind !== 'text' && item.kind !== 'group');
    const visualElements = [...visualStructure, ...visualText];
    // A pictogram may contain a visible letter/character (for example the 廉
    // inside a shield).  It belongs to the protected graphic asset, not the
    // editable text layer; otherwise cleaning destroys the icon itself.
    const rawOcrLines = allOcrLines.filter((line) => !isEmbeddedGraphicText(line, visualElements));
    const embeddedGraphicTextCount = allOcrLines.length - rawOcrLines.length;
    if (!rawOcrLines.length) throw new Error('OCR 文本全部落在图标或图片保护区内，无法安全生成可编辑文本层。');
    const styledOcrLines = styleOcrTextFromVision(rawOcrLines, visualElements);
    assertTextIntegrity(styledOcrLines.map((line, index) => normalize({ kind: 'text', name: `OCR ${index + 1}`, ...line }, index)).filter((item): item is CanvasElement => item !== null), 'OCR 返回后');
    const textElements = await styleOcrTextBoxes(image, mergeOcrText(visualElements, styledOcrLines));
    const foregroundStructure = normalizeSemanticAssetKinds(keepForegroundSemanticObjects(visualStructure));
    const calibratedStructure = calibrateSemanticGeometry(foregroundStructure, textElements);
    const ownedStructure = normalizeSemanticOwnership(calibratedStructure, textElements);
    const repeatedCardStructure = reconstructRepeatedCards(ownedStructure, textElements);
    const deterministicCardStructure = reconstructNumberedCardGrid(repeatedCardStructure, textElements);
    const reconstructedStructure = reconstructWideLabelCards(deterministicCardStructure, textElements);
    const assetCalibratedStructure = calibrateCardAssetLocations(reconstructedStructure, textElements);
    const refinedStructure = await refineAssetBoxes(image, assetCalibratedStructure);
    const nativeAndAssets = await detectAccentAssets(image, refinedStructure);
    const semanticTextElements = styleTextBySemanticHosts(textElements, nativeAndAssets);
    const elements = enrichV3Inventory(sanitizeInventory([...nativeAndAssets, ...semanticTextElements]));
    if (!elements.length) throw new Error('高精 OCR 未识别到可编辑文字。');
    assertTextIntegrity(elements, 'UTF-8 样式测量后');
    const protocol = buildRebuildProtocol(elements, { sourceWidth: 1600, sourceHeight: 900, expectedOcrCount: rawOcrLines.length });
    return Response.json({
      elements,
      protocol,
      repairAttempted: false,
      repairImproved: false,
      ocrTextCount: rawOcrLines.length,
      embeddedGraphicTextCount,
      backgroundRequired: true,
      sourceReference: image,
      sourceNormalization: normalizedSource.normalization,
      note: `v3 并发清单已完成：${protocol.quality.nativeObjectCount} 个原生结构、${protocol.quality.assetCount} 个待图库/资产执行对象、${protocol.quality.textCount} 个 OCR 文本。当前仅为清单候选；必须先生成联合掩膜 BG_CLEAN，并分别通过文字、非文本、背景和可编辑性渲染审核。`,
      model: dashScopeVisionModel(),
      ocrModel: dashScopeOcrModel(),
      mode: 'qwen-only-parallel-workbench-v3',
    });
  } catch (error) { return Response.json({ error: error instanceof Error ? error.message : '幻灯片视觉识别失败。' }, { status: 502 }); }
}
