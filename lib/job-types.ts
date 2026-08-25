import type { JobStatus, PageStatus } from '@/lib/skill-merge-runtime';

export type JobEvent = {
  at: string;
  type: 'created' | 'stage' | 'page' | 'warning' | 'completed' | 'failed' | 'cancelled';
  message: string;
  pageId?: string;
  data?: Record<string, unknown>;
};

export type PageRecord = {
  id: string;
  index: number;
  originalName: string;
  inputPath: string;
  sourcePath?: string;
  status: PageStatus;
  error?: string;
  manifestPath?: string;
  previewPath?: string;
  pptxPath?: string;
  metrics?: Record<string, number | string | boolean>;
};

export type JobRecord = {
  schema: 'skill-merge-job/v1';
  id: string;
  createdAt: string;
  updatedAt: string;
  status: JobStatus;
  mode: 'strict_high_fidelity';
  sourceWidth: number;
  sourceHeight: number;
  models: { ocr: string; vision: string; image: string; provider: string };
  pages: PageRecord[];
  candidatePath?: string;
  outputPath?: string;
  error?: string;
  warnings: string[];
  eventsFile: string;
};
