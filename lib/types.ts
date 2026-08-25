export type ElementKind =
  | 'text'
  | 'rectangle'
  | 'ellipse'
  | 'arrow'
  | 'line'
  | 'connector'
  | 'icon'
  | 'image'
  | 'table'
  | 'freeform'
  | 'group';

export type ReconstructionClass =
  | 'ocr_text'
  | 'native_editable'
  | 'library_native'
  | 'library_svg'
  | 'library_png'
  | 'exact_brand_asset'
  | 'qwen_image_asset'
  | 'decorative_fixed'
  | 'decorative_movable';

export type AssetKind = 'native_editable' | 'svg' | 'png' | 'qwen_image';

export type ElementQaStatus = 'pending' | 'detected' | 'planned' | 'built' | 'review_failed' | 'repairing' | 'passed';

export type CanvasElement = {
  id: string;
  kind: ElementKind;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation?: number;
  text?: string;
  /** OCR/初始文本，用于仅在用户改写后覆盖原图文字，避免未编辑内容重影。 */
  sourceText?: string;
  /** 由原图 OCR/预标注得出的元素；未变更时只保留其可点击区域，不重复绘制。 */
  sourceElement?: boolean;
  /** Stable link back to the authoritative OCR line used by the vision layout pass. */
  ocrIndex?: number;
  /** Semantic reconstruction role used for QA and layer ordering. */
  role?: 'decoration' | 'container' | 'label' | 'data' | 'body' | 'icon' | 'photo' | 'brand' | 'chart' | 'flow';
  reconstructionClass?: ReconstructionClass;
  assetKind?: AssetKind;
  sourceBBox?: [number, number, number, number];
  zIndex?: number;
  placementConfidence?: number;
  parentId?: string | null;
  semanticImpact?: boolean;
  classificationReason?: string;
  qaStatus?: ElementQaStatus;
  galleryAssetId?: string;
  gallerySimilarity?: number;
  structuralVetoes?: string[];
  /** Actual executable gallery payload. Preview PNGs are never stored here for SVG/native matches. */
  assetSource?: string;
  nativeComponentId?: string;
  sourceCropUsedAsFinal?: boolean;
  /** OCR items semantically owned by this container/label host. */
  containsOcr?: number[];
  fontSize?: number;
  fontWeight?: number;
  color?: string;
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  align?: 'left' | 'center' | 'right';
  radius?: number;
  opacity?: number;
  imageSrc?: string;
  rows?: number;
  columns?: number;
  cells?: string[][];
  points?: Array<{ x: number; y: number }>;
  children?: string[];
};

export type SlideDocument = {
  /** Visible production background. It must be BG_CLEAN, never the full source screenshot. */
  background: string;
  sourceReference?: string;
  cleanBackground?: string;
  elements: CanvasElement[];
};
