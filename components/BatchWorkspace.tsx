'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

type PageRecord = { id: string; index: number; originalName: string; status: string; error?: string; previewPath?: string; metrics?: Record<string, unknown> };
type JobRecord = { id: string; status: string; pages: PageRecord[]; error?: string; candidatePath?: string; outputPath?: string; models?: { ocr: string; vision: string; image: string } };

const statusText: Record<string, string> = { uploaded: '已上传', queued: '排队中', running: '处理中', ocr: 'OCR', layout: '布局识别', assets: '素材执行', building: '编译 PPTX', render_qa: 'PowerPoint 渲染 QA', passed: '已通过', needs_review: '待复核', failed: '失败', completed: '完成' };
const metricNumber = (page: PageRecord, key: string) => typeof page.metrics?.[key] === 'number' && Number.isFinite(page.metrics[key] as number) ? page.metrics[key] as number : null;
const formatDuration = (milliseconds: number | null) => milliseconds === null ? '处理中' : milliseconds < 1000 ? `${milliseconds} ms` : milliseconds < 60_000 ? `${(milliseconds / 1000).toFixed(1)} s` : `${Math.floor(milliseconds / 60_000)}m ${Math.round((milliseconds % 60_000) / 1000)}s`;
const scoreColor = (score: number | null) => score === null ? '#7c8ba0' : score >= 85 ? '#16794a' : score >= 70 ? '#a36a00' : '#bd3b45';

