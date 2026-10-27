"""PDF processing utilities.

Handles PDF type detection, text extraction, and page rendering.
Uses PyMuPDF (fitz) for all PDF operations.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
from PIL import Image


@dataclass
class TextBlock:
    """A block of text extracted from PDF."""

    text: str
    x: float
    y: float
    width: float
    height: float
    font_size: float
    is_bold: bool = False
    page_num: int = 0


@dataclass
class PDFPage:
    """Processed page data."""

    page_num: int
    text_blocks: list[TextBlock]
    image: Image.Image | None = None  # rendered page image for scanned mode
    width: float = 0.0  # page width in points
    height: float = 0.0  # page height in points


@dataclass
class PDFDocument:
    """Processed PDF document."""

    path: Path
    pages: list[PDFPage]
    page_count: int
    pdf_type: Literal["digital", "scanned"]


# Common English words used for OCR quality heuristic
_COMMON_WORDS = {
    "the", "and", "of", "to", "a", "in", "is", "it", "for", "with", "by", "on",
    "at", "from", "as", "that", "this", "or", "are", "was", "be", "have", "has",
    "had", "not", "but", "an", "page", "ch", "vol", "preface", "contents", "details",
    "translation", "commentary", "annotation", "editing", "publisher", "publications",
}


def is_readable_text(text: str) -> bool:
    """Check if extracted text is readable/valid English.

    Uses heuristics to detect OCR garbage (e.g. Sanskrit/Devanagari
    mis-recognised as random Latin characters).

    Returns True for valid English/Sanskrit-transliteration text,
    False for obvious garbage.
    """
    if not text or len(text.strip()) < 2:
        return False

    stripped = text.strip()
    alpha_count = sum(1 for c in stripped if c.isalpha())
    total = len(stripped)

    # Pure numbers / symbols – keep but don't need translation
    if alpha_count == 0:
        return False

    alpha_ratio = alpha_count / total
    if alpha_ratio < 0.4:
        return False

    text_lower = stripped.lower()
    vowels = sum(1 for c in text_lower if c in "aeiou")
    vowel_ratio = vowels / alpha_count if alpha_count > 0 else 0

    # Look for at least one recognisable English word
    words = set(_WORDS_RE.findall(text_lower))
    has_common_word = len(words & _COMMON_WORDS) > 0

    # Long runs of consonants are a strong garbage signal
    runs = _CONSONANT_RUN_RE.findall(stripped)
    max_consonant_run = max(len(m) for m in runs) if runs else 0

    # Valid text must satisfy at least one of:
    #   - contains a common English word
    #   - reasonable vowel ratio (English ~30-45%)
    # And must not have excessive consonant clusters
    if has_common_word:
        return max_consonant_run < 6

    if vowel_ratio >= 0.22:
        return max_consonant_run < 5

    # Very low vowels and no common words → almost certainly garbage
    return False


_WORDS_RE = __import__("re").compile(r"[a-zA-Z]+")
_CONSONANT_RUN_RE = __import__("re").compile(r"[^aeiouAEIOU\s\d\W]+")


def parse_page_ranges(pages_str: str, total_pages: int) -> list[int]:
    """Parse page range string into 0-based page indices.

    Supports formats:
        - "1-10"     → pages 1 to 10
        - "1,3,5"    → pages 1, 3, 5
        - "1-5,8,10-12" → mixed
        - ""         → all pages

    Args:
        pages_str: Page range string (1-based, user-facing)
        total_pages: Total number of pages in the document

    Returns:
        List of 0-based page indices
    """
    if not pages_str.strip():
        return list(range(total_pages))

    result: set[int] = set()
    parts = [p.strip() for p in pages_str.split(",")]

    for part in parts:
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                start_idx = max(0, int(start.strip()) - 1)
                end_idx = min(total_pages, int(end.strip()))
                for i in range(start_idx, end_idx):
                    result.add(i)
            except ValueError:
                continue
        else:
            try:
                idx = int(part) - 1
                if 0 <= idx < total_pages:
                    result.add(idx)
            except ValueError:
                continue

    return sorted(result)


def detect_pdf_type(doc: fitz.Document, threshold: float = 0.01) -> Literal["digital", "scanned"]:
    """Detect if a PDF is digital (text-based) or scanned (image-based).

    Strategy: check first 3 pages. If average text density < threshold,
    consider it scanned. Also check text quality — if most extracted text
    looks like OCR garbage, treat as scanned even if text area is large.

    Args:
        doc: PyMuPDF document
        threshold: Minimum ratio of text area to page area

    Returns:
        "digital" or "scanned"
    """
    sample_pages = min(3, len(doc))
    text_ratios = []
    readable_ratios = []

    for i in range(sample_pages):
        page = doc[i]
        page_area = page.rect.width * page.rect.height
        text_area = 0.0
        readable_area = 0.0

        blocks = page.get_text("dict").get("blocks", [])
        for block in blocks:
            if block.get("type") != 0:  # skip non-text blocks
                continue
            x0, y0, x1, y1 = block["bbox"]
            area = (x1 - x0) * (y1 - y0)
            text_area += area

            # Check text quality within this block
            block_text = ""
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    block_text += span.get("text", "")
            if is_readable_text(block_text):
                readable_area += area

        if page_area > 0:
            text_ratios.append(text_area / page_area)
            readable_ratios.append(readable_area / page_area)

    avg_ratio = sum(text_ratios) / len(text_ratios) if text_ratios else 0
    avg_readable = sum(readable_ratios) / len(readable_ratios) if readable_ratios else 0

    # If text area is high but readable text area is very low,
    # it's likely a scanned PDF with hidden OCR garbage layer
    if avg_ratio >= threshold and avg_readable < threshold * 0.3:
        return "scanned"

    return "digital" if avg_ratio >= threshold else "scanned"


def extract_text_blocks(page: fitz.Page, page_num: int) -> list[TextBlock]:
    """Extract text blocks from a page with position and style info."""
    blocks = page.get_text("dict").get("blocks", [])
    text_blocks = []

    for block in blocks:
        if block.get("type") != 0:  # skip non-text blocks
            continue

        x0, y0, x1, y1 = block["bbox"]
        text = ""
        font_size = 12.0
        is_bold = False

        for line in block.get("lines", []):
            line_text = ""
            for span in line.get("spans", []):
                line_text += span.get("text", "")
                font_size = max(font_size, span.get("size", 12.0))
                flags = span.get("flags", 0)
                # Check bold flag (bit 4 in fitz text flags)
                if flags & 2 ** 4:
                    is_bold = True
            text += line_text + " "

        text = text.strip()
        if text:
            text_blocks.append(TextBlock(
                text=text,
                x=x0,
                y=y0,
                width=x1 - x0,
                height=y1 - y0,
                font_size=font_size,
                is_bold=is_bold,
                page_num=page_num,
            ))

    # Sort by reading order: top-to-bottom, then left-to-right
    text_blocks.sort(key=lambda b: (b.y, b.x))
    return text_blocks


def render_page_to_image(page: fitz.Page, dpi: int = 300) -> Image.Image:
    """Render a PDF page to a PIL Image."""
    zoom = dpi / 72.0  # PDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def process_pdf(
    path: str | Path,
    scan_dpi: int = 300,
    pages_str: str = "",
    ocr_engine: "PaddleOCREngine | None" = None,
    is_scanned: bool = False,
) -> PDFDocument:
    """Process a PDF file: extract text or render images for OCR.

    The caller decides whether the PDF is scanned via `is_scanned`. There is
    no auto-detection — the user explicitly picks the mode.

    Args:
        path: Path to PDF file
        scan_dpi: DPI for rendering scanned pages
        pages_str: Optional page range string (e.g. "1-10" or "1,3,5")
        ocr_engine: Optional OCR engine for scanned PDFs (only used when is_scanned=True)
        is_scanned: If True, treat every page as scanned and render + OCR them

    Returns:
        PDFDocument with extracted data
    """
    path = Path(path)
    doc = fitz.open(path)
    pdf_type = "scanned" if is_scanned else "digital"

    # Parse page ranges
    page_indices = parse_page_ranges(pages_str, len(doc))

    # Batch OCR path: render all scanned pages first, then OCR in one call
    if pdf_type == "scanned" and ocr_engine is not None:
        rendered_images: list[Image.Image] = []
        for i in page_indices:
            rendered_images.append(render_page_to_image(doc[i], scan_dpi))

        # Single batch OCR call for all pages
        all_text_blocks = ocr_engine.predict_batch(rendered_images)

        pages = []
        for idx, i in enumerate(page_indices):
            text_blocks = all_text_blocks[idx] if idx < len(all_text_blocks) else []
            for block in text_blocks:
                block.page_num = i
            rect = doc[i].rect
            pages.append(PDFPage(
                page_num=i,
                text_blocks=text_blocks,
                image=rendered_images[idx],
                width=rect.width,
                height=rect.height,
            ))
    else:
        pages = []
        for i in page_indices:
            page = doc[i]
            text_blocks = extract_text_blocks(page, i)
            image = None
            if pdf_type == "scanned":
                image = render_page_to_image(page, scan_dpi)
            pages.append(PDFPage(
                page_num=i,
                text_blocks=text_blocks,
                image=image,
                width=page.rect.width,
                height=page.rect.height,
            ))

    result = PDFDocument(
        path=path,
        pages=pages,
        page_count=len(doc),
        pdf_type=pdf_type,
    )
    doc.close()
    return result


def is_diagram_or_table_page(page: PDFPage) -> bool:
    """Heuristic to detect pages dominated by diagrams/charts/tables.

    Such pages (star charts, horoscope wheels, data tables) have many
    short text labels scattered across the page rather than continuous
    reading paragraphs.

    Returns True if the page should skip text translation and only
    keep the original image.
    """
    blocks = page.text_blocks
    if len(blocks) < 5:
        return False

    page_area = page.width * page.height
    if page_area <= 0:
        return False

    # Feature 1: many short blocks = likely labels in diagrams/tables
    short_blocks = [b for b in blocks if len(b.text.strip()) < 20]
    short_ratio = len(short_blocks) / len(blocks)

    # Feature 2: text covers a large portion of the page area
    text_area = sum(b.width * b.height for b in blocks)
    coverage_ratio = text_area / page_area

    # Feature 3: average block length is very short
    avg_len = sum(len(b.text) for b in blocks) / len(blocks)

    # Many short labels scattered across the page
    if len(blocks) > 15 and short_ratio > 0.6 and avg_len < 25:
        return True

    # Dense text coverage = likely chart/table with many cells/labels
    if coverage_ratio > 0.5 and short_ratio > 0.5:
        return True

    return False


def merge_blocks_to_paragraphs(blocks: list[TextBlock]) -> list[TextBlock]:
    """Merge adjacent text blocks into paragraphs.

    Heuristic: if two blocks are close vertically (within 1.5x line height)
    and have similar font size, merge them.
    """
    if not blocks:
        return []

    paragraphs = []
    current = blocks[0]

    for block in blocks[1:]:
        # Check if blocks should be merged
        vertical_gap = block.y - (current.y + current.height)
        font_diff = abs(block.font_size - current.font_size)

        # Merge if close vertically and same font size
        if vertical_gap < current.font_size * 1.5 and font_diff < 1.0:
            current = TextBlock(
                text=current.text + " " + block.text,
                x=min(current.x, block.x),
                y=current.y,
                width=max(current.x + current.width, block.x + block.width) - min(current.x, block.x),
                height=block.y + block.height - current.y,
                font_size=max(current.font_size, block.font_size),
                is_bold=current.is_bold or block.is_bold,
                page_num=current.page_num,
            )
        else:
            paragraphs.append(current)
            current = block

    paragraphs.append(current)
    return paragraphs
