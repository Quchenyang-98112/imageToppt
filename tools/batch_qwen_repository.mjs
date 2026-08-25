import fs from 'node:fs';
import path from 'node:path';

const sourceDir = process.argv[2] || 'C:/Users/LENOVO/Desktop/sources';
const outDir = process.argv[3] || 'output/sources-9-qwen-library';
const baseUrl = process.argv[4] || 'http://localhost:3010';
const names = ['b60b7e2a-2c8f-443d-9203-6a4a29e6f168.png', 'saas.png', '智慧养老.png', '李佳1.png', '李佳2.png', '李佳3.png', '识别1.png', '识别2.jpg', '识别3.png'];
const policy = JSON.parse(fs.readFileSync(path.resolve('config/qwen-global-policy.json'), 'utf8'));
if (policy.schema !== 'qwen-global-reconstruction-policy/v3') throw new Error('Global reconstruction policy v3 is required.');
fs.mkdirSync(outDir, { recursive: true });
fs.mkdirSync(path.join(outDir, 'analysis'), { recursive: true });
const rows = [];
for (const name of names) {
  const stem = path.basename(name, path.extname(name));
  const target = path.join(outDir, 'analysis', `${stem}.analysis.json`);
  const source = path.join(sourceDir, name);
  if (fs.existsSync(target)) {
    try {
      const cached = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (cached?.protocol?.schema === 'pptx-rebuild-protocol/v3') { rows.push({ source, output: target, status: 'reused-v3-inventory-only' }); continue; }
    } catch {}
  }
  const bytes = fs.readFileSync(source);
  const mime = path.extname(name).toLowerCase() === '.jpg' ? 'image/jpeg' : 'image/png';
  const started = Date.now();
  try {
    const response = await fetch(`${baseUrl}/api/analyze-slide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: `data:${mime};base64,${bytes.toString('base64')}` })
    });
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch {}
    if (response.ok && parsed?.protocol?.schema !== 'pptx-rebuild-protocol/v3') throw new Error('Analysis endpoint returned a non-v3 protocol.');
    fs.writeFileSync(target, text, 'utf8');
    rows.push({ source, output: target, status: response.ok ? 'completed' : 'failed', http: response.status, elapsed_ms: Date.now() - started, elements: parsed?.elements?.length ?? 0, mode: parsed?.mode || '', model: parsed?.model || '', ocrModel: parsed?.ocrModel || '', error: parsed?.error || '' });
  } catch (error) {
    rows.push({ source, output: target, status: 'failed', elapsed_ms: Date.now() - started, error: String(error) });
  }
  fs.writeFileSync(path.join(outDir, 'analysis', 'manifest.json'), JSON.stringify(rows, null, 2), 'utf8');
}
fs.writeFileSync(path.join(outDir, 'analysis', 'manifest.json'), JSON.stringify(rows, null, 2), 'utf8');
console.log(JSON.stringify(rows, null, 2));
