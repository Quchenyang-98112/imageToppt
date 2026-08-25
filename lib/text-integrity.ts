/** Detect byte-decoding artifacts that must never enter the editor or PPTX. */
export function hasTextCorruption(value: string) {
  if (!value) return false;
  const replacement = (value.match(/\uFFFD/g) ?? []).length;
  const commonMojibake = (value.match(/(?:Ã.|Â.|â.|ï¿½|锟斤拷)/g) ?? []).length;
  return replacement > 0 || commonMojibake >= Math.max(2, Math.ceil(value.length * .08));
}

export function corruptTextIndexes(values: string[]) {
  return values.map((value, index) => hasTextCorruption(value) ? index : -1).filter((index) => index >= 0);
}
