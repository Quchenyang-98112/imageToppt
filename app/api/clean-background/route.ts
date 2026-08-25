import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { guardApiSecret } from '@/lib/ai-config';
import type { CanvasElement } from '@/lib/types';
import { assertV3Element } from '@/lib/reconstruction-v3';

export const runtime = 'nodejs';
export const maxDuration = 600;

const execFile = promisify(execFileCallback);
const python = () => process.env.SKILL_MERGE_PYTHON_PATH?.trim() || 'python';
const pythonEnv = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };

export async function POST(request: Request) {
  const denied = guardApiSecret(request);
  if (denied) return denied;
  const folder = await mkdtemp(join(tmpdir(), 'ppt-bg-clean-v3-'));
  try {
    const body = await request.json() as { image?: unknown; elements?: unknown; sourceWidth?: unknown; sourceHeight?: unknown };
    const image = typeof body.image === 'string' ? body.image : '';
    if (!image.startsWith('data:image/') || image.length > 20_000_000) return Response.json({ error: '请提交 20 MB 以下的图片 Data URL。' }, { status: 400 });
    const elements = Array.isArray(body.elements) ? body.elements as CanvasElement[] : [];
    if (!elements.length) return Response.json({ error: 'BG_CLEAN 必须在 OCR 与非文本清单均完成后，使用联合前景掩膜生成。' }, { status: 422 });
    const sourceWidth = typeof body.sourceWidth === 'number' ? body.sourceWidth : 1600;
    const sourceHeight = typeof body.sourceHeight === 'number' ? body.sourceHeight : 900;
    for (const element of elements) assertV3Element(element, sourceWidth, sourceHeight);

    const source = join(folder, 'source.png');
    const inventory = join(folder, 'inventory.json');
    const clean = join(folder, 'bg-clean.png');
    const mask = join(folder, 'foreground-mask.png');
    const reportPath = join(folder, 'build-report.json');
    await Promise.all([
      writeFile(source, Buffer.from(image.slice(image.indexOf(',') + 1), 'base64')),
      writeFile(inventory, JSON.stringify({ schema: 'pptx-foreground-inventory/v3', elements }), 'utf8'),
    ]);
    const script = join(process.cwd(), 'tools', 'vision', 'build_clean_background.py');
    await execFile(python(), [script, '--source', source, '--inventory', inventory, '--output', clean, '--mask-output', mask, '--report', reportPath], {
      timeout: 180_000, maxBuffer: 4_000_000, windowsHide: true, env: pythonEnv,
    });
    const [cleanBytes, maskBytes, reportText] = await Promise.all([readFile(clean), readFile(mask), readFile(reportPath, 'utf8')]);
    const report = JSON.parse(reportText) as { status?: string; unresolvedRegionIds?: number[] };
    const unresolved = report.unresolvedRegionIds?.length ?? 0;
    return Response.json({
      cleanBackground: `data:image/png;base64,${cleanBytes.toString('base64')}`,
      removalMask: `data:image/png;base64,${maskBytes.toString('base64')}`,
      backgroundBuildReport: report,
      status: unresolved ? 'needs_qwen_masked_local_edit' : 'candidate_requires_audit',
      exportReady: false,
      nextRequiredChecks: ['OCR重新扫描为0', '文字/非文本/阴影残边检查', '掩膜外像素同一性', '固定装饰完整度', '接缝检查'],
      note: unresolved
        ? `有 ${unresolved} 个复杂局部必须仅在掩膜内调用 Qwen Image 修复；禁止整页重绘。`
        : '确定性清洁候选已生成；完成独立背景审计前禁止融合或导出。',
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : '生成 BG_CLEAN 候选失败。' }, { status: 422 });
  } finally {
    await rm(folder, { recursive: true, force: true }).catch(() => undefined);
  }
}
