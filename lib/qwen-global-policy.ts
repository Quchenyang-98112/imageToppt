import policy from '../config/qwen-global-policy.json';

export const QWEN_GLOBAL_POLICY = policy;

export type QualityGateScores = {
  textScore: number;
  nontextScore: number;
  fusionScore?: number;
  criticalTextPassed?: boolean;
  majorNontextMissing?: boolean;
  layeringPassed?: boolean;
  coordinateContractPassed?: boolean;
  backgroundPassed?: boolean;
  renderedComparisonPassed?: boolean;
  editabilityPassed?: boolean;
  decorationPassed?: boolean;
  candidateCoverage?: number;
  unverifiedNontextCandidates?: number;
  numericOcrMisclassifications?: number;
};

export function assertQwenOnlyModels(models: { ocr?: string; vision?: string; image_generation?: string }) {
  const expected = QWEN_GLOBAL_POLICY.models;
  if (models.ocr && models.ocr !== expected.ocr) throw new Error(`Global Qwen policy violation: OCR model must be ${expected.ocr}.`);
  if (models.vision && models.vision !== expected.vision) throw new Error(`Global Qwen policy violation: vision model must be ${expected.vision}.`);
  if (models.image_generation && models.image_generation !== expected.image_generation) throw new Error(`Global Qwen policy violation: image model must be ${expected.image_generation}.`);
}

export function passesDualRouteGate(scores: QualityGateScores) {
  const gate = QWEN_GLOBAL_POLICY.quality_gates;
  return scores.textScore >= gate.text_min_score
    && scores.nontextScore >= gate.nontext_min_score
    && (scores.fusionScore === undefined || scores.fusionScore >= gate.fusion_min_score)
    && scores.criticalTextPassed !== false
    && scores.majorNontextMissing !== true
    && scores.layeringPassed !== false
    && scores.coordinateContractPassed !== false
    && scores.backgroundPassed !== false
    && scores.renderedComparisonPassed !== false
    && scores.editabilityPassed !== false
    && scores.decorationPassed !== false
    && (scores.candidateCoverage === undefined || scores.candidateCoverage >= .95)
    && (scores.unverifiedNontextCandidates === undefined || scores.unverifiedNontextCandidates === 0)
    && (scores.numericOcrMisclassifications === undefined || scores.numericOcrMisclassifications === 0);
}

export function assertGlobalPolicyV3() {
  if (QWEN_GLOBAL_POLICY.schema !== 'qwen-global-reconstruction-policy/v3') throw new Error('Global reconstruction policy v3 is required.');
  if (QWEN_GLOBAL_POLICY.background_policy.mode !== 'background_first_clean_from_union_foreground_mask') throw new Error('BASE_BG background-first union-mask policy is required.');
  if (QWEN_GLOBAL_POLICY.background_policy.unresolved_region_export !== 'forbidden') throw new Error('Unresolved background regions must block export.');
  if (!QWEN_GLOBAL_POLICY.workbench.forbid_visible_full_source_reference) throw new Error('Visible full source references must be forbidden.');
  if (!QWEN_GLOBAL_POLICY.gallery_policy.preview_png_is_retrieval_only) throw new Error('Gallery preview PNGs must remain retrieval-only.');
}

export type SourceBox = readonly [number, number, number, number];

/** Enforces the one global source-pixel geometry contract. */
export function assertCanonicalSourceBox(box: unknown, sourceWidth: number, sourceHeight: number, label = 'element'): asserts box is SourceBox {
  if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite)) {
    throw new Error(`Coordinate contract violation (${label}): bbox must be [x,y,w,h].`);
  }
  const [x, y, w, h] = box as number[];
  if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > sourceWidth || y + h > sourceHeight) {
    throw new Error(`Coordinate contract violation (${label}): bbox is outside source bounds.`);
  }
}

export function sourceToContainedSlide(box: SourceBox, sourceWidth: number, sourceHeight: number, slideWidth: number, slideHeight: number) {
  const scale = Math.min(slideWidth / sourceWidth, slideHeight / sourceHeight);
  const offsetX = (slideWidth - sourceWidth * scale) / 2;
  const offsetY = (slideHeight - sourceHeight * scale) / 2;
  return { x: offsetX + box[0] * scale, y: offsetY + box[1] * scale, w: box[2] * scale, h: box[3] * scale, scale, offsetX, offsetY };
}

export function requireDualRouteGate(scores: QualityGateScores) {
  if (!passesDualRouteGate(scores)) {
    throw new Error(`Global Qwen quality gate failed: text=${scores.textScore.toFixed(3)}, nontext=${scores.nontextScore.toFixed(3)}, fusion=${scores.fusionScore === undefined ? 'n/a' : scores.fusionScore.toFixed(3)}.`);
  }
}
