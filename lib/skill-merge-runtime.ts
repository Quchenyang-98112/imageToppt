import { join, resolve } from 'node:path';

export const SKILL_MERGE_SCHEMA = 'skill-merge-job/v1' as const;
export const SOURCE_WIDTH = 1600;
export const SOURCE_HEIGHT = 900;
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const MAX_PAGES_PER_JOB = 50;

export type JobStatus = 'queued' | 'running' | 'needs_review' | 'completed' | 'failed' | 'cancelled';
export type PageStatus = 'uploaded' | 'ocr' | 'layout' | 'assets' | 'building' | 'render_qa' | 'passed' | 'needs_review' | 'failed';

export const runtimeRoot = () => resolve(process.env.SKILL_MERGE_DATA_ROOT?.trim() || join(process.cwd(), 'data', 'skill-merge'));
export const jobIdPattern = /^[a-zA-Z0-9][a-zA-Z0-9_-]{7,79}$/;
export const isSafeJobId = (value: string) => jobIdPattern.test(value);

export const modelPolicy = Object.freeze({
  ocr: process.env.DASHSCOPE_OCR_MODEL?.trim() || 'qwen3.5-ocr',
  vision: process.env.DASHSCOPE_VISION_MODEL?.trim() || 'qwen3-vl-plus',
  image: process.env.DASHSCOPE_IMAGE_EDIT_MODEL?.trim() || 'qwen-image-2.0-pro',
  provider: 'dashscope',
  mode: 'strict_high_fidelity_qwen_only',
});

export function assertRuntimePolicy() {
  const forbidden = /(^|[-_])(openai|chatgpt|gpt)([-_]|$)/i;
  for (const [name, value] of Object.entries(modelPolicy)) {
    if (typeof value === 'string' && forbidden.test(value)) throw new Error(`生产模型策略禁止 ${name}=${value}`);
  }
  if (modelPolicy.ocr !== 'qwen3.5-ocr' || modelPolicy.vision !== 'qwen3-vl-plus' || modelPolicy.image !== 'qwen-image-2.0-pro') {
    throw new Error('模型白名单不匹配：必须使用 qwen3.5-ocr、qwen3-vl-plus、qwen-image-2.0-pro。');
  }
}

export const allowedImageTypes = new Set(['image/png', 'image/jpeg']);
export const extensionForMime = (mime: string) => mime === 'image/png' ? '.png' : '.jpg';