export function BatchWorkspace() {
  const [files, setFiles] = useState<File[]>([]);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [message, setMessage] = useState('请选择一批 PNG/JPG 原图。系统会保留逐页质量分数和耗时，候选 PPTX 无论是否通过门禁都可以导出。');
  const objectUrls = useRef<string[]>([]);
  const previews = useMemo(() => files.map((file) => { const url = URL.createObjectURL(file); objectUrls.current.push(url); return url; }), [files]);

  useEffect(() => () => { objectUrls.current.forEach((url) => URL.revokeObjectURL(url)); }, []);
  useEffect(() => {
    if (!job || ['completed', 'failed', 'needs_review', 'cancelled'].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/jobs/${job.id}`);
      if (!response.ok) return;
      const payload = await response.json() as { job?: JobRecord };
      if (payload.job) setJob(payload.job);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job]);

  function onFiles(event: React.ChangeEvent<HTMLInputElement>) {
    const next = Array.from(event.target.files ?? []).filter((file) => ['image/png', 'image/jpeg'].includes(file.type));
    setFiles(next); setJob(null);
    setMessage(next.length ? `已选择 ${next.length} 张图片，点击“开始严格重建”。` : '请选择 PNG/JPG 原图。');
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= files.length) return;
    const next = [...files]; [next[index], next[target]] = [next[target], next[index]]; setFiles(next);
  }

  async function submit() {
    if (!files.length) return;
    setBusy(true); setMessage('正在创建批处理任务…');
    try {
      const form = new FormData(); files.forEach((file) => form.append('files', file, file.name));
      const response = await fetch('/api/jobs', { method: 'POST', body: form });
      const payload = await response.json() as { job?: JobRecord; error?: string };
      if (!response.ok || !payload.job) throw new Error(payload.error || '任务创建失败。');
      setJob(payload.job); setMessage('任务已进入 Worker；页面会自动刷新逐页状态、质量指标和耗时。');
    } catch (error) { setMessage(error instanceof Error ? error.message : '任务创建失败。'); }
    finally { setBusy(false); }
  }

  async function compileCandidate() {
    if (!job) return;
    setCompiling(true); setMessage('正在编译候选 PPTX，并调用本机 PowerPoint 渲染和 Qwen-VL 独立复核…');
    try {
      const response = await fetch(`/api/jobs/${job.id}/compile`, { method: 'POST' });
      const payload = await response.json() as { job?: JobRecord; error?: string };
      if (!response.ok || !payload.job) throw new Error(payload.error || '候选 PPTX 编译失败。');
      setJob(payload.job); setMessage('候选 PPTX 已生成。质量门禁只负责标记结果，不阻止你导出当前候选。');
    } catch (error) { setMessage(error instanceof Error ? error.message : '候选 PPTX 编译失败。'); }
    finally { setCompiling(false); }
  }

  const pageCount = job?.pages.length ?? files.length;
  const doneCount = job?.pages.filter((page) => ['passed', 'needs_review', 'failed'].includes(page.status)).length ?? 0;
  const scoredPages = job?.pages.map((page) => metricNumber(page, 'qualityScore')).filter((score): score is number => score !== null) ?? [];
  const overallScore = scoredPages.length ? Math.round(scoredPages.reduce((sum, score) => sum + score, 0) / scoredPages.length) : null;
  const candidateAvailable = Boolean(job && (job.candidatePath || job.pages.some((page) => page.previewPath)));

  return <main style={{ minHeight: '100vh', background: '#f4f7fb', color: '#172033', padding: '34px 5vw' }}>
    <section style={{ maxWidth: 1180, margin: '0 auto' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', gap: 20, alignItems: 'flex-end', marginBottom: 28 }}>
        <div><div style={{ color: '#276ef1', fontWeight: 800, letterSpacing: 2, fontSize: 12 }}>SKILL-MERGE · STRICT HIGH FIDELITY</div><h1 style={{ margin: '8px 0 6px', fontSize: 32 }}>图片批量逆向编译为可编辑 PPTX</h1><p style={{ margin: 0, color: '#68778a' }}>Qwen OCR + Qwen-VL + Qwen Image；质量门禁展示结果，但候选 PPTX 始终允许导出。</p></div>
        <label style={{ flex: '0 0 auto', padding: '12px 18px', borderRadius: 8, background: '#276ef1', color: '#fff', fontWeight: 700, cursor: 'pointer' }}><input type="file" accept="image/png,image/jpeg" multiple onChange={onFiles} style={{ display: 'none' }} />选择原图</label>
      </header>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 330px', gap: 20, alignItems: 'start' }}>
        <section style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 12, padding: 20, boxShadow: '0 8px 24px rgba(26,42,68,.05)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}><h2 style={{ margin: 0, fontSize: 18 }}>页面顺序与逐页指标</h2><span style={{ color: '#8a97a8', fontSize: 12 }}>{pageCount} 页</span></div>
          {!files.length && !job && <div style={{ border: '1.5px dashed #b7c6dc', borderRadius: 10, padding: '56px 20px', textAlign: 'center', color: '#7c8ba0' }}>上传多张 PNG/JPG 后，这里会显示缩略图和处理顺序。</div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(170px,1fr))', gap: 14 }}>{files.map((file, index) => <div key={`${file.name}-${file.lastModified}`} style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#fbfdff' }}><img src={previews[index]} alt={file.name} style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover', display: 'block' }} /><div style={{ padding: 9 }}><div style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{index + 1}. {file.name}</div><div style={{ display: 'flex', gap: 5, marginTop: 8 }}><button onClick={() => move(index, -1)} disabled={index === 0} style={{ border: '1px solid #dbe3ee', background: '#fff', borderRadius: 4 }}>↑</button><button onClick={() => move(index, 1)} disabled={index === files.length - 1} style={{ border: '1px solid #dbe3ee', background: '#fff', borderRadius: 4 }}>↓</button></div></div></div>)}</div>
          {job && <div style={{ marginTop: 18, borderTop: '1px solid #edf0f4', paddingTop: 16 }}><div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 10 }}><strong>任务 {job.id.slice(0, 12)}</strong><span style={{ color: job.status === 'failed' ? '#c84048' : '#276ef1' }}>{statusText[job.status] || job.status} · {doneCount}/{pageCount}</span></div>{job.pages.map((page) => { const score = metricNumber(page, 'qualityScore'); const duration = metricNumber(page, 'processingDurationMs'); const bgPassed = page.metrics?.backgroundAuditPassed === true; return <div key={page.id} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto auto', gap: 12, alignItems: 'center', marginTop: 7, padding: '9px 10px', borderRadius: 6, background: '#f7f9fc', fontSize: 12 }}><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{page.index + 1}. {page.originalName}<br /><small style={{ color: page.status === 'failed' ? '#c84048' : '#56718e' }}>{statusText[page.status] || page.status} · BG_CLEAN {bgPassed ? '通过' : '待复核'}</small></span><span style={{ color: scoreColor(score), fontWeight: 700 }}>{score === null ? '—' : `${score}/100`}</span><span style={{ color: '#6b7c91', whiteSpace: 'nowrap' }}>{formatDuration(duration)}</span></div>; })}</div>}
        </section>
        <aside style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 12, padding: 20 }}><h2 style={{ margin: '0 0 14px', fontSize: 18 }}>质量与导出</h2><p style={{ color: '#68778a', fontSize: 13, lineHeight: 1.65 }}>{message}</p>{job && <div style={{ padding: 12, borderRadius: 8, background: '#f7f9fc', marginBottom: 14 }}><div style={{ fontSize: 12, color: '#6b7c91' }}>综合质量分数</div><div style={{ fontSize: 30, fontWeight: 800, color: scoreColor(overallScore), marginTop: 3 }}>{overallScore === null ? '—' : `${overallScore}/100`}</div><div style={{ fontSize: 11, color: '#7c8ba0', marginTop: 3 }}>由逐页 Qwen-VL 文字、非文本、融合和背景指标计算；不等同于发布门禁。</div></div>}<button onClick={submit} disabled={busy || !files.length || Boolean(job && !['failed', 'needs_review'].includes(job.status))} style={{ width: '100%', border: 0, borderRadius: 7, padding: 12, background: busy || !files.length ? '#a7bde9' : '#276ef1', color: '#fff', fontWeight: 700, cursor: busy || !files.length ? 'wait' : 'pointer' }}>{busy ? '创建任务…' : '开始严格重建'}</button>{job?.status === 'needs_review' && <button onClick={compileCandidate} disabled={compiling} style={{ width: '100%', marginTop: 10, border: '1px solid #276ef1', borderRadius: 7, padding: 11, background: '#fff', color: '#276ef1', fontWeight: 700, cursor: compiling ? 'wait' : 'pointer' }}>{compiling ? 'PowerPoint 渲染中…' : '编译并复核候选 PPTX'}</button>}{candidateAvailable && <a href={`/api/jobs/${job?.id}/download`} download style={{ display: 'block', marginTop: 12, textAlign: 'center', borderRadius: 7, padding: 11, background: '#e9f1ff', color: '#1458c0', fontWeight: 700 }}>导出当前候选 PPTX</a>}{job?.models && <div style={{ marginTop: 18, fontSize: 12, color: '#6b7c91', lineHeight: 1.8 }}><div><b>OCR</b>：{job.models.ocr}</div><div><b>视觉</b>：{job.models.vision}</div><div><b>图像</b>：{job.models.image}</div></div>}{job?.error && <div style={{ marginTop: 18, padding: 10, borderRadius: 6, background: '#fff4f4', color: '#b13b46', fontSize: 12, lineHeight: 1.5 }}>{job.error}</div>}</aside>
      </div>
    </section>
  </main>;
}
