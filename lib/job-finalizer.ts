import { execFile as execFileCallback } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { promisify } from 'node:util';
import { jobDir, readJob, writeJob, appendJobEvent } from '@/lib/job-store';
import { compileDeck } from '@/lib/pptx-compiler';
import type { CanvasElement } from '@/lib/types';

const execFile = promisify(execFileCallback);
const python = () => process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python';

export async function compileAndReviewJob(id: string) {
  const job = await readJob(id);
  if (!['needs_review', 'failed'].includes(job.status)) throw new Error('任务尚未完成逐页分析，不能进入 PPTX 编译。');
  const finalDir = join(jobDir(id), 'final');
  const renderDir = join(finalDir, 'rendered');
  await mkdir(renderDir, { recursive: true });
  const pages: Array<{ sourcePath: string; backgroundPath: string; elements: CanvasElement[]; title?: string }> = [];
  const pageReports: Record<string, unknown>[] = [];
  for (const page of job.pages) {
    const pageDir = join(jobDir(id), 'pages', page.id);
    const analysis = JSON.parse(await readFile(join(pageDir, 'analysis.json'), 'utf8')) as { elements?: CanvasElement[] };
    pages.push({ sourcePath: join(pageDir, 'source.png'), backgroundPath: join(pageDir, 'bg-clean-candidate.png'), elements: Array.isArray(analysis.elements) ? analysis.elements : [], title: page.originalName });
  }
  const candidatePath = join(finalDir, 'candidate-editable.pptx');
  await compileDeck(pages, candidatePath);
  const renderResult = await execFile('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-File', join(process.cwd(), 'tools', 'powerpoint_render.ps1'), '-InputPptx', candidatePath, '-OutputDir', renderDir], { timeout: 300_000, maxBuffer: 4_000_000, windowsHide: true });
  const rendered = (await import('node:fs/promises')).readdir(renderDir);
  const renderedNames = (await rendered).filter((name) => /\.png$/i.test(name)).sort();
  if (renderedNames.length !== pages.length) throw new Error(`PowerPoint 渲染页数不匹配：${renderedNames.length}/${pages.length}`);
  for (let index = 0; index < pages.length; index += 1) {
    const page = job.pages[index];
    const pageDir = join(jobDir(id), 'pages', page.id);
    const reviewPath = join(pageDir, 'qwen-slide-review.json');
    try {
      await execFile(python(), [join(process.cwd(), 'tools', 'qwen_slide_review.py'), '--source', pages[index].sourcePath, '--render', join(renderDir, renderedNames[index]), '--output', reviewPath, '--round', '1'], { timeout: 360_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
      const review = JSON.parse(await readFile(reviewPath, 'utf8')) as Record<string, unknown>;
      pageReports.push({ page: page.id, qwenReview: review, render: join(renderDir, renderedNames[index]) });
      const textScore = Number(review.textScore || 0);
      const nontextScore = Number(review.nontextScore || 0);
      const fusionScore = Number(review.fusionScore || 0);
      const backgroundScore = review.backgroundPassed === true ? 1 : 0;
      const qualityScore = Math.round((textScore * .3 + nontextScore * .3 + fusionScore * .25 + backgroundScore * .15) * 100);
      page.metrics = { ...(page.metrics || {}), qwenTextScore: textScore, qwenNontextScore: nontextScore, qwenFusionScore: fusionScore, qwenReviewPassed: review.pass === true, qualityScore };
    } catch (error) {
      pageReports.push({ page: page.id, qwenReview: 'failed', error: error instanceof Error ? error.message : 'Qwen slide review failed' });
      page.metrics = { ...(page.metrics || {}), qwenReviewPassed: false };
    }
    page.status = 'needs_review';
    page.previewPath = join(renderDir, renderedNames[index]);
  }
  const report = { schema: 'skill-merge-final-review/v1', jobId: id, candidatePath, renderer: renderResult.stdout.trim(), pages: pageReports, publishable: false, blockingReason: 'candidate deck is rendered and reviewed, but editability move/delete gates and accepted asset provenance are not yet recorded' };
  await writeFile(join(finalDir, 'review-report.json'), JSON.stringify(report, null, 2), 'utf8');
  job.candidatePath = candidatePath;
  job.status = 'needs_review'; job.error = '已生成并渲染候选 PPTX；移动/删除可编辑性和最终素材 provenance 门禁尚未通过，因此暂不发布。';
  await writeJob(job); await appendJobEvent(id, { type: 'warning', message: job.error, data: { candidatePath, renderedSlides: renderedNames.length } });
  return { job, report };
}
