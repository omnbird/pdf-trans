"""Build an invisible text layer on top of a scanned PDF.

Why: BabelDOC preserves the original layout, but it needs the page to expose
text (it reads the PDF's text objects and their positions). A scanned PDF has
only images, so it cannot be translated with layout preservation at all.

The trick used here is the same one searchable-PDF ("OCR PDF") tools rely on:
run OCR, then write each recognised line back onto the page as *invisible*
text (render_mode=3) at the exact position it appeared. The page still looks
identical, but now BabelDOC can find the text, translate it, and overlay the
translation while keeping images, tables and page geometry untouched.

The OCR output comes from PaddleOCR (PP-OCRv5 det+rec), which already returns
line boxes: no document-VLM is required for this to work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

import pymupdf
from PIL import Image

logger = logging.getLogger(__name__)

# Default for the invisible layer's font size = median line height × this
# factor. Override per run with scanned.text_layer_font_scale.
# Measured on a synthetic scan (10 pt original): 0.78 and 0.88 both give a
# uniform result, 0.88 lands closest to the original size, while 1.0 makes
# BabelDOC shrink individual paragraphs again (visible size jumps).
FONT_SIZE_FACTOR = 0.88

# Pages are rendered and OCR'ed in chunks to bound memory on long books.
BATCH_SIZE = 6

# DocLayout labels whose text is *not* worth translating: charts, diagrams,
# scans of tables. Text inside these regions is left as-is on the page image.
DEFAULT_SKIP_LABELS = ("figure", "table")


def _diagram_boxes(layout_model, image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Figure/table regions of a page image, in pixel coordinates."""
    import numpy as np

    try:
        result = layout_model.predict(np.array(image))[0]
    except Exception as exc:  # pragma: no cover - layout model is best-effort
        logger.warning("[textlayer] layout prediction failed: %s", exc)
        return []

    names = getattr(result, "names", {}) or {}
    boxes = []
    for box in getattr(result, "boxes", []):
        label = names.get(int(box.cls), "")
        if label in DEFAULT_SKIP_LABELS:
            x0, y0, x1, y1 = (float(v) for v in box.xyxy)
            boxes.append((x0, y0, x1, y1))

    # Hand-drawn charts are frequently under-detected by the layout model;
    # add regions enclosed by ruling lines.
    boxes.extend(_grid_boxes(image))

    # Small margin so labels sitting on the frame are included too.
    if boxes:
        w, h = image.size
        mx, my = w * 0.004, h * 0.004
        boxes = [(max(0.0, x0 - mx), max(0.0, y0 - my), x1 + mx, y1 + my)
                 for x0, y0, x1, y1 in boxes]
    return boxes


def _grid_boxes(image: Image.Image) -> list[tuple[float, float, float, float]]:
    """Regions enclosed by ruling lines (tables, chart frames), via morphology.

    YOLO alone often boxes only part of a hand-drawn chart, so this adds a
    geometric signal: long horizontal + vertical rules whose intersection forms
    a rectangle. Page borders (>80% of the page) are ignored.
    """
    try:
        import cv2
        import numpy as np
    except Exception:  # pragma: no cover - optional
        return []

    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    # Dark pixels = ink; invert so lines become white.
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    h, w = binary.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 20), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 20)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=1)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=1)
    grid = cv2.add(horizontal, vertical)
    grid = cv2.dilate(grid, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=2)

    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = float(w * h)
    boxes = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        area = float(bw * bh)
        if area < page_area * 0.02 or area > page_area * 0.8:
            continue
        if bw < w * 0.15 or bh < h * 0.05:
            continue
        boxes.append((float(x), float(y), float(x + bw), float(y + bh)))
    return boxes


def _inside(x: float, y: float, boxes: Sequence[tuple[float, float, float, float]]) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)


