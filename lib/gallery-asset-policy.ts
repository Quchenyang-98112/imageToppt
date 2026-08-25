import type { AssetKind, CanvasElement } from './types';
import { QWEN_GLOBAL_POLICY } from './qwen-global-policy';

export type GalleryCandidate = {
  id: string;
  actualAssetKind: AssetKind;
  similarity: number;
  structuralVetoes: string[];
  previewPng?: string;
  nativeComponentId?: string;
  svgSource?: string;
  pngSource?: string;
};

export type GalleryExecution =
  | { action: 'instantiate_native'; assetId: string; componentId: string }
  | { action: 'insert_svg'; assetId: string; svgSource: string }
  | { action: 'insert_png'; assetId: string; pngSource: string }
  | { action: 'qwen_image'; reason: string };

export function resolveGalleryExecution(candidate: GalleryCandidate | undefined): GalleryExecution {
  const policy = QWEN_GLOBAL_POLICY.gallery_policy;
  if (!candidate) return { action: 'qwen_image', reason: 'no_gallery_candidate' };
  if (candidate.structuralVetoes.length) return { action: 'qwen_image', reason: `structural_veto:${candidate.structuralVetoes.join(',')}` };
  if (candidate.similarity < policy.render_rerank_floor) return { action: 'qwen_image', reason: 'similarity_below_floor' };
  if (candidate.actualAssetKind === 'native_editable' && candidate.nativeComponentId) {
    return { action: 'instantiate_native', assetId: candidate.id, componentId: candidate.nativeComponentId };
  }
  if (candidate.actualAssetKind === 'svg' && candidate.svgSource) {
    return { action: 'insert_svg', assetId: candidate.id, svgSource: candidate.svgSource };
  }
  if (candidate.actualAssetKind === 'png' && candidate.pngSource) {
    return { action: 'insert_png', assetId: candidate.id, pngSource: candidate.pngSource };
  }
  return { action: 'qwen_image', reason: `missing_actual_${candidate.actualAssetKind}_payload` };
}

export function assertExecutedGalleryElement(element: CanvasElement) {
  if (!element.galleryAssetId) return;
  if (!element.assetKind) throw new Error(`${element.id}: gallery asset has no actual assetKind.`);
  if (element.sourceCropUsedAsFinal) throw new Error(`${element.id}: source query crop cannot be used as the final asset.`);
  if ((element.assetKind === 'native_editable' || element.assetKind === 'svg') && element.imageSrc?.startsWith('data:image/png')) {
    throw new Error(`${element.id}: retrieval preview PNG cannot replace ${element.assetKind}.`);
  }
  if ((element.structuralVetoes?.length ?? 0) > 0) throw new Error(`${element.id}: structural gallery veto remains unresolved.`);
  if ((element.gallerySimilarity ?? 0) < QWEN_GLOBAL_POLICY.gallery_policy.render_rerank_floor) {
    throw new Error(`${element.id}: gallery match is below the post-render floor.`);
  }
  if (element.qaStatus !== 'passed') throw new Error(`${element.id}: gallery asset has not passed local post-insert render review.`);
  if (element.assetKind === 'native_editable' && !element.nativeComponentId) throw new Error(`${element.id}: native gallery asset has no component ID.`);
  if ((element.assetKind === 'svg' || element.assetKind === 'png') && !element.assetSource && !element.imageSrc) throw new Error(`${element.id}: executable asset payload is missing.`);
}
