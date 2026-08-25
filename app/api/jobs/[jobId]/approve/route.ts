import { copyFile, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { guardApiSecret } from '@/lib/ai-config';
import { jobDir, readJob, writeJob, appendJobEvent } from '@/lib/job-store';
import { isSafeJobId } from '@/lib/skill-merge-runtime';

const execFile = promisify(execFileCallback);
const python = () => process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python';

type ApprovalEvidence = {
  pageId: string;
  regionReviewPassed: boolean;
  assetProvenanceAccepted: boolean;
  editabilityManifest: Record<string, unknown>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function number(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function validateEvidence(value: unknown): value is ApprovalEvidence {
  if (!isRecord(value) || typeof value.pageId !== 'string') return false;
  if (value.regionReviewPassed !== true || value.assetProvenanceAccepted !== true) return false;
  const manifest = value.editabilityManifest;
  if (!isRecord(manifest)) return false;
  if (typeof manifest.sourceImageHash !== 'string' || !manifest.sourceImageHash) return false;
  if (number(manifest.expectedOcrObjects) === null || number(manifest.visibleOcrObjects) === null) return false;
  if (!Array.isArray(manifest.visiblePictures) || !Array.isArray(manifest.moveTests) || !Array.isArray(manifest.deleteTests)) return false;
  return manifest.foregroundOnlyRenderPassed === true && manifest.stableObjectNamesPassed === true && manifest.componentGroupingPassed === true;
}

async function readJson(path: string) {
  return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
}

export const runtime = 'nodejs';
export const maxDuration = 300;

/**
 * Publish is deliberately a separate, evidence-bearing operation. The browser
 * may request it, but it cannot turn a candidate into a deliverable deck by
 * setting a single `approved` flag: every page must carry a real editability
 * manifest and pass the deterministic audit script.
 */
export async function POST(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  const { jobId } = await context.params;
  if (!isSafeJobId(jobId)) return Response.json({ error: '非法任务 ID。' }, { status: 400 });
  try {
    const job = await readJob(jobId);
    if (job.status !== 'needs_review') return Response.json({ error: '只有 needs_review 任务可以提交最终复核证据。' }, { status: 409 });
    const body = await request.json() as { pages?: unknown };
    if (!Array.isArray(body.pages) || body.pages.length !== job.pages.length) return Response.json({ error: '必须为每一页提交一份最终复核证据。' }, { status: 400 });
    const byId = new Map<string, ApprovalEvidence>();
    for (const raw of body.pages) {
      if (!validateEvidence(raw) || byId.has(raw.pageId)) return Response.json({ error: '复核证据格式错误、重复或未完成强制字段。' }, { status: 400 });
      byId.set(raw.pageId, raw);
    }
    const candidatePath = join(jobDir(jobId), 'final', 'candidate-editable.pptx');
    const finalPath = join(jobDir(jobId), 'final', 'editable-deck.pptx');
    const auditReports: Record<string, unknown>[] = [];
    for (const page of job.pages) {
      const evidence = byId.get(page.id);
      if (!evidence) return Response.json({ error: `缺少 ${page.id} 的复核证据。` }, { status: 400 });
      const pageDir = join(jobDir(jobId), 'pages', page.id);
      const analysis = await readJson(join(pageDir, 'analysis.json'));
      const elements = Array.isArray(analysis.elements) ? analysis.elements : [];
      const expected = elements.filter((item) => isRecord(item) && item.kind === 'text').length;
      const manifest = evidence.editabilityManifest;
      if (manifest.sourceImageHash !== page.metrics?.sha256) return Response.json({ error: `${page.id} 的 sourceImageHash 与上传文件不一致。` }, { status: 422 });
      if (manifest.expectedOcrObjects !== expected) return Response.json({ error: `${page.id} 的 expectedOcrObjects 必须等于服务端 OCR 对象数（${expected}）。` }, { status: 422 });
      const inputPath = join(pageDir, 'editability-audit-input.json');
      const outputPath = join(pageDir, 'editability-audit.json');
      await writeFile(inputPath, JSON.stringify(manifest, null, 2), 'utf8');
      let audit: Record<string, unknown>;
      try {
        await execFile(python(), [join(process.cwd(), 'tools', 'qa', 'audit_editability_manifest.py'), '--manifest', inputPath, '--output', outputPath], { timeout: 120_000, maxBuffer: 2_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
        audit = await readJson(outputPath);
      } catch {
        audit = await readJson(outputPath).catch(() => ({ passed: false, hardFailures: ['audit_script_failed'] }));
      }
      const review = await readJson(join(pageDir, 'qwen-slide-review.json')).catch(() => ({ pass: false } as Record<string, unknown>));
      const background = await readJson(join(pageDir, 'background-audit.json')).catch(() => ({ passed: false } as Record<string, unknown>));
      const reviewPassed = review.pass === true && Number(review.textScore || 0) >= 0.95 && Number(review.nontextScore || 0) >= 0.88 && Number(review.fusionScore || 0) >= 0.9;
      const pagePassed = audit.passed === true && reviewPassed && background.passed === true && evidence.regionReviewPassed && evidence.assetProvenanceAccepted;
      auditReports.push({ pageId: page.id, editability: audit, qwenReviewPassed: reviewPassed, backgroundPassed: background.passed === true, regionReviewPassed: evidence.regionReviewPassed, assetProvenanceAccepted: evidence.assetProvenanceAccepted, passed: pagePassed });
      if (!pagePassed) return Response.json({ error: `${page.id} 未通过最终发布门禁。`, reports: auditReports }, { status: 422 });
    }
    await copyFile(candidatePath, finalPath);
    job.outputPath = finalPath;
    job.status = 'completed';
    job.error = undefined;
    for (const page of job.pages) { page.status = 'passed'; page.pptxPath = finalPath; }
    await writeFile(join(jobDir(jobId), 'final', 'publish-report.json'), JSON.stringify({ schema: 'skill-merge-publish/v1', jobId, outputPath: finalPath, pages: auditReports, publishedAt: new Date().toISOString() }, null, 2), 'utf8');
    await writeJob(job);
    await appendJobEvent(jobId, { type: 'completed', message: '全部页面通过最终视觉、背景和可编辑性门禁，PPTX 已发布。', data: { outputPath: finalPath } });
    return Response.json({ job, reports: auditReports });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : '提交最终复核证据失败。' }, { status: 422 });
  }
}
