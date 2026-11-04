"""Robust digital-vs-scanned detection for PDFs.

The previous heuristic looked at the text *block area* of the first three pages,
which misreads two common cases:

* a scanned page with a thin hidden OCR layer (a few characters of garbage) —
  text area is tiny but present, so it was called "digital";
* a digital page whose body text sits in a large image (cover, chart page) —
  text area looks fine, but the page is mostly a picture.

This module combines independent per-page signals across the whole document
(sampled evenly, not just the first pages):

  * characters of extractable text
  * how much of the page is covered by placed images
  * number of vector drawings (scanned pages usually have none)
  * readability of the extracted text (reuses is_readable_text)

and reports per-page kinds plus an overall verdict with the evidence attached,
so the log explains *why* a PDF was treated as scanned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from python_engine.pdf_processor import is_readable_text

logger = logging.getLogger(__name__)

PageKind = Literal["text", "image", "unknown"]

# Tunables (kept explicit so the log can show what was applied).
MIN_TEXT_CHARS = 120      # a "real" text page normally has at least this many
MAX_IMAGE_CHARS = 60      # an image-only page carries almost no text
MIN_IMAGE_RATIO = 0.40    # …and images cover a substantial part of the page
SCANNED_PAGE_RATIO = 0.8  # ≥80% image pages ⇒ scanned document
DIGITAL_PAGE_RATIO = 0.8  # ≥80% text pages  ⇒ digital document


@dataclass
class PageSignal:
    page_num: int
    chars: int
    image_ratio: float
    drawings: int
    readable: bool
    kind: PageKind


@dataclass
class ScanReport:
    is_scanned: bool
    confidence: float
    total_pages: int
    sampled: list[PageSignal] = field(default_factory=list)
    reason: str = ""

    @property
    def image_pages(self) -> int:
        return sum(1 for p in self.sampled if p.kind == "image")

    @property
    def text_pages(self) -> int:
        return sum(1 for p in self.sampled if p.kind == "text")

    def summary(self) -> str:
        order = {p.page_num: p for p in self.sampled}
        pages = ",".join(str(n + 1) for n in sorted(order))
        kinds = "".join(
            {"text": "T", "image": "I", "unknown": "?"}[order[n].kind] for n in sorted(order)
        )
        verdict = "scanned" if self.is_scanned else "digital"
        return (
            f"{verdict} PDF ({self.reason}); sampled pages [{pages}] -> {kinds} "
            f"(T=text, I=image, ?=unclear)"
        )


def _image_coverage(page: pymupdf.Page) -> float:
    """Fraction of the page area covered by placed images (union, capped at 1)."""
    page_area = abs(page.rect.width * page.rect.height)
    if page_area <= 0:
        return 0.0
    covered = 0.0
    bboxes: list[tuple[float, float, float, float]] = []
    try:
        for info in page.get_image_info():
            if info.get("bbox"):
                bboxes.append(tuple(info["bbox"]))
    except Exception:  # pragma: no cover - malformed page
        pass
    # Fallback: some producers only expose images as dict blocks.
    if not bboxes:
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1 and block.get("bbox"):
                    bboxes.append(tuple(block["bbox"]))
        except Exception:  # pragma: no cover
            pass
    for bbox in bboxes:
        x0, y0, x1, y1 = bbox
        w = max(0.0, min(x1, page.rect.x1) - max(x0, page.rect.x0))
        h = max(0.0, min(y1, page.rect.y1) - max(y0, page.rect.y0))
        covered += w * h
    return min(1.0, covered / page_area)


def _classify(signal: PageSignal) -> PageKind:
    if signal.chars >= MIN_TEXT_CHARS and signal.readable:
        return "text"
    # A page with (almost) no extractable text has to go through OCR, whatever
    # it looks like: scans with tiny/low-contrast images and pages whose text
    # was converted to vector outlines both end up here.
    if signal.chars <= MAX_IMAGE_CHARS:
        return "image"
    # Text present but unreadable (broken CMap / garbage OCR layer) with images.
    if signal.chars > 0 and not signal.readable and signal.image_ratio >= MIN_IMAGE_RATIO:
        return "image"
    if signal.chars >= MIN_TEXT_CHARS:
        return "text"
    return "unknown"


def _sample_indices(total: int, limit: int) -> list[int]:
    if total <= limit:
        return list(range(total))
    # Evenly spaced sample including first and last page.
    step = (total - 1) / (limit - 1)
    return sorted({round(i * step) for i in range(limit)})


def detect_scanned_pdf(path: str | Path, sample_limit: int = 24) -> ScanReport:
    """Decide whether a PDF is image-based (scanned) or text-based."""
    path = Path(path)
    with pymupdf.open(str(path)) as doc:
        total = doc.page_count
        if total == 0:
            return ScanReport(False, 0.0, 0, reason="empty document")

        signals: list[PageSignal] = []
        for idx in _sample_indices(total, sample_limit):
            page = doc[idx]
            text = page.get_text("text")
            chars = len(text.strip())
            signal = PageSignal(
                page_num=idx,
                chars=chars,
                image_ratio=_image_coverage(page),
                drawings=len(page.get_drawings()),
                readable=is_readable_text(text) if chars else False,
                kind="unknown",
            )
            signal.kind = _classify(signal)
            signals.append(signal)

    image_pages = sum(1 for s in signals if s.kind == "image")
    text_pages = sum(1 for s in signals if s.kind == "text")
    sampled = len(signals)
    image_ratio = image_pages / sampled

    if text_pages == 0:
        return ScanReport(True, 1.0, total, signals,
                          f"no sampled page carries extractable text ({image_pages}/{sampled} are image-only)")

    if image_ratio >= SCANNED_PAGE_RATIO:
        return ScanReport(True, image_ratio, total, signals,
                          f"{image_pages}/{sampled} sampled pages are image-only")
    if text_pages / sampled >= DIGITAL_PAGE_RATIO:
        return ScanReport(False, text_pages / sampled, total, signals,
                          f"{text_pages}/{sampled} sampled pages carry real text")

    # Mixed document: decide by which side dominates, and say so.
    if image_pages > text_pages:
        return ScanReport(True, image_ratio, total, signals,
                          f"mixed document, image pages dominate ({image_pages} vs {text_pages})")
    return ScanReport(False, text_pages / sampled, total, signals,
                      f"mixed document, text pages dominate ({text_pages} vs {image_pages})")
