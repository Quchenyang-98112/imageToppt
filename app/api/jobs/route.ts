import { guardApiSecret } from '@/lib/ai-config';
import { createJob } from '@/lib/job-store';
import { launchJob } from '@/lib/job-worker';

export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(request: Request) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  try {
    const form = await request.formData();
    const files = form.getAll('files').filter((value): value is File => value instanceof File);
    const job = await createJob(files);
    launchJob(job.id);
    return Response.json({ job, statusUrl: `/api/jobs/${job.id}`, eventsUrl: `/api/jobs/${job.id}?events=1` }, { status: 202 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : '创建批处理任务失败。' }, { status: 400 });
  }
}
