import type { CanvasElement } from '@/lib/types';
import type { PageRecord } from '@/lib/job-types';

type AnalysisPayload = { elements?: unknown[]; sourceNormalization?: unknown; protocol?: unknown; ocrTextCount?: number; model?: string; ocrModel?: string; mode?: string };

const toNumber = (value: unknown, fallback = 0) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
const bboxOf = (value: Record<string, unknown>) => {
  if (Array.isArray(value.sourceBBox) && value.sourceBBox.length === 4) return value.sourceBBox.map((item) => toNumber(item)) as [number, number, number, number];
  return [toNumber(value.x), toNumber(value.y), toNumber(value.w, 1), toNumber(value.h, 1)] as [number, number, number, number];
};

export function buildPageManifest(page: PageRecord, analysis: AnalysisPayload, paths: { source: string; bgCandidate: string; mask?: string }) {
  const raw = Array.isArray(analysis.elements) ? analysis.elements : [];
  const elements = raw.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'));
  const textBoxes: Array<Record<string, unknown>> = [];
  const shapes: Array<Record<string, unknown>> = [];
  const images: Array<Record<string, unknown>> = [];
  const inventory = elements.map((item, index) => {
    const id = String(item.id || `${page.id}-object-${index + 1}`);
    const kind = String(item.kind || 'unknown');
    const bbox = bboxOf(item);
    const classification = kind === 'text' ? 'ocr_text' : ['icon', 'image'].includes(kind) ? String(item.reconstructionClass || 'qwen_image_asset') : 'native_editable';
    const record = { id, element_id: id, kind, classification, source_bbox_px: bbox, z_index: toNumber(item.zIndex, index), parent_id: item.parentId ?? null, semantic_impact: item.semanticImpact !== false };
    if (kind === 'text') textBoxes.push({ id, text: String(item.text || ''), box_px: bbox, font_size_source: 'qwen_vision_plus_ocr_calibrated', source: 'qwen3.5-ocr' });
    else if (['icon', 'image'].includes(kind)) images.push({ id, path: item.assetSource || item.imageSrc || '', box_px: bbox, asset_kind: item.assetKind || 'pending', provenance_status: 'pending_asset_route' });
    else shapes.push({ id, kind, box_px: bbox, fill: item.fill, stroke: item.stroke, stroke_width: item.strokeWidth, radius: item.radius, z_index: record.z_index });
    return record;
  });
  return {
    schema_version: 2,
    page_id: page.id,
    source: { path: paths.source, width_px: 1600, height_px: 900, normalization: analysis.sourceNormalization || null },
    background_strategy: { mode: 'background-first', candidate_path: paths.bgCandidate, mask_path: paths.mask || null, audit_status: 'pending' },
    visual_inventory: inventory,
    text_boxes: textBoxes,
    shapes,
    images,
    image2ppt_region_decomposition: { schema_version: 'image2ppt-region-decomposition-v1', page_complexity: 'pending_review', regions: [], review_status: 'pending' },
    quality_evidence: { source_pixels_reviewed: false, text_route: 'pending', nontext_route: 'pending', background_route: 'pending', render_review: 'pending', editability_review: 'pending' },
    lifecycle: { status: 'analysis_candidate', accepted: false, blocking_reasons: ['semantic-region-review-pending', 'asset-provenance-pending', 'powerpoint-render-qa-pending'] },
    provider: { ocr: analysis.ocrModel || 'qwen3.5-ocr', vision: analysis.model || 'qwen3-vl-plus', mode: analysis.mode || 'qwen-only' },
    analysis_protocol: analysis.protocol || null,
    generated_at: new Date().toISOString(),
  };
}
