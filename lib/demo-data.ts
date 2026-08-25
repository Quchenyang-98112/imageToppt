import type { CanvasElement } from './types';

/** The sample's decorative layer only. All original text, cards and icons were removed. */
export const DEMO_IMAGE = '/samples/party-report-clean-background.png';
export const DEMO_SOURCE_IMAGE = '/samples/party-report.png';

const red = '#C90C10';
const orange = '#F4B64A';
const ink = '#202020';
const white = '#FFFFFF';

const text = (id: string, name: string, x: number, y: number, w: number, h: number, value: string, fontSize: number, color = ink, align: CanvasElement['align'] = 'left', fontWeight = 400): CanvasElement => ({
  id, kind: 'text', name, x, y, w, h, text: value, sourceText: value, sourceElement: false, fontSize, fontWeight, color, align,
});

const box = (id: string, name: string, x: number, y: number, w: number, h: number, fill = white, stroke = '#F2D7C2', radius = 14): CanvasElement => ({
  id, kind: 'rectangle', name, x, y, w, h, fill, stroke, strokeWidth: 1, radius, sourceElement: false,
});

const line = (id: string, name: string, x: number, y: number, w: number): CanvasElement => ({
  id, kind: 'line', name, x, y, w, h: 0, stroke: '#DF1B1A', strokeWidth: 1.6, sourceElement: false,
});

const cards = [
  ['01', '强化政治建设', '⚑', ['学习贯彻四中全会精神', '深化模范机关建设', '落实意识形态工作责任制']],
  ['02', '筑牢思想根基', '▱', ['高质量开展正确政绩观学习教育', '推动党史学习教育和党纪学习\n教育常态化长效化', '完善基本培训机制']],
  ['03', '增强组织功能', '●●', ['加强基层党组织建设', '强化分类指导', '做好庆祝建党105周年\n相关工作']],
  ['04', '突出基层导向', '⌂', ['抓党建促乡村振兴', '深化党建引领基层治理', '加强新兴领域党的建设', '强化国有企业党建工作']],
  ['05', '正风肃纪反腐', '廉', ['推进作风建设常态化长效化', '加强对权力配置和运行的规范监督', '一体推进不敢腐、不能腐、不想腐', '把党的纪律转化为干事创业动力']],
  ['06', '压实责任落实', '☑', ['拧紧党建责任链条', '加强党务干部队伍建设', '强化督责考责']],
] as const;

const cardElements = cards.flatMap(([number, title, icon, items], index): CanvasElement[] => {
  const col = index % 3;
  const row = Math.floor(index / 3);
  const x = [68, 568, 1067][col];
  const y = row === 0 ? 366 : 574;
  const slug = `card-${index + 1}`;
  return [
    box(slug, `模块 ${number} 卡片`, x, y, 466, 196),
    box(`${slug}-tab`, `模块 ${number} 编号底`, x, y, 92, 60, red, red, 0),
    { id: `${slug}-slash`, kind: 'freeform', name: `模块 ${number} 金色装饰`, x: x + 90, y, w: 22, h: 60, fill: orange, stroke: orange, strokeWidth: 0, sourceElement: false },
    text(`${slug}-number`, `模块 ${number} 编号`, x + 18, y + 10, 60, 42, number, 31, white, 'center', 800),
    text(`${slug}-title`, `模块 ${number} 标题`, x + 132, y + 17, 290, 38, title, 28, red, 'center', 800),
    line(`${slug}-rule`, `模块 ${number} 分隔线`, x + 116, y + 64, 320),
    text(`${slug}-icon`, `模块 ${number} 图标`, x + 28, y + 88, 100, 88, icon, icon === '廉' ? 49 : 54, red, 'center', 800),
    text(`${slug}-items`, `模块 ${number} 要点`, x + 145, y + 89, 290, 100, items.map((item) => `•  ${item}`).join('\n'), 17, ink, 'left', 500),
  ];
});

/** Reconstructed native elements for the supplied 1600×900 example. */
export const demoElements: CanvasElement[] = [
  text('title', '主标题', 290, 58, 1040, 84, '以高质量党建引领高质量发展', 56, '#D21012', 'center', 800),
  text('subtitle', '副标题', 470, 154, 660, 46, '——2026年党建重点工作专题报告', 34, '#CF1011', 'center', 700),
  box('summary-box', '总体要求边框', 66, 228, 1468, 112, '#FFFDFB', '#D21012', 16),
  box('summary-label', '总体要求标题底', 72, 234, 316, 102, red, red, 4),
  text('summary-title', '总体要求标题', 102, 267, 260, 38, '★  时代方位与总体要求', 24, white, 'center', 800),
  { id: 'summary-divider', kind: 'line', name: '总体要求竖分隔线', x: 418, y: 244, w: 0, h: 80, stroke: '#9D9D9D', strokeWidth: 1, sourceElement: false },
  text('summary-copy', '总体要求正文', 448, 242, 1035, 84, '①  2026年：建党105周年、“十五五”开局之年\n②  总体要求：坚持以习近平新时代中国特色社会主义思想为指导，增强“四个意识”、坚定“四个自信”、做到“两个维护”\n③  核心主线：落实党建责任制，深化模范机关建设，坚持“四个带头”，当好“三个表率”，走好第一方阵', 17, ink, 'left', 500),
  ...cardElements,
  box('footer', '目标导向横幅', 70, 786, 1460, 61, red, red, 12),
  text('footer-text', '目标导向文本', 112, 802, 1376, 32, '★  目标导向：以高质量党建促进高质量发展，为实现“十五五”良好开局提供坚强政治保证和组织保证。', 24, white, 'center', 800),
];
