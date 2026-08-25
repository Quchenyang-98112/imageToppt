import { guardApiSecret } from '@/lib/ai-config';
import { compileAndReviewJob } from '@/lib/job-finalizer';
import { isSafeJobId } from '@/lib/skill-merge-runtime';

export const runtime = 'nodejs';
export const maxDuration = 900;

export async function POST(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  const { jobId } = await context.params;
  if (!isSafeJobId(jobId)) return Response.json({ error: '非法任务 ID。' }, { status: 400 });
  try {
    const result = await compileAndReviewJob(jobId);
    return Response.json(result, { status: 202 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : 'PPTX 编译或 QA 失败。' }, { status: 422 });
  }
}
