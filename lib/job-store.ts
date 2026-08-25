import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { allowedImageTypes, extensionForMime, isSafeJobId, MAX_PAGES_PER_JOB, MAX_UPLOAD_BYTES, modelPolicy, runtimeRoot, SKILL_MERGE_SCHEMA, SOURCE_HEIGHT, SOURCE_WIDTH } from '@/lib/skill-merge-runtime';
import type { JobEvent, JobRecord, PageRecord } from '@/lib/job-types';

const now = () => new Date().toISOString();
const safeName = (name: string) => basename(name).replace(/[^\p{L}\p{N}._-]+/gu, '_').slice(0, 140) || 'slide';

export const jobDir = (id: string) => {
  if (!isSafeJobId(id)) throw new Error('非法任务 ID');
  return join(runtimeRoot(), 'jobs', id);
};
const manifestPath = (id: string) => join(jobDir(id), 'job.json');
const eventPath = (id: string) => join(jobDir(id), 'events.ndjson');

async function writeJsonAtomic(path: string, value: unknown) {
  await mkdir(join(path, '..'), { recursive: true });
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(value, null, 2), 'utf8');
  await rename(temporary, path);
}

export async function readJob(id: string): Promise<JobRecord> {
  return JSON.parse(await readFile(manifestPath(id), 'utf8')) as JobRecord;
}

export async function writeJob(job: JobRecord) {
  job.updatedAt = now();
  await writeJsonAtomic(manifestPath(job.id), job);
}

export async function appendJobEvent(id: string, event: Omit<JobEvent, 'at'>) {
  const full: JobEvent = { at: now(), ...event };
  await mkdir(jobDir(id), { recursive: true });
  await writeFile(eventPath(id), `${JSON.stringify(full)}\n`, { encoding: 'utf8', flag: 'a' });
}

export async function createJob(files: File[]) {
  if (!files.length) throw new Error('至少上传一张 PNG 或 JPG 图片。');
  if (files.length > MAX_PAGES_PER_JOB) throw new Error(`单个任务最多支持 ${MAX_PAGES_PER_JOB} 张图片。`);
  const id = randomUUID().replaceAll('-', '');
  const root = jobDir(id);
  await mkdir(join(root, 'input'), { recursive: true });
  const pages: PageRecord[] = [];
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    if (!allowedImageTypes.has(file.type)) throw new Error(`${file.name}: 只支持 PNG/JPG。`);
    if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) throw new Error(`${file.name}: 文件必须小于 25 MB。`);
    const bytes = Buffer.from(await file.arrayBuffer());
    const digest = createHash('sha256').update(bytes).digest('hex');
    const extension = extensionForMime(file.type);
    const pageId = `page-${String(index + 1).padStart(3, '0')}`;
    const inputPath = join(root, 'input', `${pageId}-${safeName(file.name).replace(/\.(png|jpe?g)$/i, '')}${extension}`);
    await writeFile(inputPath, bytes, { flag: 'wx' });
    pages.push({ id: pageId, index, originalName: file.name, inputPath, status: 'uploaded', metrics: { bytes: file.size, sha256: digest } });
  }
  const timestamp = now();
  const job: JobRecord = { schema: SKILL_MERGE_SCHEMA, id, createdAt: timestamp, updatedAt: timestamp, status: 'queued', mode: 'strict_high_fidelity', sourceWidth: SOURCE_WIDTH, sourceHeight: SOURCE_HEIGHT, models: { ocr: modelPolicy.ocr, vision: modelPolicy.vision, image: modelPolicy.image, provider: modelPolicy.provider }, pages, warnings: [], eventsFile: eventPath(id) };
  await writeJob(job);
  await appendJobEvent(id, { type: 'created', message: `已接收 ${pages.length} 张图片。` });
  return job;
}
