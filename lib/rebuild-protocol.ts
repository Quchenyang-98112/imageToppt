import type { AssetKind, CanvasElement, ReconstructionClass } from '@/lib/types';
import { assertCanonicalSourceBox } from '@/lib/qwen-global-policy';

export type VisualInventoryItem = {
  id: string;
  elementId: string;
  role: string;
  kind: CanvasElement['kind'];
  reconstructionClass: ReconstructionClass;
  assetKind?: AssetKind;
  bbox: { x: number; y: number; w: number; h: number };
  zIndex: number;
  parentId: string | null;
  semanticImpact: boolean;
};

export type RebuildQuality = {
  passed: boolean;
  score: number;
  issues: string[];
  outOfBounds: number;
  severeTextOverlaps: number;
  textCount: number;
  nativeObjectCount: number;
  assetCount: number;
  semanticMisalignments: number;
  visibleFullSourceImages: number;
  expectedRenderedReviews: string[];
};

export type RebuildProtocol = {
  version: 3;
  schema: 'pptx-rebuild-protocol/v3';
  inventory: VisualInventoryItem[];
  quality: RebuildQuality;
  phases: {
    inputPrepared: boolean;
    parallelInventoryDone: boolean;
    candidateInventoryDone: boolean;
    localCandidateReviewDone: boolean;
    maskBuilt: boolean;
    backgroundCleaned: boolean;
    backgroundQaDone: boolean;
    textRoutePassed: boolean;
    nontextRoutePassed: boolean;
    fusionDone: boolean;
    renderQaDone: boolean;
    moveDeleteQaDone: boolean;
    validationDone: boolean;
  };
};

const nativeKinds = new Set<CanvasElement['kind']>(['text', 'rectangle', 'ellipse', 'arrow', 'line', 'connector', 'table', 'freeform']);

function intersectionRatio(a: CanvasElement, b: CanvasElement) {
  const left = Math.max(a.x, b.x), top = Math.max(a.y, b.y), right = Math.min(a.x + a.w, b.x + b.w), bottom = Math.min(a.y + a.h, b.y + b.h);
  if (right <= left || bottom <= top) return 0;
  return ((right - left) * (bottom - top)) / Math.max(1, Math.min(a.w * a.h, b.w * b.h));
}

function defaultClass(element: CanvasElement): ReconstructionClass {
  if (element.kind === 'text') return 'ocr_text';
  if (nativeKinds.has(element.kind)) return 'native_editable';
  if (element.role === 'decoration') return 'decorative_movable';
  if (element.role === 'brand') return 'exact_brand_asset';
  return 'library_png';
}

function stableRole(element: CanvasElement, index: number) {
  const seed = (element.name || element.text || element.kind).replace(/\s+/g, '_').replace(/[^\p{L}\p{N}_-]/gu, '').slice(0, 38);
  return `${element.kind}_${seed || index + 1}_${String(index + 1).padStart(3, '0')}`;
}

export function buildRebuildProtocol(elements: CanvasElement[], options: { sourceWidth?: number; sourceHeight?: number; expectedOcrCount?: number } = {}): RebuildProtocol {
  const sourceWidth = options.sourceWidth ?? 1600, sourceHeight = options.sourceHeight ?? 900;
  const inventory: VisualInventoryItem[] = [];
  const issues: string[] = [];
  let outOfBounds = 0;
  for (const [index, element] of elements.entries()) {
    const bbox: [number, number, number, number] = element.sourceBBox ?? [element.x, element.y, element.w, element.h];
    try { assertCanonicalSourceBox(bbox, sourceWidth, sourceHeight, element.id); } catch { outOfBounds += 1; }
    const reconstructionClass = element.reconstructionClass ?? defaultClass(element);
    if (reconstructionClass === 'decorative_fixed' && element.semanticImpact !== false) issues.push(`${element.id} 固定装饰未声明 semanticImpact=false`);
    if (element.galleryAssetId && !element.assetKind) issues.push(`${element.id} 图库资产缺少实际 assetKind`);
    if (element.galleryAssetId && element.assetKind !== 'png' && element.imageSrc?.startsWith('data:image/png')) issues.push(`${element.id} 使用预览PNG替代 ${element.assetKind}`);
    inventory.push({ id: stableRole(element, index), elementId: element.id, role: element.name || element.kind, kind: element.kind, reconstructionClass, assetKind: element.assetKind, bbox: { x: bbox[0], y: bbox[1], w: bbox[2], h: bbox[3] }, zIndex: element.zIndex ?? index, parentId: element.parentId ?? null, semanticImpact: element.semanticImpact ?? reconstructionClass !== 'decorative_fixed' });
  }
  const texts = elements.filter((item) => item.kind === 'text');
  let severeTextOverlaps = 0;
  for (let i = 0; i < texts.length; i += 1) for (let j = i + 1; j < texts.length; j += 1) if (intersectionRatio(texts[i], texts[j]) > .58) severeTextOverlaps += 1;
  const nativeObjectCount = inventory.filter((item) => item.reconstructionClass === 'native_editable' && item.kind !== 'text').length;
  const assetCount = inventory.filter((item) => ['library_native', 'library_svg', 'library_png', 'exact_brand_asset', 'qwen_image_asset', 'decorative_movable'].includes(item.reconstructionClass)).length;
  const expectedOcr = options.expectedOcrCount ?? texts.length;
  if (texts.length !== expectedOcr) issues.push(`OCR对象覆盖不足：${texts.length}/${expectedOcr}`);
  if (outOfBounds) issues.push(`${outOfBounds} 个对象违反源像素坐标契约`);
  if (severeTextOverlaps) issues.push(`${severeTextOverlaps} 组文本框严重重叠`);
  // v3 deliberately never derives a pass score from object counts. A protocol
  // remains pending until background/text/non-text/fusion/editability renders pass.
  const expectedRenderedReviews = ['background_only', 'ocr_on_neutral', 'nontext_on_neutral', 'composite', 'difference_heatmap', 'move_test', 'delete_test'];
  return {
    version: 3,
    schema: 'pptx-rebuild-protocol/v3',
    inventory,
    quality: { passed: false, score: 0, issues: [...issues, '等待独立渲染审核；对象数量不能决定通过'], outOfBounds, severeTextOverlaps, textCount: texts.length, nativeObjectCount, assetCount, semanticMisalignments: 0, visibleFullSourceImages: 0, expectedRenderedReviews },
    phases: { inputPrepared: true, parallelInventoryDone: true, candidateInventoryDone: false, localCandidateReviewDone: false, maskBuilt: false, backgroundCleaned: false, backgroundQaDone: false, textRoutePassed: false, nontextRoutePassed: false, fusionDone: false, renderQaDone: false, moveDeleteQaDone: false, validationDone: false },
  };
}
