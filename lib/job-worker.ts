import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, extname, join, resolve } from 'node:path';
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { POST as analyzeSlide } from '@/app/api/analyze-slide/route';
import { POST as cleanBackground } from '@/app/api/clean-background/route';
import { appendJobEvent, jobDir, readJob, writeJob } from '@/lib/job-store';
import { buildPageManifest } from '@/lib/page-manifest';
import { assertRuntimePolicy } from '@/lib/skill-merge-runtime';

const active = new Map<string, Promise<void>>();
const execFile = promisify(execFileCallback);
const pythonImageType = (path: string) => extname(path).toLowerCase() === '.png' ? 'image/png' : 'image/jpeg';
const python = () => {
  return process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python';
};

async function saveDataUrl(dataUrl: string, path: string) {
  const comma = dataUrl.indexOf(',');
  if (!dataUrl.startsWith('data:image/') || comma < 0) throw new Error('模型未返回有效图片数据。');
  await writeFile(path, Buffer.from(dataUrl.slice(comma + 1), 'base64'));
}

async function runBackgroundWithLocalRetry(pageDir: string, sourcePath: string, inventoryPath: string) {
  const output = join(pageDir, 'bg-clean-candidate.png');
  const mask = join(pageDir, 'foreground-mask.png');
  const reportPath = join(pageDir, 'background-build-report.json');
  const script = join(process.cwd(), 'tools', 'vision', 'build_clean_background.py');
  await execFile(python(), [script, '--source', sourcePath, '--inventory', inventoryPath, '--output', output, '--mask-output', mask, '--report', reportPath], { timeout: 180_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
  const [cleanBytes, maskBytes, reportText] = await Promise.all([readFile(output), readFile(mask), readFile(reportPath, 'utf8')]);
  return {
    cleanBackground: `data:image/png;base64,${cleanBytes.toString('base64')}`,
    removalMask: `data:image/png;base64,${maskBytes.toString('base64')}`,
    backgroundBuildReport: JSON.parse(reportText),
    status: 'candidate_requires_audit',
    exportReady: false,
    fallback: 'direct_local_build_after_api_failure',
  } as Record<string, unknown>;
}

async function runCandidateReview(pageDir: string, sourceReference: string, elements: unknown[]) {
  const sourceDir = join(pageDir, 'candidate-source');
  const ocrDir = join(pageDir, 'ocr');
  const outputDir = join(pageDir, 'candidate-review');
  await Promise.all([mkdir(sourceDir, { recursive: true }), mkdir(ocrDir, { recursive: true }), mkdir(outputDir, { recursive: true })]);
  const sourcePath = join(sourceDir, 'source.png');
  await saveDataUrl(sourceReference, sourcePath);
  const lines = elements.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object')).filter((item) => item.kind === 'text').map((item, index) => ({ id: String(item.id || `ocr-${index + 1}`), text: String(item.text || ''), bbox: Array.isArray(item.sourceBBox) ? item.sourceBBox : [item.x || 0, item.y || 0, item.w || 1, item.h || 1] }));
  const ocrPath = join(ocrDir, 'source.ocr.v1.json');
  await writeFile(ocrPath, JSON.stringify({ schema: 'qwen3.5-ocr/v1', source: sourcePath, coordinate_contract: '[x,y,w,h] source pixels', lines }, null, 2), 'utf8');
  const script = join(process.cwd(), 'tools', 'run_candidate_grounded_batch.py');
  try {
    const { stdout, stderr } = await execFile(python(), [script, '--source-dir', sourceDir, '--ocr-dir', ocrDir, '--output-dir', outputDir, '--workers', '1', '--max-candidates', '48'], { timeout: 900_000, maxBuffer: 8_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    const manifest = JSON.parse(await readFile(join(outputDir, 'manifest.json'), 'utf8'));
    return { status: manifest.passed === true ? 'passed' : 'needs_review', manifest, stdout: stdout.slice(-2000), stderr: stderr.slice(-2000) };
  } catch (error) {
    return { status: 'needs_review', error: error instanceof Error ? error.message : '候选核验失败。' };
  }
}

async function runGalleryRoute(pageDir: string) {
  const galleryDir = join(pageDir, 'gallery-query');
  await mkdir(galleryDir, { recursive: true });
  const reviewDir = join(pageDir, 'candidate-review');
  const source = join(pageDir, 'candidate-source', 'source.png');
  const queryScript = join(process.cwd(), 'tools', 'prepare_candidate_gallery_queries.py');
  const rankScript = join(process.cwd(), 'tools', 'gallery_visual_rank.py');
  const rerankScript = join(process.cwd(), 'tools', 'qwen_gallery_rerank.py');
  const query = join(galleryDir, 'source.gallery-query.json');
  const matches = join(galleryDir, 'source.matches.json');
  const rerank = join(galleryDir, 'source.rerank.json');
  try {
    await execFile(python(), [queryScript, '--review-dir', reviewDir, '--output-dir', galleryDir], { timeout: 60_000, maxBuffer: 4_000_000, windowsHide: true });
    await execFile(python(), [rankScript, '--source', source, '--audit', query, '--repository', join(process.cwd(), 'Image repository'), '--output', matches, '--top-k', '5'], { timeout: 120_000, maxBuffer: 4_000_000, windowsHide: true });
    await execFile(python(), [rerankScript, '--source', source, '--audit', query, '--matches', matches, '--output', rerank, '--max-assets', '24'], { timeout: 480_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    return { status: 'passed', query, matches, rerank, payload: JSON.parse(await readFile(rerank, 'utf8')) };
  } catch (error) {
    return { status: 'needs_review', query, matches, rerank, error: error instanceof Error ? error.message : '图库路由失败。' };
  }
}

async function runQwenImageFallback(pageDir: string, sourcePath: string, candidateReview: { status: string }) {
  if (candidateReview.status !== 'passed') return { status: 'needs_review', generated: [], error: '候选核验未通过，禁止生成图像资产。' };
  const review = JSON.parse(await readFile(join(pageDir, 'candidate-review', 'source.candidate-review.json'), 'utf8')) as { reviews?: Array<Record<string, unknown>> };
  const rerankPath = join(pageDir, 'gallery-query', 'source.rerank.json');
  const rerank = JSON.parse(await readFile(rerankPath, 'utf8').catch(() => '{}')) as { selections?: Array<Record<string, unknown>> };
  const approvedGallery = new Set((rerank.selections || []).filter((row) => row.approved === true).map((row) => String(row.elementId || '')));
  const candidates = (review.reviews || []).filter((row) => row.status === 'verified' && ['qwen_image_asset', 'decorative_movable'].includes(String(row.classification || '')) && !approvedGallery.has(String(row.candidateId || ''))).sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0)).slice(0, Number(process.env.SKILL_MERGE_QWEN_IMAGE_MAX_ASSETS || 6));
  const outputDir = join(pageDir, 'generated-assets'); await mkdir(outputDir, { recursive: true });
  const generated: Record<string, unknown>[] = [];
  for (const candidate of candidates) {
    const id = String(candidate.candidateId || `generated-${generated.length + 1}`);
    const bbox = Array.isArray(candidate.sourceBBox) ? candidate.sourceBBox.map(Number) : [0, 0, 1, 1];
    const output = join(outputDir, `${id}.png`);
    try {
      await execFile(python(), [join(process.cwd(), 'tools', 'qwen_image_local_asset.py'), '--source', sourcePath, '--bbox', bbox.join(','), '--semantic', String(candidate.semantic || candidate.classification || 'source-faithful visual asset'), '--output', output], { timeout: 420_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
      generated.push({ id, sourceBBox: bbox, semantic: String(candidate.semantic || ''), generatedPath: output, candidateClassification: candidate.classification });
    } catch (error) {
      generated.push({ id, sourceBBox: bbox, semantic: String(candidate.semantic || ''), generatedPath: output, status: 'generation_failed', error: error instanceof Error ? error.message : 'Qwen Image generation failed' });
    }
  }
  const generatedOk = generated.filter((row) => typeof row.generatedPath === 'string' && row.status !== 'generation_failed' && row.generatedPath);
  let audit: Record<string, unknown> | undefined;
  if (generatedOk.length) {
    const plan = join(outputDir, 'generation-plan.json'); const auditPath = join(outputDir, 'asset-audit.json');
    await writeFile(plan, JSON.stringify({ schema: 'qwen-image-generation-plan/v1', source: sourcePath, assets: generatedOk }, null, 2), 'utf8');
    try {
      await execFile(python(), [join(process.cwd(), 'tools', 'qwen_image_asset_audit.py'), '--source', sourcePath, '--plan', plan, '--output', auditPath], { timeout: 420_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
      audit = JSON.parse(await readFile(auditPath, 'utf8')) as Record<string, unknown>;
    } catch (error) {
      audit = { schema: 'qwen-image-asset-audit/v1', passed: false, error: error instanceof Error ? error.message : 'Qwen Image asset audit failed', reviews: [] };
    }
  }
  return { status: audit?.passed === true ? 'passed' : generated.length ? 'needs_review' : 'not_required', generated, audit };
}

function candidateMaskElements(analysis: Record<string, unknown>, candidateReview: { status: string; manifest?: Record<string, unknown> }) {
  return Array.isArray(analysis.elements) ? [...analysis.elements] : [];
}

async function buildBackgroundMaskElements(pageDir: string, analysis: Record<string, unknown>, candidateReview: { status: string }) {
  const elements = candidateMaskElements(analysis, candidateReview).filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'));
  if (candidateReview.status !== 'passed') return elements;
  const review = JSON.parse(await readFile(join(pageDir, 'candidate-review', 'source.candidate-review.json'), 'utf8')) as { reviews?: Array<Record<string, unknown>> };
  const overlap = (a: number[], b: number[]) => { const x = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0])); const y = Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1])); return x * y / Math.max(1, Math.min(a[2] * a[3], b[2] * b[3])); };
  for (const row of review.reviews || []) {
    if (row.status !== 'verified' || !Array.isArray(row.sourceBBox)) continue;
    const bbox = row.sourceBBox.map(Number); const hasOwner = elements.some((item) => { const owned = Array.isArray(item.sourceBBox) ? item.sourceBBox.map(Number) : [Number(item.x || 0), Number(item.y || 0), Number(item.w || 1), Number(item.h || 1)]; return overlap(owned, bbox) > .55; });
    if (hasOwner) continue;
    const classification = String(row.classification || 'qwen_image_asset');
    elements.push({ id: `candidate-mask-${String(row.candidateId || elements.length)}`, kind: 'image', name: String(row.semantic || classification), role: 'asset', reconstructionClass: classification === 'decorative_fixed' ? 'decorative_fixed' : classification === 'decorative_movable' ? 'decorative_movable' : classification === 'native_editable' ? 'native_editable' : 'qwen_image_asset', assetKind: 'qwen_image', x: bbox[0], y: bbox[1], w: bbox[2], h: bbox[3], sourceBBox: bbox, zIndex: 400, placementConfidence: Number(row.confidence || .8), parentId: null, semanticImpact: classification === 'decorative_fixed' ? false : true });
  }
  return elements;
}

async function applyGeneratedAssets(pageDir: string, analysis: Record<string, unknown>, imageRoute: { audit?: Record<string, unknown> }) {
  const reviews = Array.isArray(imageRoute.audit?.reviews) ? imageRoute.audit?.reviews : [];
  const elements = Array.isArray(analysis.elements) ? analysis.elements.map((item) => item && typeof item === 'object' ? { ...(item as Record<string, unknown>) } : item) : [];
  const applied: Record<string, unknown>[] = [];
  for (const raw of reviews) {
    if (!raw || typeof raw !== 'object' || (raw as Record<string, unknown>).pass !== true) continue;
    const row = raw as Record<string, unknown>; const generatedPath = String(row.generatedPath || '');
    if (!generatedPath) continue;
    const bbox = Array.isArray(row.sourceBBox) ? row.sourceBBox.map(Number) : [0, 0, 1, 1];
    const id = String(row.id || `qwen-image-${applied.length + 1}`);
    elements.push({ id, kind: 'image', name: String(row.semantic || 'Qwen Image local asset'), role: 'icon', reconstructionClass: 'qwen_image_asset', assetKind: 'qwen_image', assetSource: `data:image/png;base64,${(await readFile(generatedPath)).toString('base64')}`, x: bbox[0], y: bbox[1], w: bbox[2], h: bbox[3], sourceBBox: bbox, zIndex: 900 + applied.length, placementConfidence: 0.9, parentId: null, semanticImpact: false, qaStatus: 'passed', gallerySimilarity: Number(row.visualSimilarity || 0) });
    applied.push({ id, sourceBBox: bbox, generatedPath, visualSimilarity: row.visualSimilarity, semantic: row.semantic });
  }
  return { analysis: { ...analysis, elements }, applied };
}

async function applyGalleryAssets(pageDir: string, analysis: Record<string, unknown>, gallery: { status: string; payload?: Record<string, unknown> }) {
  const payload = gallery.payload;
  const selections = Array.isArray(payload?.selections) ? payload.selections : [];
  if (!selections.length) return { analysis, applied: [], status: gallery.status };
  const reviewPath = join(pageDir, 'candidate-review', 'source.candidate-review.json');
  const review = JSON.parse(await readFile(reviewPath, 'utf8')) as { reviews?: Array<Record<string, unknown>> };
  const byCandidate = new Map((review.reviews || []).map((item) => [String(item.candidateId || ''), item]));
  const elements = Array.isArray(analysis.elements) ? analysis.elements.map((item) => item && typeof item === 'object' ? { ...(item as Record<string, unknown>) } : item) : [];
  const usedElementIds = new Set<string>();
  const assetsDir = join(pageDir, 'assets'); await mkdir(assetsDir, { recursive: true });
  const applied: Record<string, unknown>[] = [];
  for (const raw of selections) {
    if (!raw || typeof raw !== 'object' || (raw as Record<string, unknown>).approved !== true) continue;
    const selection = raw as Record<string, unknown>;
    const candidate = selection.candidate && typeof selection.candidate === 'object' ? selection.candidate as Record<string, unknown> : null;
    const reviewItem = byCandidate.get(String(selection.elementId || ''));
    const category = String(candidate?.category || '');
    const assetPng = String(candidate?.assetPng || '');
    const repositoryPath = resolve(process.cwd(), 'Image repository', category, assetPng);
    const repositoryRoot = resolve(process.cwd(), 'Image repository');
    if (!candidate || !reviewItem || !assetPng || !repositoryPath.startsWith(`${repositoryRoot}\\`) || !repositoryPath.toLowerCase().endsWith('.png')) continue;
    const assetId = String(selection.elementId || `asset-${applied.length + 1}`);
    const localAsset = join(assetsDir, `${assetId}.png`);
    await copyFile(repositoryPath, localAsset);
    const sourceBBox = Array.isArray(reviewItem.sourceBBox) ? reviewItem.sourceBBox.map(Number) : [0, 0, 1, 1];
    const sx = Number(sourceBBox[0]) + Number(sourceBBox[2]) / 2; const sy = Number(sourceBBox[1]) + Number(sourceBBox[3]) / 2;
    let bestIndex = -1; let bestDistance = Number.POSITIVE_INFINITY;
    elements.forEach((item, index) => {
      if (!item || typeof item !== 'object') return;
      const value = item as Record<string, unknown>; const kind = String(value.kind || ''); const id = String(value.id || '');
      if (!['icon', 'image'].includes(kind) || usedElementIds.has(id)) return;
      const bbox = Array.isArray(value.sourceBBox) ? value.sourceBBox.map(Number) : [Number(value.x || 0), Number(value.y || 0), Number(value.w || 1), Number(value.h || 1)];
      const distance = Math.hypot(Number(bbox[0]) + Number(bbox[2]) / 2 - sx, Number(bbox[1]) + Number(bbox[3]) / 2 - sy);
      if (distance < bestDistance) { bestDistance = distance; bestIndex = index; }
    });
    if (bestIndex >= 0) {
      const target = elements[bestIndex] as Record<string, unknown>;
      target.assetSource = `data:image/png;base64,${(await readFile(localAsset)).toString('base64')}`;
      target.assetKind = String(candidate.actualAssetKind || 'png');
      target.galleryAssetId = String(candidate.id || assetId);
      target.gallerySimilarity = Number(selection.visualSimilarity || candidate.score || 0);
      target.reconstructionClass = target.reconstructionClass || 'library_png';
      target.qaStatus = 'passed';
      usedElementIds.add(String(target.id || bestIndex));
    }
    applied.push({ elementId: assetId, sourceBBox, localAsset, repository: repositoryPath, visualSimilarity: selection.visualSimilarity, candidateId: candidate.id, assetKind: candidate.actualAssetKind || 'png' });
  }
  return { analysis: { ...analysis, elements }, applied, status: gallery.status };
}

async function runJob(id: string) {
  const job = await readJob(id);
  assertRuntimePolicy();
  job.status = 'running';
  await writeJob(job);
  await appendJobEvent(id, { type: 'stage', message: '严格高保真任务开始：逐页执行 OCR、结构识别和背景候选生成。' });
  try {
    for (const page of job.pages) {
      const pageDir = join(jobDir(id), 'pages', page.id);
      if (page.status === 'needs_review' && page.manifestPath) {
        await appendJobEvent(id, { type: 'stage', pageId: page.id, message: `${page.originalName}：已有完整候选，断点续跑跳过重新调用模型。` });
        continue;
      }
      await mkdir(pageDir, { recursive: true });
      const processingStartedAt = new Date().toISOString();
      const processingStartedMs = Date.now();
      page.metrics = { ...(page.metrics || {}), processingStartedAt };
      page.status = 'ocr';
      await writeJob(job);
      await appendJobEvent(id, { type: 'page', pageId: page.id, message: `${page.originalName}：OCR 与视觉清单处理中。` });
      const sourceBytes = await readFile(page.inputPath);
      const sourceData = `data:${pythonImageType(page.inputPath)};base64,${sourceBytes.toString('base64')}`;
      const sourceLocalPath = join(pageDir, `source${extname(page.inputPath).toLowerCase()}`);
      await copyFile(page.inputPath, sourceLocalPath);
      const analysisResponse = await analyzeSlide(new Request('http://skill-merge.local/api/analyze-slide', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ image: sourceData }) }));
      let analysis = await analysisResponse.json() as Record<string, unknown>;
      if (!analysisResponse.ok || !Array.isArray(analysis.elements)) throw new Error(String(analysis.error || `分析接口失败（${analysisResponse.status}）。`));
      await writeFile(join(pageDir, 'analysis.json'), JSON.stringify(analysis, null, 2), 'utf8');
      page.status = 'layout';
      await writeJob(job);
      await appendJobEvent(id, { type: 'page', pageId: page.id, message: `${page.originalName}：OCR 清单和结构清单已生成。`, data: { ocrTextCount: analysis.ocrTextCount, elementCount: (analysis.elements as unknown[]).length } });
      page.status = 'assets';
      await writeJob(job);
      const candidateReview = await runCandidateReview(pageDir, String(analysis.sourceReference || sourceData), analysis.elements);
      await writeFile(join(pageDir, 'candidate-review-summary.json'), JSON.stringify(candidateReview, null, 2), 'utf8');
      await appendJobEvent(id, { type: 'page', pageId: page.id, message: `${page.originalName}：候选检测和 Qwen-VL 局部核验已结束。`, data: { candidateStatus: candidateReview.status } });
      const gallery = candidateReview.status === 'passed' ? await runGalleryRoute(pageDir) : { status: 'needs_review', error: '候选核验未通过，图库路由被阻断。' };
      const routed = await applyGalleryAssets(pageDir, analysis, gallery);
      analysis = routed.analysis as Record<string, unknown>;
      const imageRoute = await runQwenImageFallback(pageDir, join(pageDir, 'candidate-source', 'source.png'), candidateReview);
      const generatedRouted = imageRoute.audit?.passed === true ? await applyGeneratedAssets(pageDir, analysis, imageRoute) : { analysis, applied: [] };
      analysis = generatedRouted.analysis as Record<string, unknown>;
      await writeFile(join(pageDir, 'analysis.json'), JSON.stringify(analysis, null, 2), 'utf8');
      await writeFile(join(pageDir, 'asset-route.json'), JSON.stringify({ gallery, galleryApplied: routed.applied, qwenImage: imageRoute, qwenImageApplied: generatedRouted.applied }, null, 2), 'utf8');
      await appendJobEvent(id, { type: 'page', pageId: page.id, message: `${page.originalName}：素材库、Qwen Image 和资产级 Qwen-VL 复核已结束。`, data: { galleryStatus: routed.status, appliedAssets: routed.applied.length + generatedRouted.applied.length, qwenImageStatus: imageRoute.status } });
      const maskElements = await buildBackgroundMaskElements(pageDir, analysis, candidateReview);
      const maskInventoryPath = join(pageDir, 'background-inventory.json');
      await writeFile(maskInventoryPath, JSON.stringify({ schema: 'pptx-foreground-inventory/v3', elements: maskElements }, null, 2), 'utf8');
      const maskSourcePath = join(pageDir, 'candidate-source', 'source.png');
      let clean: Record<string, unknown>;
      try {
        const cleanResponse = await cleanBackground(new Request('http://skill-merge.local/api/clean-background', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ image: analysis.sourceReference, elements: maskElements, sourceWidth: job.sourceWidth, sourceHeight: job.sourceHeight }) }));
        clean = await cleanResponse.json() as Record<string, unknown>;
        if (!cleanResponse.ok || typeof clean.cleanBackground !== 'string') throw new Error(String(clean.error || `背景候选失败（${cleanResponse.status}）。`));
      } catch (error) {
        await appendJobEvent(id, { type: 'warning', pageId: page.id, message: `${page.originalName}：BG_CLEAN API 子进程异常，改用同一脚本的本地确定性重试。`, data: { error: error instanceof Error ? error.message : String(error) } });
        const retryInventory = join(pageDir, 'background-inventory.json');
        clean = await runBackgroundWithLocalRetry(pageDir, maskSourcePath, retryInventory);
      }
      await saveDataUrl(String(clean.cleanBackground || ''), join(pageDir, 'bg-clean-candidate.png'));
      if (typeof clean.removalMask === 'string') await saveDataUrl(clean.removalMask, join(pageDir, 'foreground-mask.png'));
      await writeFile(join(pageDir, 'background-report.json'), JSON.stringify(clean, null, 2), 'utf8');
      let backgroundAudit: Record<string, unknown> = { status: 'not_run' };
      try {
        const auditPath = join(pageDir, 'background-audit.json');
        await execFile(python(), [join(process.cwd(), 'tools', 'vision', 'audit_clean_background.py'), '--source', sourceLocalPath, '--clean', join(pageDir, 'bg-clean-candidate.png'), '--inventory', maskInventoryPath, '--output', auditPath], { timeout: 180_000, maxBuffer: 4_000_000, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
        backgroundAudit = JSON.parse(await readFile(auditPath, 'utf8')) as Record<string, unknown>;
      } catch (error) { backgroundAudit = { status: 'needs_review', passed: false, error: error instanceof Error ? error.message : '背景审计失败' }; }
      const manifest = buildPageManifest(page, analysis, { source: sourceLocalPath, bgCandidate: join(pageDir, 'bg-clean-candidate.png'), mask: join(pageDir, 'foreground-mask.png') });
      await writeFile(join(pageDir, 'manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');
      page.status = 'needs_review';
      page.error = '已生成 OCR、结构和 BG_CLEAN 候选，等待独立素材、渲染、移动/删除可编辑性门禁。';
      page.manifestPath = join(pageDir, 'manifest.json');
      page.metrics = { ...(page.metrics || {}), ocrTextCount: Number(analysis.ocrTextCount || 0), elementCount: (analysis.elements as unknown[]).length, candidateStatus: candidateReview.status, backgroundStatus: String(clean.status || 'candidate_requires_audit'), backgroundAuditPassed: backgroundAudit.passed === true, processingCompletedAt: new Date().toISOString(), processingDurationMs: Date.now() - processingStartedMs };
      await writeJob(job);
      await appendJobEvent(id, { type: 'warning', pageId: page.id, message: page.error });
    }
    job.status = 'needs_review';
    job.error = '严格质量门禁尚未全部通过，当前任务不可直接交付。';
    await writeJob(job);
    await appendJobEvent(id, { type: 'completed', message: '批量分析阶段完成；任务已进入逐页复核，尚未发布 PPTX。' });
  } catch (error) {
    job.status = 'failed';
    job.error = error instanceof Error ? error.message : '任务执行失败。';
    await writeJob(job);
    await appendJobEvent(id, { type: 'failed', message: job.error });
  } finally {
    active.delete(id);
  }
}

export function launchJob(id: string) {
  if (active.has(id)) return;
  const promise = runJob(id);
  active.set(id, promise);
  void promise;
}
