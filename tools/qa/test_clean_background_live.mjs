import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { basename, extname, join } from 'node:path';

const input = process.argv[2];
if (!input) throw new Error('Usage: node tools/qa/test_clean_background_live.mjs <image> [output-dir]');
const outputDir = process.argv[3] || join(process.cwd(), 'output', 'model-clean');
await mkdir(outputDir, { recursive: true });
const extension = extname(input).toLowerCase();
const mime = extension === '.png' ? 'image/png' : extension === '.webp' ? 'image/webp' : 'image/jpeg';
const image = `data:${mime};base64,${(await readFile(input)).toString('base64')}`;
const response = await fetch('http://127.0.0.1:3105/api/clean-background', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image }),
});
const result = await response.json();
if (!response.ok) throw new Error(`Clean failed (${response.status}): ${result.error || JSON.stringify(result)}`);
const output = result.cleanBackground;
if (!output?.startsWith('data:image/')) throw new Error('No clean background returned.');
const stem = basename(input, extension);
const path = join(outputDir, `${stem}-model-clean.png`);
await writeFile(path, Buffer.from(output.slice(output.indexOf(',') + 1), 'base64'));
console.log(JSON.stringify({ input, model: result.model, output: path }, null, 2));
