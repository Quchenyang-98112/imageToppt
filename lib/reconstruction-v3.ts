import type { CanvasElement, ReconstructionClass } from './types';
import { QWEN_GLOBAL_POLICY, assertCanonicalSourceBox } from './qwen-global-policy';

export type WorkbenchCanvas = 'SOURCE_REFERENCE' | 'BG_CLEAN' | 'OCR_LAYER' | 'NONTEXT_LAYER' | 'COMPOSITE' | 'DIFF_HEATMAP';
export type RouteName = 'background' | 'text' | 'nontext' | 'fusion' | 'editability';

export type RouteGate = {
  route: RouteName;
  score: number;
  passed: boolean;
  hardFailures: string[];
  evidence: string[];
};

export type BackgroundIntegrityEvidence = {
  outsideMaskPixelIdentity: number;
  outsideMaskSsim: number;
  outsideMaskDeltaE: number;
  ocrRescanLines: number;
  textEdgeResidual: number;
  nontextEdgeResidual: number;
  shadowResidual: number;
  decorationCompleteness: number;
  seamsPassed: boolean;
};

export type EditabilityEvidence = {
  visibleFullSourceImages: number;
  expectedOcrObjects: number;
  visibleOcrObjects: number;
  moveOriginDifference: number;
  deleteTestPassed: boolean;
  foregroundOnlyRenderPassed: boolean;
  stableObjectNamesPassed: boolean;
  groupingPassed: boolean;
};

export type TextRouteEvidence = {
  renderScore: number;
  contentAccuracy: number;
  criticalTextAccuracy: number;
  bboxMeanIou: number;
  styleMaxRelativeError: number;
  colorMaxDeltaE: number;
  visibleObjectCoverage: number;
  spellingOrOcrErrors: number;
};

export type NontextRouteEvidence = {
  renderScore: number;
  majorRecall: number;
  allRecall: number;
  bboxMeanIou: number;
  sizeMaxRelativeError: number;
  aspectMaxRelativeError: number;
  missingMajorElements: number;
  structuralVetoes: number;
  layeringPassed: boolean;
  candidateCoverage?: number;
  unverifiedCandidates?: number;
  numericOcrMisclassifications?: number;
};

export type CandidateDiscoveryEvidence = {
  proposedCandidates: number;
  verifiedCandidates: number;
  rejectedOcrOrNoise: number;
  unverifiedCandidates: number;
  candidateCoverage: number;
  numericOcrMisclassifications: number;
};

export type FusionEvidence = {
  renderScore: number;
  textNontextAlignmentPassed: boolean;
  layeringPassed: boolean;
  backgroundInflatedScore: boolean;
};

export type WorkbenchRecord = {
  schema: 'pptx-reconstruction-workbench/v3';
  sourceWidth: number;
  sourceHeight: number;
  canvases: Partial<Record<WorkbenchCanvas, string>>;
  elements: CanvasElement[];
  routeGates: Partial<Record<RouteName, RouteGate>>;
  repairQueue: RepairItem[];
};

export type RepairItem = {
  elementId: string;
  issue: 'missing_elements' | 'wrong_classification_or_match' | 'geometry_and_scale' | 'color_and_style' | 'layering_and_occlusion' | 'text_nontext_fusion';
  bbox: [number, number, number, number];
  severity: 'major' | 'minor';
  visualBand?: number;
};

const semanticClasses = new Set<ReconstructionClass>([
  'ocr_text', 'native_editable', 'library_native', 'library_svg', 'library_png', 'exact_brand_asset', 'qwen_image_asset', 'decorative_movable',
]);
const executedAssetClasses = new Set<ReconstructionClass>(['library_native', 'library_svg', 'library_png', 'exact_brand_asset', 'qwen_image_asset', 'decorative_movable']);

