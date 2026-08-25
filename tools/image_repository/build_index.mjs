import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  if (i < 0 || !process.argv[i + 1]) throw new Error(`Missing --${name}`);
  return process.argv[i + 1];
}

async function bytes(filePath) {
  const b = await fs.readFile(filePath);
  return new Uint8Array(b.buffer, b.byteOffset, b.byteLength);
}

function shape(slide, geometry, name, position, fill = 'none', line = 'none', width = 0, radius) {
  return slide.shapes.add({
    geometry,
    name,
    position,
    fill,
    line: { style: 'solid', fill: line, width },
    ...(radius ? { borderRadius: radius } : {}),
    shadow: 'shadow-none',
  });
}

function text(slide, name, value, position, fontSize, options = {}) {
  const box = shape(slide, 'textbox', name, position);
  box.text = value;
  box.text.style = {
    typeface: 'Microsoft YaHei',
    fontSize,
    bold: Boolean(options.bold),
    color: options.color || '#132238',
    alignment: options.alignment || 'left',
    verticalAlignment: options.verticalAlignment || 'middle',
    autoFit: 'shrinkText',
    wrap: 'square',
    insets: { left: 0, right: 0, top: 0, bottom: 0 },
  };
  return box;
}

async function main() {
  const repositoryDir = path.resolve(arg('repository'));
  const outputPptx = path.resolve(arg('output'));
  const previewDir = path.resolve(arg('preview-dir'));
  await fs.mkdir(previewDir, { recursive: true });
  const manifest = JSON.parse(await fs.readFile(path.join(repositoryDir, 'manifest.json'), 'utf8'));
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

  const cover = presentation.slides.add();
  cover.background.fill = '#F4F7FA';
  shape(cover, 'rect', 'cover-left-rail', { left: 0, top: 0, width: 20, height: 720 }, '#1266AE');
  text(cover, 'cover-title', 'PPT 非文本元素图库', { left: 72, top: 92, width: 760, height: 76 }, 46, { bold: true, color: '#075196' });
  text(cover, 'cover-subtitle', '历史生成文件提取 · 视觉去重 · 分类组件包', { left: 74, top: 176, width: 720, height: 42 }, 24, { color: '#52677C' });
  const metrics = [
    ['历史 PPTX', String(manifest.source_deck_count)],
    ['来源页', String(manifest.source_slide_count)],
    ['原始候选', String(manifest.raw_record_count)],
    ['去重元素', String(manifest.unique_count)],
  ];
  metrics.forEach(([label, value], i) => {
    const left = 74 + i * 278;
    shape(cover, 'roundRect', `metric-${i + 1}`, { left, top: 302, width: 244, height: 150 }, '#FFFFFF', '#D4E0EA', 1, 12);
    text(cover, `metric-value-${i + 1}`, value, { left: left + 18, top: 322, width: 208, height: 68 }, 42, { bold: true, color: '#0B60AA', alignment: 'center' });
    text(cover, `metric-label-${i + 1}`, label, { left: left + 18, top: 392, width: 208, height: 38 }, 18, { color: '#53677A', alignment: 'center' });
  });
  text(cover, 'cover-note', '打开同目录 catalog.html 可搜索；各分类 components.pptx 每页保存一个可复制组件。', { left: 74, top: 566, width: 1080, height: 48 }, 19, { color: '#52677C' });
  cover.speakerNotes.textFrame.setText('[Sources]\n- Project-local historical PPTX files listed in manifest.json.');

  for (const [category, count] of Object.entries(manifest.category_counts)) {
    const catManifestPath = path.join(repositoryDir, category, 'manifest.json');
    const catManifest = JSON.parse(await fs.readFile(catManifestPath, 'utf8'));
    const sheets = catManifest.contact_sheets || [];
    if (!sheets.length) continue;
    for (let pageIndex = 0; pageIndex < sheets.length; pageIndex += 1) {
      const slide = presentation.slides.add();
      slide.background.fill = '#F4F7FA';
      shape(slide, 'rect', `category-rail-${category}-${pageIndex}`, { left: 0, top: 0, width: 14, height: 720 }, '#1266AE');
      text(slide, `category-title-${category}-${pageIndex}`, catManifest.label, { left: 42, top: 24, width: 620, height: 48 }, 30, { bold: true, color: '#075196' });
      text(slide, `category-meta-${category}-${pageIndex}`, `${count} 个元素 · 预览 ${pageIndex + 1}/${sheets.length}`, { left: 830, top: 30, width: 390, height: 36 }, 17, { color: '#617487', alignment: 'right' });
      const sheetPath = path.join(repositoryDir, category, sheets[pageIndex]);
      slide.images.add({
        blob: await bytes(sheetPath),
        contentType: 'image/png',
        alt: `${catManifest.label} contact sheet ${pageIndex + 1}`,
        fit: 'contain',
        position: { left: 30, top: 82, width: 1220, height: 610 },
      });
      slide.speakerNotes.textFrame.setText(`[Sources]\n- Extracted project-local PPTX elements; see ${category}/manifest.json.`);
    }
  }

  for (const [i, slide] of presentation.slides.items.entries()) {
    const png = await presentation.export({ slide, format: 'png', scale: 1 });
    await fs.writeFile(path.join(previewDir, `slide-${String(i + 1).padStart(3, '0')}.png`), new Uint8Array(await png.arrayBuffer()));
  }
  const montage = await presentation.export({ format: 'webp', montage: true, scale: 0.6 });
  await fs.writeFile(path.join(previewDir, 'index-montage.webp'), new Uint8Array(await montage.arrayBuffer()));
  const inspect = await presentation.inspect({ kind: 'slide,textbox,shape,image,notes', maxChars: 60000 });
  await fs.writeFile(path.join(previewDir, 'index-inspect.ndjson'), inspect.ndjson, 'utf8');
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(outputPptx);
  console.log(JSON.stringify({ outputPptx, slides: presentation.slides.items.length, previewDir }));
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