def _has_cjk(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" for ch in text)


def _pick_font(text: str) -> str:
    """Built-in font that can actually encode the glyphs (invisible anyway)."""
    return "china-s" if _has_cjk(text) else "helv"


def build_text_layer_pdf(
    src: str | Path,
    dst: str | Path,
    ocr_engine,
    dpi: int = 200,
    page_indices: Sequence[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    skip_diagrams: bool = True,
    font_scale: float | None = None,
) -> Path:
    """Write OCR text back onto `src` as invisible text and save to `dst`.

    Args:
        src: original (scanned) PDF
        dst: output PDF with an invisible text layer (source is not modified)
        ocr_engine: object with `predict(PIL.Image) -> list[TextBlock]`
            (see python_engine/ocr_engine.py)
        dpi: rendering resolution for OCR — higher is more accurate but slower
        page_indices: pages to OCR (0-based); None = all pages
        on_progress: callback(done, total) for progress reporting
        font_scale: font size of the layer relative to the median line height
            (default FONT_SIZE_FACTOR); smaller leaves more room so BabelDOC
            does not have to shrink the translation itself
        skip_diagrams: leave text inside figures/tables untranslated (charts and
            diagram labels read as gibberish when machine-translated, and the
            page image already contains them)

    Returns:
        The path written (`dst`).
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    layout_model = None
    if skip_diagrams:
        try:
            from babeldoc.docvision.doclayout import DocLayoutModel

            layout_model = DocLayoutModel.load_available()
            logger.info("[textlayer] skipping figure/table regions during OCR")
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("[textlayer] layout model unavailable, translating everything: %s", exc)

    doc = pymupdf.open(str(src))
    try:
        targets = list(page_indices) if page_indices is not None else list(range(doc.page_count))
        total = len(targets)
        done = 0

        for start in range(0, total, BATCH_SIZE):
            batch = targets[start:start + BATCH_SIZE]

            images: list[Image.Image] = []
            scale = 72.0 / dpi  # image pixels -> PDF points

            for idx in batch:
                page = doc[idx]
                pix = page.get_pixmap(dpi=dpi)
                mode = "RGB" if pix.n >= 3 else "L"
                images.append(
                    Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")
                )

            try:
                blocks_per_page = ocr_engine.predict_batch(images)
            except AttributeError:  # engine without batch API
                blocks_per_page = [ocr_engine.predict(img) for img in images]

            for idx, blocks, image in zip(batch, blocks_per_page, images):
                page = doc[idx]
                skip_boxes = _diagram_boxes(layout_model, image) if layout_model else []
                skipped = 0
                # One font size per page: line boxes vary with ascenders and
                # descenders, and feeding that jitter to BabelDOC makes it
                # render paragraphs at visibly different sizes.
                heights = sorted(
                    b.height for b in (blocks or [])
                    if getattr(b, "height", 0) > 0 and (getattr(b, "text", "") or "").strip()
                )
                factor = font_scale if font_scale and font_scale > 0 else FONT_SIZE_FACTOR
                page_font = max(3.0, heights[len(heights) // 2] * scale * factor) if heights else None
                if page_font and heights:
                    original_pt = heights[len(heights) // 2] * scale
                    logger.info(
                        "[textlayer] page %d: original type ≈ %.1f pt, layer set to %.1f pt",
                        idx + 1, original_pt, page_font,
                    )
                for block in blocks or []:
                    text = (getattr(block, "text", "") or "").strip()
                    if not text:
                        continue
                    x0 = block.x * scale
                    y1 = (block.y + block.height) * scale
                    height = block.height * scale
                    if height <= 0 or x0 < 0 or y1 < 0:
                        continue

                    if skip_boxes:
                        # OCR blocks and layout boxes share the rendered-image
                        # pixel space; compare there (not in PDF points).
                        cx = block.x + block.width / 2
                        cy = block.y + block.height / 2
                        if _inside(cx, cy, skip_boxes):
                            skipped += 1
                            continue

                    font_size = page_font if page_font else max(3.0, height * 0.78)
                    baseline = y1 - height * 0.18
                    try:
                        page.insert_text(
                            (x0, baseline),
                            text,
                            fontsize=font_size,
                            fontname=_pick_font(text),
                            render_mode=3,  # invisible: 0=fill, 3=invisible
                        )
                    except Exception as exc:  # pragma: no cover - font/edge cases
                        logger.warning(
                            "[textlayer] page %d: could not insert %r (%s)",
                            idx + 1, text[:40], exc,
                        )

                if skipped:
                    logger.info(
                        "[textlayer] page %d: left %d line(s) inside figures/tables untranslated",
                        idx + 1, skipped,
                    )

                done += 1
                if on_progress:
                    on_progress(done, total)

        doc.save(str(dst), garbage=3, deflate=True)
        page_total = doc.page_count
    finally:
        doc.close()

    logger.info("[textlayer] wrote %s (%d pages, %d OCR'ed)", dst, page_total, total)
    return dst
