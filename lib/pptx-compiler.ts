import PptxGenJS from 'pptxgenjs';
import { readFile } from 'node:fs/promises';
import type { CanvasElement } from '@/lib/types';

export type CompilePage = { sourcePath: string; backgroundPath: string; elements: CanvasElement[]; title?: string };
const SW = 1600;
const SH = 900;
const PW = 13.333333;
const PH = 7.5;
const xScale = PW / SW;
const yScale = PH / SH;
const color = (value: unknown, fallback: string) => { const raw = typeof value === 'string' ? value.replace(/^#/, '') : ''; return /^[0-9a-f]{6}$/i.test(raw) ? raw.toUpperCase() : fallback; };
const dataUrl = async (path: string) => `data:image/png;base64,${(await readFile(path)).toString('base64')}`;

function position(element: CanvasElement) { return { x: Math.max(0, element.x * xScale), y: Math.max(0, element.y * yScale), w: Math.max(.02, element.w * xScale), h: Math.max(.02, element.h * yScale) }; }

export async function compileDeck(pages: CompilePage[], outputPath: string) {
  const pptx = new PptxGenJS();
  pptx.defineLayout({ name: 'SKILL_MERGE_WIDE', width: PW, height: PH });
  pptx.layout = 'SKILL_MERGE_WIDE';
  pptx.author = 'skill-merge';
  pptx.subject = 'Strict high-fidelity editable PowerPoint reconstruction';
  pptx.title = 'skill-merge editable deck';
  for (const page of pages) {
    const slide = pptx.addSlide();
    slide.background = { color: 'FFFFFF' };
    slide.addImage({ data: await dataUrl(page.backgroundPath), x: 0, y: 0, w: PW, h: PH });
    const ordered = page.elements.map((element, index) => ({ element, index })).sort((a, b) => (a.element.zIndex ?? a.index) - (b.element.zIndex ?? b.index)).map((item) => item.element);
    for (const element of ordered) {
      const p = position(element);
      if (element.kind === 'text') {
        slide.addText(element.text || '', { ...p, fontFace: 'Microsoft YaHei', fontSize: Math.max(5, Math.min(60, (element.fontSize || 24) * .6)), bold: (element.fontWeight || 400) >= 600, color: color(element.color, '111111'), align: element.align || 'left', margin: 0, breakLine: false, fit: 'shrink', valign: 'middle', paraSpaceAfter: 0, lang: 'zh-CN' });
      } else if (element.kind === 'ellipse') {
        slide.addShape(pptx.ShapeType.ellipse, { ...p, fill: { color: color(element.fill, 'FFFFFF'), transparency: Math.round((1 - Math.max(0, Math.min(1, element.opacity ?? 1))) * 100) }, line: { color: color(element.stroke, '2674C8'), width: Math.max(.25, element.strokeWidth || 1) } });
      } else if (element.kind === 'arrow') {
        slide.addShape(pptx.ShapeType.rightArrow, { ...p, rotate: element.rotation || 0, fill: { color: color(element.fill, '2674C8') }, line: { color: color(element.stroke, '2674C8'), width: Math.max(.25, element.strokeWidth || 1) } });
      } else if (element.kind === 'line' || element.kind === 'connector') {
        slide.addShape(pptx.ShapeType.line, { x: p.x, y: p.y + p.h / 2, w: p.w, h: 0, line: { color: color(element.stroke, '2674C8'), width: Math.max(.25, element.strokeWidth || 1), beginArrowType: element.kind === 'connector' ? 'none' : undefined, endArrowType: element.kind === 'connector' ? 'triangle' : undefined } });
      } else if ((element.kind === 'image' || element.kind === 'icon') && (element.assetSource || element.imageSrc)) {
        slide.addImage({ data: element.assetSource || element.imageSrc || '', x: p.x, y: p.y, w: p.w, h: p.h, transparency: Math.round((1 - Math.max(0, Math.min(1, element.opacity ?? 1))) * 100) });
      } else if (element.kind === 'table') {
        const rows = Math.max(1, element.rows || 2); const columns = Math.max(1, element.columns || 2);
        slide.addTable(Array.from({ length: rows }, (_, row) => Array.from({ length: columns }, (_, column) => ({ text: element.cells?.[row]?.[column] || '', options: { bold: row === 0, color: color(element.color, '111111') } }))), { ...p, fontFace: 'Microsoft YaHei', fontSize: Math.max(5, (element.fontSize || 18) * .6), border: { type: 'solid', color: color(element.stroke, 'CBD5E1'), pt: 1 }, fill: { color: color(element.fill, 'FFFFFF') }, margin: .03 });
      } else {
        slide.addShape((element.radius || 0) > 0 ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, { ...p, fill: { color: color(element.fill, 'FFFFFF'), transparency: Math.round((1 - Math.max(0, Math.min(1, element.opacity ?? 1))) * 100) }, line: { color: color(element.stroke, 'CBD5E1'), width: Math.max(.25, element.strokeWidth || 1) } });
      }
    }
  }
  await pptx.writeFile({ fileName: outputPath });
}
