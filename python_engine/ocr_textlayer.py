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

# Pages are rendered and OCR'ed in chunks to bound memory on long books.
BATCH_SIZE = 6


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

    Returns:
        The path written (`dst`).
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

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

            for idx, blocks in zip(batch, blocks_per_page):
                page = doc[idx]
                for block in blocks or []:
                    text = (getattr(block, "text", "") or "").strip()
                    if not text:
                        continue
                    x0 = block.x * scale
                    y1 = (block.y + block.height) * scale
                    height = block.height * scale
                    if height <= 0 or x0 < 0 or y1 < 0:
                        continue

                    font_size = max(3.0, height * 0.78)
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

                done += 1
                if on_progress:
                    on_progress(done, total)

        doc.save(str(dst), garbage=3, deflate=True)
    finally:
        doc.close()

    logger.info("[textlayer] wrote %s (%d pages)", dst, len(page_indices or range(doc.page_count)))
    return dst