export function assertV3Element(element: CanvasElement, sourceWidth: number, sourceHeight: number, options: { requirePassed?: boolean } = {}) {
  if (!element.reconstructionClass) throw new Error(`Element ${element.id} has no reconstructionClass.`);
  if (!element.sourceBBox) throw new Error(`Element ${element.id} has no sourceBBox.`);
  assertCanonicalSourceBox(element.sourceBBox, sourceWidth, sourceHeight, element.id);
  if (!Number.isFinite(element.zIndex)) throw new Error(`Element ${element.id} has no zIndex.`);
  if (!Number.isFinite(element.placementConfidence)) throw new Error(`Element ${element.id} has no placementConfidence.`);
  if (element.reconstructionClass === 'decorative_fixed' && element.semanticImpact !== false) {
    throw new Error(`Fixed decoration ${element.id} must explicitly declare semanticImpact=false.`);
  }
  if (element.reconstructionClass !== 'decorative_fixed' && semanticClasses.has(element.reconstructionClass) && element.qaStatus === 'passed' && (element.structuralVetoes?.length ?? 0) > 0) {
    throw new Error(`Element ${element.id} cannot pass with structural vetoes.`);
  }
  if (element.galleryAssetId && !element.assetKind) throw new Error(`Gallery element ${element.id} has no actual assetKind.`);
  if (options.requirePassed && semanticClasses.has(element.reconstructionClass) && element.qaStatus !== 'passed') throw new Error(`Element ${element.id} has not passed its independent route review.`);
  if (options.requirePassed && executedAssetClasses.has(element.reconstructionClass)) {
    if (!element.assetKind) throw new Error(`Asset ${element.id} has no executable asset kind.`);
    if ((element.assetKind === 'png' || element.assetKind === 'svg' || element.assetKind === 'qwen_image') && !element.assetSource && !element.imageSrc) {
      throw new Error(`Asset ${element.id} has no executable payload.`);
    }
  }
}

export function evaluateBackgroundGate(evidence: BackgroundIntegrityEvidence): RouteGate {
  const gate = QWEN_GLOBAL_POLICY.quality_gates;
  const failures: string[] = [];
  if (evidence.outsideMaskPixelIdentity < gate.background_outside_mask_pixel_identity) failures.push('outside_mask_pixel_identity');
  if (evidence.outsideMaskSsim < gate.background_outside_mask_ssim) failures.push('outside_mask_ssim');
  if (evidence.outsideMaskDeltaE > gate.background_outside_mask_max_delta_e) failures.push('outside_mask_delta_e');
  if (evidence.ocrRescanLines > gate.background_ocr_rescan_max_lines) failures.push('ocr_rescan_detects_text');
  if (evidence.textEdgeResidual > gate.background_text_edge_residual_max) failures.push('text_edge_residual');
  if (evidence.nontextEdgeResidual > gate.background_nontext_edge_residual_max) failures.push('nontext_edge_residual');
  if (evidence.shadowResidual > gate.background_shadow_residual_max) failures.push('shadow_residual');
  if (evidence.decorationCompleteness < gate.decoration_visual_completeness) failures.push('fixed_decoration_incomplete');
  if (!evidence.seamsPassed) failures.push('repair_seams');
  const score = Math.min(1, evidence.outsideMaskPixelIdentity, evidence.outsideMaskSsim, evidence.decorationCompleteness, 1 - evidence.textEdgeResidual, 1 - evidence.nontextEdgeResidual);
  return { route: 'background', score, passed: failures.length === 0 && score >= gate.background_min_score, hardFailures: failures, evidence: [] };
}

export function evaluateEditabilityGate(evidence: EditabilityEvidence): RouteGate {
  const failures: string[] = [];
  if (evidence.visibleFullSourceImages > 0) failures.push('visible_full_source_reference');
  if (evidence.visibleOcrObjects !== evidence.expectedOcrObjects) failures.push('ocr_object_coverage');
  if (evidence.moveOriginDifference > QWEN_GLOBAL_POLICY.quality_gates.editability_move_origin_max_difference) failures.push('move_test_reveals_baked_foreground');
  if (!evidence.deleteTestPassed) failures.push('delete_test_reveals_baked_foreground');
  if (!evidence.foregroundOnlyRenderPassed) failures.push('foreground_only_render_failed');
  if (!evidence.stableObjectNamesPassed) failures.push('unstable_object_names');
  if (!evidence.groupingPassed) failures.push('component_grouping_failed');
  const score = failures.length ? Math.max(0, 1 - failures.length / 7) : 1;
  return { route: 'editability', score, passed: failures.length === 0, hardFailures: failures, evidence: [] };
}

export function evaluateTextGate(evidence: TextRouteEvidence): RouteGate {
  const gate = QWEN_GLOBAL_POLICY.quality_gates;
  const failures: string[] = [];
  if (evidence.renderScore < gate.text_min_score) failures.push('text_render_score');
  if (evidence.contentAccuracy < gate.text_content_min_accuracy) failures.push('text_content_accuracy');
  if (evidence.criticalTextAccuracy < gate.critical_text_accuracy) failures.push('critical_text_accuracy');
  if (evidence.bboxMeanIou < gate.text_bbox_min_iou) failures.push('text_bbox_iou');
  if (evidence.styleMaxRelativeError > gate.text_style_max_relative_error) failures.push('text_style_error');
  if (evidence.colorMaxDeltaE > gate.text_color_max_delta_e) failures.push('text_color_delta_e');
  if (evidence.visibleObjectCoverage < gate.text_visible_object_coverage) failures.push('text_object_coverage');
  if (evidence.spellingOrOcrErrors > 0) failures.push('spelling_or_ocr_error');
  return { route: 'text', score: evidence.renderScore, passed: failures.length === 0, hardFailures: failures, evidence: [] };
}

