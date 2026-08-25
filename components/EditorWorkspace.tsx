'use client';

import { ChangeEvent, PointerEvent, useEffect, useMemo, useRef, useState } from 'react';
import { DEMO_IMAGE, DEMO_SOURCE_IMAGE, demoElements } from '@/lib/demo-data';
import type { CanvasElement, ElementKind } from '@/lib/types';
import type { RebuildProtocol } from '@/lib/rebuild-protocol';
import { hasTextCorruption } from '@/lib/text-integrity';

const BOARD = { width: 1600, height: 900 };
const cloneDemo = () => demoElements.map((element) => ({ ...element, sourceElement: false }));
const uid = () => `element-${Math.random().toString(36).slice(2, 9)}`;
const labels: Record<ElementKind, string> = { text: '文本', rectangle: '框图', ellipse: '圆形', arrow: '箭头', line: '线条', connector: '连接线', icon: '图标', image: '图片', table: '表格', freeform: '自由形状', group: '组合' };
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
export function EditorWorkspace() {
  const [background, setBackground] = useState(DEMO_IMAGE);
  const [sourceImage, setSourceImage] = useState(DEMO_SOURCE_IMAGE);
  const [cleanBaseReady, setCleanBaseReady] = useState(true);
  const [elements, setElements] = useState<CanvasElement[]>(cloneDemo);
  const [selectedId, setSelectedId] = useState<string | null>('title');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [status, setStatus] = useState(`已载入党建报告示例：干净底图 + ${demoElements.length} 个原生可编辑对象。`);
  const [exporting, setExporting] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: string; sx: number; sy: number; x: number; y: number } | null>(null);
  const selected = useMemo(() => elements.find((item) => item.id === selectedId) ?? null, [elements, selectedId]);

  useEffect(() => { setElements(cloneDemo()); }, []);

  const update = (id: string, patch: Partial<CanvasElement>) => setElements((items) => items.map((item) => item.id === id ? { ...item, ...patch, sourceElement: false } : item));

  function add(kind: ElementKind) {
    const common = { id: uid(), kind, name: `新${labels[kind]}`, x: 640, y: 400, w: 260, h: kind === 'line' || kind === 'connector' ? 2 : 90, sourceElement: false };
    const item: CanvasElement = kind === 'text'
      ? { ...common, text: '双击或单击此处直接输入', fontSize: 28, fontWeight: 600, color: '#CA1113', align: 'center' }
      : kind === 'line' || kind === 'connector' ? { ...common, w: 280, stroke: '#C90C10', strokeWidth: 2 }
      : kind === 'table' ? { ...common, w: 360, h: 180, fill: '#FFFFFF', stroke: '#C90C10', strokeWidth: 1, rows: 3, columns: 3, cells: [['表头 1', '表头 2', '表头 3'], ['内容', '内容', '内容'], ['内容', '内容', '内容']], fontSize: 18, color: '#202020' }
      : { ...common, fill: kind === 'icon' ? '#C90C10' : '#FFFFFF', stroke: '#C90C10', strokeWidth: 2, radius: 8 };
    setElements((items) => [...items, item]); setSelectedId(item.id);
  }

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    if (!file.type.startsWith('image/')) { setStatus('请选择 PNG、JPG 或 WEBP 图片。'); return; }
    const reader = new FileReader();
    reader.onload = () => { const image = String(reader.result); setSourceImage(image); setBackground(image); setCleanBaseReady(false); setElements([]); setSelectedId(null); setEditingId(null); setStatus('图片已导入。请点击“AI 识别”，系统会同时生成干净底图与可编辑对象；完成前不能导出，以避免重影。'); };
    reader.readAsDataURL(file);
  }

  function loadDemo() { setBackground(DEMO_IMAGE); setSourceImage(DEMO_SOURCE_IMAGE); setCleanBaseReady(false); setElements(cloneDemo()); setSelectedId('title'); setEditingId(null); setStatus(`已载入演示清单；按 v3 全局规则，未完成背景、双路线和移动/删除审核前禁止导出。`); }

  async function sourceForApi() {
    if (sourceImage.startsWith('data:image/')) return sourceImage;
    const blob = await (await fetch(sourceImage)).blob();
    return await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error('读取图片失败。')); reader.readAsDataURL(blob); });
  }

  async function analyze() {
    setAnalyzing(true); setStatus('正在执行 Knight 完整语义重建：OCR 文字清单、视觉结构规划、装饰底层、原生卡片/标签/线条与独立图标资产……');
    try {
      const image = await sourceForApi();
      const analysisResponse = await fetch('/api/analyze-slide', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image }) });
      const analysis = await analysisResponse.json() as { elements?: CanvasElement[]; protocol?: RebuildProtocol; sourceReference?: string; model?: string; ocrModel?: string; mode?: string; note?: string; error?: string; ocrTextCount?: number };
      if (!analysisResponse.ok || !analysis.elements) throw new Error(analysis.error || '未返回可编辑对象。');
      const damagedText = analysis.elements.filter((element) => element.kind === 'text' && hasTextCorruption(element.text ?? ''));
      if (damagedText.length) throw new Error(`识别结果含 ${damagedText.length} 个乱码文本框，已拒绝载入。请重新识别；原图仍保持不变。`);
      if (analysis.protocol?.schema !== 'pptx-rebuild-protocol/v3') throw new Error('分析器未返回 v3 重建协议。');
      if (!analysis.sourceReference?.startsWith('data:image/')) throw new Error('分析器未返回规范化 SOURCE_REFERENCE。');
      const reconstructed = analysis.elements.map((element) => ({ ...element, sourceElement: false }));
      const textCount = reconstructed.filter((element) => element.kind === 'text').length;
      const nativeCount = reconstructed.filter((element) => ['rectangle', 'ellipse', 'arrow', 'line', 'connector', 'table', 'freeform'].includes(element.kind)).length;
      const assetCount = reconstructed.filter((element) => element.kind === 'image' || element.kind === 'icon').length;
      if (!textCount || !nativeCount) throw new Error('语义重建清单缺少文本或原生结构，已停止生成。');
      const backgroundResponse = await fetch('/api/clean-background', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image: analysis.sourceReference, elements: reconstructed, sourceWidth: 1600, sourceHeight: 900 }) });
      const backgroundResult = await backgroundResponse.json() as { cleanBackground?: string; status?: string; note?: string; error?: string };
      if (!backgroundResponse.ok || !backgroundResult.cleanBackground?.startsWith('data:image/')) throw new Error(backgroundResult.error || 'BG_CLEAN 候选生成失败。');
      const productionElements = reconstructed.filter((element) => element.reconstructionClass !== 'decorative_fixed');
      setElements(productionElements); setBackground(backgroundResult.cleanBackground); setCleanBaseReady(false); setSelectedId(productionElements.find((item) => item.kind === 'text')?.id ?? productionElements[0]?.id ?? null); setEditingId(null);
      setStatus(`v3 工作台候选已生成：${textCount} 个 OCR 文本、${nativeCount} 个原生结构、${assetCount} 个待图库/图像资产对象。${backgroundResult.note ?? ''} 当前仍须分别通过文字≥90%、非文本≥80%、背景≥98%及移动/删除审核，尚不可导出。`);
    } catch (error) { setCleanBaseReady(false); setStatus(`AI 重建失败：${error instanceof Error ? error.message : '未知错误'}。原图不会被导出为底图，以免产生重影。`); }
    finally { setAnalyzing(false); }
  }

  function pointerDown(event: PointerEvent<HTMLDivElement>, element: CanvasElement) {
    if ((event.target as HTMLElement).dataset.handle) return;
    event.stopPropagation(); dragRef.current = { id: element.id, sx: event.clientX, sy: event.clientY, x: element.x, y: element.y }; event.currentTarget.setPointerCapture(event.pointerId); setSelectedId(element.id); setEditingId(element.kind === 'text' ? element.id : null);
  }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current; const rect = boardRef.current?.getBoundingClientRect(); if (!drag || !rect) return;
    const element = elements.find((item) => item.id === drag.id); if (!element) return; const scale = BOARD.width / rect.width;
    update(drag.id, { x: Math.round(clamp(drag.x + (event.clientX - drag.sx) * scale, 0, BOARD.width - element.w)), y: Math.round(clamp(drag.y + (event.clientY - drag.sy) * scale, 0, BOARD.height - Math.max(2, element.h))) });
  }
  function resize(element: CanvasElement, direction: 'se' | 'e' | 's', dx: number, dy: number) {
    const rect = boardRef.current?.getBoundingClientRect(); if (!rect) return; const s = BOARD.width / rect.width;
    update(element.id, { w: direction === 's' ? element.w : Math.round(clamp(element.w + dx * s, 28, BOARD.width - element.x)), h: direction === 'e' || element.kind === 'line' || element.kind === 'connector' ? element.h : Math.round(clamp(element.h + dy * s, 20, BOARD.height - element.y)) });
  }

  async function exportPptx() {
    if (!cleanBaseReady) { setStatus('请先完成“AI 识别”。只有干净底图和重建对象同时就绪时才允许导出，避免旧文字与新文字重合。'); return; }
    const damagedText = elements.filter((element) => element.kind === 'text' && hasTextCorruption(element.text ?? ''));
    if (damagedText.length) { setStatus(`已阻止导出：当前存在 ${damagedText.length} 个乱码文本框，请重新识别。`); return; }
    setExporting(true); setStatus('正在生成单层、可编辑的 PPTX……');
    try {
      let cleanBackground = background;
      if (cleanBackground.startsWith('/')) { const blob = await (await fetch(cleanBackground)).blob(); cleanBackground = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error('无法读取干净底图。')); reader.readAsDataURL(blob); }); }
      const response = await fetch('/api/export-pptx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cleanBackground, elements }) });
      if (!response.ok) throw new Error((await response.json()).error || '导出失败。');
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = '可编辑幻灯片.pptx'; link.click(); URL.revokeObjectURL(url);
      setStatus('导出完成：PPTX 使用干净底图，文字、线条、框图均为单层原生对象，不含原图文字。');
    } catch (error) { setStatus(`导出失败：${error instanceof Error ? error.message : '未知错误'}`); }
    finally { setExporting(false); }
  }

  return <main className="shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">P</span><div><strong>图转可编辑 PPT</strong><small>IMAGE TO EDITABLE SLIDES</small></div></div><div className="steps"><span className="active">1 上传图片</span><i /> <span className="active">2 审阅对象</span><i /> <span>3 导出 PPTX</span></div><button className="export" onClick={exportPptx} disabled={exporting || !cleanBaseReady}>{exporting ? '正在导出…' : '导出可编辑 PPTX'}</button></header>
    <section className="workspace">
      <aside className="sidebar leftbar"><div className="panel-title"><span>幻灯片</span><b>1 / 1</b></div><button className="thumbnail selected" onClick={loadDemo}><img src={sourceImage} alt="当前幻灯片缩略图" /><span>01</span></button><label className="upload-zone"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={onFile} /><span className="upload-icon">↑</span><b>上传 PPT 图片</b><small>PNG / JPG / WEBP</small></label><button className="quiet-button" onClick={loadDemo}>载入党建报告样例</button><div className="method-note"><b>Knight 完整语义重建</b><p>OCR 负责准确文字；视觉模型负责卡片、标签、色块、线条、箭头、层级与图标清单；装饰底层单独生成。导出不使用含字原图，也不再采用擦字覆盖。</p></div></aside>
      <section className="stage-wrap"><div className="stage-toolbar"><div className="tool-group"><button onClick={() => add('text')}>T 文本</button><button onClick={() => add('rectangle')}>▣ 框图</button><button onClick={() => add('line')}>／线条</button><button onClick={() => add('connector')}>↗ 连接</button><button onClick={() => add('table')}>▦ 表格</button><button onClick={() => add('icon')}>★ 图标</button><button onClick={() => add('group')}>◇ 组合</button><button className="ai-button" onClick={analyze} disabled={analyzing}>{analyzing ? '重建中…' : '✦ AI 识别'}</button></div><div className={`source-toggle locked-base ${cleanBaseReady ? '' : 'base-pending'}`}>{cleanBaseReady ? '▣ 干净底图已就绪' : '▣ 等待生成干净底图'}</div></div>
        <div className="stage-area"><div className="board" ref={boardRef} onPointerMove={pointerMove} onPointerUp={() => { dragRef.current = null; }} onPointerLeave={() => { dragRef.current = null; }} onClick={(event) => { if (event.target === event.currentTarget || (event.target as HTMLElement).classList.contains('paper')) { setSelectedId(null); setEditingId(null); } }}><div className="paper" />{background && <img className="source-image" src={background} alt="幻灯片干净底图" draggable={false} />}{elements.map((element) => <CanvasObject key={element.id} element={element} selected={element.id === selectedId} editing={element.id === editingId} onPointerDown={pointerDown} onResize={resize} onTextChange={(value) => update(element.id, { text: value })} onStopEditing={() => setEditingId(null)} />)}</div></div>
        <footer className="statusbar"><span className="status-dot" />{status}<span className="object-count">{elements.length} 个对象</span></footer></section>
      <aside className="sidebar inspector"><div className="panel-title"><span>属性</span>{selected && <b>{labels[selected.kind]}</b>}</div>{selected ? <Inspector element={selected} onChange={(patch) => update(selected.id, patch)} onDelete={() => { setElements((items) => items.filter((item) => item.id !== selected.id)); setSelectedId(null); }} /> : <div className="empty-inspector"><span>⌁</span><b>选择一个对象</b><p>点击画布中的对象后，在这里调整样式、位置和尺寸。</p></div>}</aside>
    </section>
  </main>;
}

