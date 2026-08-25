#!/usr/bin/env node
import { execFile as execFileCallback } from 'node:child_process';
import { promisify } from 'node:util';
import { resolve } from 'node:path';

const execFile = promisify(execFileCallback);
const text = '新能源渗透率持续提升，市场主流地位形成（2026年1-6月）';
const element = { id: 'utf8-check', kind: 'text', name: '中文回环测试', x: 80, y: 90, w: 680, h: 42, text, sourceText: text, color: '#1A6AC9' };
const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };

for (const script of ['style_ocr_lines.py', 'refine_text_boxes.py']) {
  const { stdout } = await execFile('python', [resolve('tools/vision', script), `--image=${resolve('public/samples/party-report.png')}`, `--elements-json=${JSON.stringify([element])}`], { env, windowsHide: true });
  const result = JSON.parse(stdout);
  if (result[0]?.text !== text || result[0]?.name !== element.name) throw new Error(`${script}: UTF-8 roundtrip mismatch`);
}

console.log('UTF-8 Node → Python → Node roundtrip passed: Chinese text is byte-safe.');