export function evaluateNontextGate(evidence: NontextRouteEvidence): RouteGate {
  const gate = QWEN_GLOBAL_POLICY.quality_gates;
  const failures: string[] = [];
  if (evidence.renderScore < gate.nontext_min_score) failures.push('nontext_render_score');
  if (evidence.majorRecall < gate.nontext_major_recall) failures.push('major_nontext_recall');
  if (evidence.allRecall < gate.nontext_all_recall) failures.push('all_nontext_recall');
  if (evidence.bboxMeanIou < gate.nontext_bbox_min_iou) failures.push('nontext_bbox_iou');
  if (evidence.sizeMaxRelativeError > gate.nontext_size_max_relative_error) failures.push('nontext_size_error');
  if (evidence.aspectMaxRelativeError > gate.nontext_aspect_max_relative_error) failures.push('nontext_aspect_error');
  if (evidence.missingMajorElements > 0) failures.push('major_nontext_missing');
  if (evidence.structuralVetoes > 0) failures.push('structural_gallery_veto');
  if (!evidence.layeringPassed) failures.push('nontext_layering');
  if ((evidence.candidateCoverage ?? 1) < 0.95) failures.push('candidate_coverage');
  if ((evidence.unverifiedCandidates ?? 0) > 0) failures.push('unverified_nontext_candidate');
  if ((evidence.numericOcrMisclassifications ?? 0) > 0) failures.push('ocr_numeric_misclassified_as_visual_asset');
  return { route: 'nontext', score: evidence.renderScore, passed: failures.length === 0, hardFailures: failures, evidence: [] };
}

/** Candidate evidence is a pre-fusion completeness gate, not a visual score. */
export function evaluateCandidateDiscoveryGate(evidence: CandidateDiscoveryEvidence): RouteGate {
  const failures: string[] = [];
  if (evidence.proposedCandidates <= 0) failures.push('no_nontext_candidates');
  if (evidence.candidateCoverage < 0.95) failures.push('candidate_coverage');
  if (evidence.unverifiedCandidates > 0) failures.push('unverified_nontext_candidate');
  if (evidence.numericOcrMisclassifications > 0) failures.push('ocr_numeric_misclassified_as_visual_asset');
  return {
    route: 'nontext',
    score: evidence.candidateCoverage,
    passed: failures.length === 0,
    hardFailures: failures,
    evidence: [`proposed=${evidence.proposedCandidates}`, `verified=${evidence.verifiedCandidates}`, `rejected=${evidence.rejectedOcrOrNoise}`],
  };
}

export function evaluateFusionGate(evidence: FusionEvidence): RouteGate {
  const failures: string[] = [];
  if (evidence.renderScore < QWEN_GLOBAL_POLICY.quality_gates.fusion_min_score) failures.push('fusion_render_score');
  if (!evidence.textNontextAlignmentPassed) failures.push('text_nontext_alignment');
  if (!evidence.layeringPassed) failures.push('fusion_layering');
  if (evidence.backgroundInflatedScore) failures.push('background_inflated_foreground_score');
  return { route: 'fusion', score: evidence.renderScore, passed: failures.length === 0, hardFailures: failures, evidence: [] };
}

const issueRank = new Map(QWEN_GLOBAL_POLICY.repair_order.map((value, index) => [value, index]));

export function sortRepairQueue(items: RepairItem[]) {
  return [...items].sort((a, b) => {
    const severity = (a.severity === 'major' ? 0 : 1) - (b.severity === 'major' ? 0 : 1);
    if (severity) return severity;
    const issue = (issueRank.get(a.issue) ?? 99) - (issueRank.get(b.issue) ?? 99);
    if (issue) return issue;
    const band = (a.visualBand ?? Math.floor(a.bbox[1] / 150)) - (b.visualBand ?? Math.floor(b.bbox[1] / 150));
    if (band) return band;
    return a.bbox[0] - b.bbox[0];
  });
}

export function requireIndependentRoutePasses(gates: Partial<Record<RouteName, RouteGate>>) {
  for (const route of ['background', 'text', 'nontext'] as const) {
    if (!gates[route]?.passed) throw new Error(`Fusion blocked: ${route} route has not independently passed.`);
  }
}