function CanvasObject({ element, selected, editing, onPointerDown, onResize, onTextChange, onStopEditing }: { element: CanvasElement; selected: boolean; editing: boolean; onPointerDown: (event: PointerEvent<HTMLDivElement>, element: CanvasElement) => void; onResize: (element: CanvasElement, direction: 'se' | 'e' | 's', dx: number, dy: number) => void; onTextChange: (value: string) => void; onStopEditing: () => void }) {
  const resizeRef = useRef<{ x: number; y: number; direction: 'se' | 'e' | 's' } | null>(null);
  const style = { left: `${element.x / BOARD.width * 100}%`, top: `${element.y / BOARD.height * 100}%`, width: `${element.w / BOARD.width * 100}%`, height: `${Math.max(2, element.h) / BOARD.height * 100}%`, transform: `rotate(${element.rotation ?? 0}deg)` };
  const contentStyle = element.kind === 'text' ? { fontSize: `${(element.fontSize ?? 24) / BOARD.width * 100}cqw`, fontWeight: element.fontWeight ?? 400, color: element.color ?? '#111', textAlign: element.align ?? 'left' as const, lineHeight: 1.08 } : element.kind === 'line' || element.kind === 'connector' ? { borderTop: `${element.strokeWidth ?? 2}px solid ${element.stroke ?? '#C90C10'}` } : element.kind === 'image' ? { opacity: element.opacity ?? 1 } : { background: element.fill ?? '#FFF', border: `${element.strokeWidth ?? 1}px solid ${element.stroke ?? 'transparent'}`, borderRadius: element.kind === 'ellipse' ? '50%' : `${element.radius ?? 0}px`, clipPath: element.kind === 'arrow' ? 'polygon(0 25%,70% 25%,70% 0,100% 50%,70% 100%,70% 75%,0 75%)' : undefined, opacity: element.opacity ?? 1, color: element.color ?? '#111' };
  const beginResize = (event: PointerEvent<HTMLSpanElement>, direction: 'se' | 'e' | 's') => { event.stopPropagation(); resizeRef.current = { x: event.clientX, y: event.clientY, direction }; event.currentTarget.setPointerCapture(event.pointerId); };
  const moveResize = (event: PointerEvent<HTMLSpanElement>) => { const point = resizeRef.current; if (!point) return; onResize(element, point.direction, event.clientX - point.x, event.clientY - point.y); resizeRef.current = { ...point, x: event.clientX, y: event.clientY }; };
  return <div className={`canvas-object ${element.kind} ${selected ? 'is-selected' : ''}`} style={style} onPointerDown={(event) => onPointerDown(event, element)}><div className="object-content" style={contentStyle}>{element.kind === 'text' ? editing ? <textarea aria-label={`${element.name} 直接编辑`} className="canvas-text-editor" autoFocus value={element.text ?? ''} onPointerDown={(event) => event.stopPropagation()} onChange={(event) => onTextChange(event.target.value)} onBlur={onStopEditing} onKeyDown={(event) => { if (event.key === 'Escape') event.currentTarget.blur(); }} /> : <span className="edited-text-layer">{element.text}</span> : element.kind === 'image' && element.imageSrc ? <img className="element-image" src={element.imageSrc} alt={element.name} draggable={false} /> : element.kind === 'icon' ? '★' : element.kind === 'table' ? <div className="table-preview" style={{ gridTemplateColumns: `repeat(${element.columns ?? 2}, 1fr)` }}>{Array.from({ length: (element.rows ?? 2) * (element.columns ?? 2) }, (_, i) => <span key={i} className={i < (element.columns ?? 2) ? 'header-cell' : ''}>{element.cells?.[Math.floor(i / (element.columns ?? 2))]?.[i % (element.columns ?? 2)] ?? ''}</span>)}</div> : element.kind === 'group' ? <span className="group-label">组合 · {element.children?.length ?? 0} 个对象</span> : null}</div>{selected && <><span className="object-tag">{editing ? '直接输入中 · 拖动可移动' : labels[element.kind]}</span>{element.kind !== 'line' && element.kind !== 'connector' && <><span className="resize-handle e" data-handle onPointerDown={(event) => beginResize(event, 'e')} onPointerMove={moveResize} onPointerUp={() => { resizeRef.current = null; }} /><span className="resize-handle s" data-handle onPointerDown={(event) => beginResize(event, 's')} onPointerMove={moveResize} onPointerUp={() => { resizeRef.current = null; }} /></>}<span className="resize-handle se" data-handle onPointerDown={(event) => beginResize(event, 'se')} onPointerMove={moveResize} onPointerUp={() => { resizeRef.current = null; }} /></>}</div>;
}

