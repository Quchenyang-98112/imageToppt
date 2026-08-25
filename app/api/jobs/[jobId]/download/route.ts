import { access, readFile } from 'node:fs/promises';
import { basename, join, resolve } from 'node:path';
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
    const root = resolve(jobDir(jobId));
    const completedFile = job.status === 'completed' && job.outputPath ? resolve(job.outputPath) : null;
    const candidateFile = resolve(join(jobDir(jobId), 'final', 'candidate-editable.pptx'));
    const file = completedFile || candidateFile;
    await access(file);
    if (!file.startsWith(`${root}\\`) && !file.startsWith(`${root}/`)) return Response.json({ error: '输出路径越界。' }, { status: 500 });
    const bytes = await readFile(file);
    const candidate = !completedFile;
    return new Response(bytes, { headers: { 'content-type': 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'content-disposition': `attachment; filename="${basename(file)}"`, 'x-skill-merge-artifact': candidate ? 'candidate' : 'published' } });
  } catch {
    return Response.json({ error: '任务不存在或尚未生成候选 PPTX。' }, { status: 404 });
  }
}
