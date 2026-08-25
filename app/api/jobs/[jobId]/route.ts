import { readFile } from 'node:fs/promises';
import { guardApiSecret } from '@/lib/ai-config';
import { jobDir, readJob } from '@/lib/job-store';
import { isSafeJobId } from '@/lib/skill-merge-runtime';

export const runtime = 'nodejs';

export async function GET(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  const { jobId } = await context.params;
  if (!isSafeJobId(jobId)) return Response.json({ error: '非法任务 ID。' }, { status: 400 });
  try {
    const job = await readJob(jobId);
    const events = new URL(request.url).searchParams.get('events') === '1' ? (await readFile(job.eventsFile, 'utf8').catch(() => '')).trim().split('\n').filter(Boolean).map((line) => JSON.parse(line)) : undefined;
    return Response.json(events ? { job, events } : { job });
  } catch {
    return Response.json({ error: '任务不存在。' }, { status: 404 });
  }
}
