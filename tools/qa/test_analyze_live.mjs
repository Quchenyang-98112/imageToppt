import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { basename, extname, join } from 'node:path';

const input = process.argv[2];
if (!input) throw new Error('Usage: node tools/qa/test_analyze_live.mjs <image> [output-dir]');
const outputDir = process.argv[3] || join(process.cwd(), 'output', 'live-analysis');
await mkdir(outputDir, { recursive: true });
const extension = extname(input).toLowerCase();
const mime = extension === '.png' ? 'image/png' : extension === '.webp' ? 'image/webp' : 'image/jpeg';
const image = `data:${mime};base64,${(await readFile(input)).toString('base64')}`;
const response = await fetch('http://127.0.0.1:3105/api/analyze-slide', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ image }),
});
const result = await response.json();
if (!response.ok) throw new Error(`Analyze failed (${response.status}): ${result.error || JSON.stringify(result)}`);
const stem = basename(input, extension);
const clean = typeof result.cleanBackground === 'string' ? result.cleanBackground : '';
if (clean.startsWith('data:image/')) {
  await writeFile(join(outputDir, `${stem}-clean.png`), Buffer.from(clean.slice(clean.indexOf(',') + 1), 'base64'));
}
const summary = {
  input,
  model: result.model,
  ocrModel: result.ocrModel,
  mode: result.mode,
  textCount: result.elements?.filter((item) => item.kind === 'text').length || 0,
  quality: result.protocol?.quality,
  cleanupQuality: result.cleanupQuality,
  damagedTextCount: result.elements?.filter((item) => /\uFFFD|锟|�/.test(item.text || '')).length || 0,
  sample: result.elements?.slice(0, 8).map(({ text, x, y, w, h, fontSize, color }) => ({ text, x, y, w, h, fontSize, color })),
};
const analysisForQa = { ...result, cleanBackground: clean ? `[saved as ${stem}-clean.png]` : undefined };
await writeFile(join(outputDir, `${stem}-analysis.json`), JSON.stringify(analysisForQa, null, 2), 'utf8');
await writeFile(join(outputDir, `${stem}-summary.json`), JSON.stringify(summary, null, 2), 'utf8');
console.log(JSON.stringify(summary, null, 2));
