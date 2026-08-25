import { rm } from 'node:fs/promises';
import { join } from 'node:path';
import { guardApiSecret } from '@/lib/ai-config';
import { appendJobEvent, jobDir, readJob, writeJob } from '@/lib/job-store';
import { isSafeJobId } from '@/lib/skill-merge-runtime';
import { launchJob } from '@/lib/job-worker';

export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  const { jobId } = await context.params;
  if (!isSafeJobId(jobId)) return Response.json({ error: '非法任务 ID。' }, { status: 400 });
  try {
    const job = await readJob(jobId);
    if (!['failed', 'cancelled'].includes(job.status)) return Response.json({ error: '只有失败或取消的任务可以断点续跑。' }, { status: 409 });
    for (const page of job.pages) {
      if (page.status === 'needs_review' && page.manifestPath) continue;
      await rm(join(jobDir(jobId), 'pages', page.id), { recursive: true, force: true });
      page.status = 'uploaded';
      page.error = undefined;
      page.sourcePath = undefined;
      page.manifestPath = undefined;
      page.previewPath = undefined;
      page.pptxPath = undefined;
      const preserved = page.metrics ? { bytes: page.metrics.bytes, sha256: page.metrics.sha256 } : undefined;
      page.metrics = preserved as typeof page.metrics;
    }
    job.status = 'queued';
    job.error = undefined;
    await writeJob(job);
    await appendJobEvent(jobId, { type: 'stage', message: '已触发断点续跑：保留已有完整页面，仅重试失败页面。' });
    launchJob(jobId);
    return Response.json({ job, resumed: true }, { status: 202 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : '断点续跑失败。' }, { status: 422 });
  }
}