function Inspector({ element, onChange, onDelete }: { element: CanvasElement; onChange: (patch: Partial<CanvasElement>) => void; onDelete: () => void }) {
  const number = (field: keyof CanvasElement, value: string) => onChange({ [field]: Number(value) });
  return <div className="inspector-content"><label className="field full"><span>对象名称</span><input value={element.name} onChange={(event) => onChange({ name: event.target.value })} /></label>{element.kind === 'text' && <label className="field full"><span>文字内容</span><textarea rows={4} value={element.text ?? ''} onChange={(event) => onChange({ text: event.target.value })} /></label>}<div className="field-grid">{(['x', 'y', 'w', 'h'] as const).map((field) => <label className="field" key={field}><span>{{ x: 'X', y: 'Y', w: '宽', h: '高' }[field]}</span><input type="number" value={Math.round(element[field])} onChange={(event) => number(field, event.target.value)} /></label>)}</div>{element.kind === 'text' && <><div className="rule" /><div className="field-grid"><label className="field"><span>字号</span><input type="number" value={element.fontSize ?? 24} onChange={(event) => number('fontSize', event.target.value)} /></label><label className="field"><span>字重</span><select value={element.fontWeight ?? 400} onChange={(event) => number('fontWeight', event.target.value)}><option value="400">常规</option><option value="600">半粗</option><option value="800">粗体</option></select></label></div><label className="field full"><span>文字颜色</span><div className="color-row"><input type="color" value={element.color ?? '#111111'} onChange={(event) => onChange({ color: event.target.value })} /><input value={element.color ?? '#111111'} onChange={(event) => onChange({ color: event.target.value })} /></div></label><div className="alignments">{(['left', 'center', 'right'] as const).map((align) => <button key={align} className={element.align === align ? 'chosen' : ''} onClick={() => onChange({ align })}>{align === 'left' ? '左对齐' : align === 'center' ? '居中' : '右对齐'}</button>)}</div></>} {element.kind !== 'text' && <><div className="rule" /><label className="field full"><span>{element.kind === 'line' || element.kind === 'connector' ? '线条颜色' : '填充颜色'}</span><div className="color-row"><input type="color" value={element.kind === 'line' || element.kind === 'connector' ? (element.stroke ?? '#C90C10') : (element.fill ?? '#FFFFFF')} onChange={(event) => onChange(element.kind === 'line' || element.kind === 'connector' ? { stroke: event.target.value } : { fill: event.target.value })} /><input value={element.kind === 'line' || element.kind === 'connector' ? (element.stroke ?? '#C90C10') : (element.fill ?? '#FFFFFF')} onChange={(event) => onChange(element.kind === 'line' || element.kind === 'connector' ? { stroke: event.target.value } : { fill: event.target.value })} /></div></label></>}<button className="delete-button" onClick={onDelete}>删除对象</button></div>;
}
