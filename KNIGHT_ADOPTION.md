# Knight Image-to-PPTX adoption

This project vendors the upstream reference skill under
`reference/knight-imagetopptx-skill` and applies the following runtime rules.

## Adopted in code

- Text recognition is a separate hard-gated channel. `qwen3.5-ocr` is called
  through DashScope's native `advanced_recognition` task so that each line has
  official glyph text and image-space coordinates; VLM text is never used as a
  silent fallback.
- OCR coordinates are normalized from the actual uploaded image dimensions to
  the editor's 1600x900 coordinate system. Mojibake, empty text, duplicate
  fragments, out-of-bounds boxes, and severe overlaps are rejected before the
  editor can display them.
- In generic uploads the application currently runs in text-first mode: OCR
  text becomes editable while photos, icons, cards, lines, and decoration stay
  exact in the source-derived base image. This deliberately prioritizes a clean,
  editable text result before non-text semantic reconstruction.
- Text color, safe line height, and font hierarchy are measured from source
  pixels. Text removal targets glyph-colored pixels and locally repairs those
  pixels instead of painting large opaque rectangles over the slide.
- Every recognition result produces a stable visual inventory.
- Every object is classified as `native_editable` or `source_slice_asset`.
- Results with out-of-bounds objects, severe text overlap, or an incomplete
  structural inventory are rejected before editing/export.
- A failed first-pass inventory triggers one full image-grounded layout repair
  pass. The repaired inventory is accepted only when its measured quality score
  is higher; otherwise the original image remains visible and export stays locked.
- A deterministic sanitation pass clamps every object to 1600x900 and removes
  only provable duplicate text fragments (same/contained text with severe overlap).
- Text boxes are measured against the real Microsoft YaHei font before PPTX
  generation. The upstream `ppt_text_fit.py` is called through one batch job;
  any text that still cannot fit blocks export and reports its element ID.
- Independent icon slices are repacked with a 12-pixel transparent safety
  gutter and validated by the upstream `check_rebuild_assets.py` before export.
- PPTX layer order remains containers/shapes, then images/icons, then text.

## Project-specific override

The upstream skill normally requires image-generated replacements for complex
assets. This application's product requirement explicitly prefers exact,
tightly cropped source-image slices for icons and pictograms. Therefore the
runtime classification uses `source_slice_asset` and preserves the source
appearance while keeping each slice independently movable and resizable.

## Quality contract

An API response is not considered successful merely because model JSON parsed.
The reconstruction must pass the inventory quality gate. Export independently
runs text-fit and asset-padding checks; failed checks return a non-2xx response
instead of emitting a visibly broken PPTX.
