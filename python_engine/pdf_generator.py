"""PDF generation for translated documents.

Uses reportlab to generate:
- Monolingual (translation only)
- Dual side-by-side (left-right columns)
- Alternating pages (original + translation on separate pages)
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from python_engine.config import OutputConfig


@dataclass
class ParagraphData:
    """A paragraph with original and translated text."""

    original: str
    translated: str
    is_heading: bool = False
    level: int = 0


@dataclass
class PageImage:
    """An image to embed (e.g., original scanned page)."""

    image_path: str
    caption: str = ""


@dataclass
class ContentBlock:
    """A content block that can be paragraph or image."""

    type: Literal["paragraph", "image"]
    paragraph: ParagraphData | None = None
    image: PageImage | None = None
    page_num: int = 0  # 0-based page index in the original document


def _register_chinese_font():
    """Register a Chinese font for reportlab. Tries common system fonts."""
    font_paths = [
        # macOS
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]

    for path in font_paths:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("CNFont", path))
                return "CNFont"
            except Exception:
                continue

    return "Helvetica"


def _create_styles():
    """Create paragraph styles for PDF generation."""
    styles = getSampleStyleSheet()
    cn_font = _register_chinese_font()

    # Chinese text style
    cn_normal = ParagraphStyle(
        "CN_Normal",
        parent=styles["Normal"],
        fontName=cn_font,
        fontSize=10,
        leading=16,
        spaceAfter=8,
    )

    cn_heading1 = ParagraphStyle(
        "CN_Heading1",
        parent=styles["Heading1"],
        fontName=cn_font,
        fontSize=16,
        leading=22,
        spaceAfter=12,
        textColor=colors.HexColor("#1a1a1a"),
    )

    cn_heading2 = ParagraphStyle(
        "CN_Heading2",
        parent=styles["Heading2"],
        fontName=cn_font,
        fontSize=13,
        leading=18,
        spaceAfter=10,
        textColor=colors.HexColor("#222222"),
    )

    cn_heading3 = ParagraphStyle(
        "CN_Heading3",
        parent=styles["Heading3"],
        fontName=cn_font,
        fontSize=11,
        leading=16,
        spaceAfter=8,
        textColor=colors.HexColor("#333333"),
    )

    cn_quote = ParagraphStyle(
        "CN_Quote",
        parent=styles["Normal"],
        fontName=cn_font,
        fontSize=10,
        leading=16,
        spaceAfter=8,
        leftIndent=12,
        textColor=colors.HexColor("#555555"),
    )

    en_normal = ParagraphStyle(
        "EN_Normal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=14,
        spaceAfter=8,
        textColor=colors.HexColor("#555555"),
    )

    en_heading = ParagraphStyle(
        "EN_Heading",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=18,
        spaceAfter=12,
        textColor=colors.HexColor("#444444"),
    )

    return {
        "cn_normal": cn_normal,
        "cn_heading": cn_heading1,
        "cn_heading1": cn_heading1,
        "cn_heading2": cn_heading2,
        "cn_heading3": cn_heading3,
        "cn_quote": cn_quote,
        "en_normal": en_normal,
        "en_heading": en_heading,
    }


def _escape_text(text: str) -> str:
    """Escape XML special characters for reportlab Paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _make_large_styles(styles: dict[str, ParagraphStyle]) -> None:
    """Replace styles dict entries with larger variants for dual PDF left side.

    Since tables are skipped (visible on the original scan at right), there's
    much more vertical space — we can use generous font sizes.
    """
    cn_font = _register_chinese_font()
    styles["cn_normal"] = ParagraphStyle(
        "CN_Large", fontName=cn_font,
        fontSize=12, leading=18, spaceAfter=10,
    )
    styles["cn_heading1"] = ParagraphStyle(
        "CN_LargeH1", fontName=cn_font,
        fontSize=17, leading=23, spaceAfter=12,
        textColor=colors.HexColor("#1a1a1a"),
    )
    styles["cn_heading2"] = ParagraphStyle(
        "CN_LargeH2", fontName=cn_font,
        fontSize=15, leading=21, spaceAfter=10,
        textColor=colors.HexColor("#222222"),
    )
    styles["cn_heading3"] = ParagraphStyle(
        "CN_LargeH3", fontName=cn_font,
        fontSize=13, leading=18, spaceAfter=8,
        textColor=colors.HexColor("#333333"),
    )
    styles["cn_quote"] = ParagraphStyle(
        "CN_LargeQuote", fontName=cn_font,
        fontSize=12, leading=18, spaceAfter=8, leftIndent=12,
    )



def generate_mono_pdf(
    blocks: list[ContentBlock],
    output_path: str | Path,
    translation_first: bool = True,
    translation_margin_cm: float = 3.5,
) -> Path:
    """Generate monolingual PDF (translation only).

    Args:
        blocks: Content blocks
        output_path: Output file path
        translation_first: If True, use translation text; else original
        translation_margin_cm: Page margin in cm. Larger values shrink the
            content box so e-readers can auto-crop and zoom in further.

    Returns:
        Path to generated PDF
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    margin = translation_margin_cm * cm
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    styles = _create_styles()
    story = []

    for block in blocks:
        if block.type == "paragraph" and block.paragraph:
            text = (
                block.paragraph.translated
                if translation_first
                else block.paragraph.original
            )
            style = (
                styles["cn_heading"]
                if block.paragraph.is_heading
                else styles["cn_normal"]
            )
            story.append(Paragraph(_escape_text(text), style))

        elif block.type == "image" and block.image:
            try:
                img = RLImage(
                    block.image.image_path,
                    width=16 * cm,
                    height=16 * cm,
                    kind="proportional",
                )
                story.append(img)
                if block.image.caption:
                    story.append(
                        Paragraph(
                            _escape_text(block.image.caption),
                            styles["cn_normal"],
                        )
                    )
            except Exception:
                pass  # Skip failed images

        story.append(Spacer(1, 0.3 * cm))

    doc.build(story)
    return output_path


def generate_dual_pdf(
    blocks: list[ContentBlock],
    output_path: str | Path,
    translation_first: bool = True,
    source_path: str | Path | None = None,
    translation_margin_cm: float = 3.5,
) -> Path:
    """Generate a dual spread PDF with original pages on the left.

    Left side: original PDF page rendered as an image. This preserves the
    exact original layout — images, cover design, centered text, charts.

    Right side: translated text for that page in a clean readable layout.

    Because both sides are derived from the same page number, the
    correspondence is guaranteed one-to-one.

    Args:
        blocks: Content blocks (must have page_num set)
        output_path: Output file path
        translation_first: If True, translation on left; else original on left
        source_path: Path to the original PDF (required to render left side)
        translation_margin_cm: Margin in cm around the translation text. Larger
            margins make the text-content box smaller, so e-readers (e.g.
            6-inch) auto-cropping white space can zoom in further.

    Returns:
        Path to generated PDF
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_w, page_h = A4
    text_margin = translation_margin_cm * cm
    text_frame_w = page_w - 2 * text_margin
    text_frame_h = page_h - 2 * text_margin
    styles = _create_styles()

    # Group text blocks by page number
    page_texts: dict[int, list[ContentBlock]] = {}
    for block in blocks:
        if block.type == "paragraph" and block.paragraph:
            page_texts.setdefault(block.page_num, []).append(block)

    c = pdfcanvas.Canvas(str(output_path), pagesize=(2 * page_w, page_h))

    src_doc = None
    if source_path:
        import fitz
        src_doc = fitz.open(str(source_path))

    for page_num in sorted(page_texts.keys()):
        # Decide which side gets the original image and which gets the text
        if translation_first:
            img_x = 0
            text_x = page_w + text_margin
        else:
            img_x = page_w
            text_x = text_margin

        # --- Draw original page as image (fills its half-page entirely) ---
        if src_doc and page_num < len(src_doc):
            page = src_doc[page_num]
            pix = page.get_pixmap(dpi=150)
            img_reader = ImageReader(io.BytesIO(pix.tobytes("jpeg")))
            c.drawImage(img_reader, img_x, 0, width=page_w, height=page_h)

        # --- Draw translated text ---
        text_blocks = page_texts[page_num]
        _draw_page_text(
            c, text_blocks, text_x, page_h - text_margin,
            text_frame_w, text_frame_h, styles, translation_first, is_original_side=not translation_first,
        )

        c.showPage()

    if src_doc:
        src_doc.close()

    c.save()
    return output_path


def _draw_page_text(
    canvas,
    blocks: list[ContentBlock],
    x: float,
    y_top: float,
    width: float,
    height: float,
    styles: dict,
    translation_first: bool,
    is_original_side: bool,
) -> None:
    """Draw translated (or original) text blocks from top to bottom."""
    y = y_top

    for block in blocks:
        if not (block.type == "paragraph" and block.paragraph):
            continue

        para = block.paragraph
        # Decide which text to show
        if is_original_side:
            text = para.original
            style = (
                styles["en_heading"] if para.is_heading else styles["en_normal"]
            ) if translation_first else (
                styles["cn_heading"] if para.is_heading else styles["cn_normal"]
            )
        else:
            text = para.translated
            style = (
                styles["cn_heading"] if para.is_heading else styles["cn_normal"]
            ) if translation_first else (
                styles["en_heading"] if para.is_heading else styles["en_normal"]
            )

        if not text:
            continue

        p = Paragraph(_escape_text(text), style)
        w, h = p.wrap(width, height)
        y -= h
        p.drawOn(canvas, x, y)
        y -= 0.3 * cm  # inter-paragraph spacer

def generate_vision_dual_pdf(
    pages_data: list[tuple[int, str, "Image.Image"]],
    output_path: str | Path,
    translation_margin_cm: float = 3.5,
) -> Path:
    """Generate a dual spread PDF for vision OCR mode.

    Left side: translated text paragraphs only (no tables — they are
    visible on the original scan at right).  Skips Markdown tables
    entirely so the text can use a larger, more readable font while
    still fitting within the page.

    Right side: original scanned page image.

    Args:
        pages_data: List of (page_num, translated_markdown, original_image)
        output_path: Output file path
        translation_margin_cm: Margin in cm around the translation text. Larger
            margins give e-readers more white space to auto-crop, so the actual
            text appears bigger on small screens (e.g. 6-inch devices).

    Returns:
        Path to generated PDF
    """
    from python_engine.md_renderer import markdown_to_story

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_w, page_h = A4
    text_margin = translation_margin_cm * cm
    frame_w = page_w - 2 * text_margin
    styles = _create_styles()

    c = pdfcanvas.Canvas(str(output_path), pagesize=(2 * page_w, page_h))

    for page_num, md_text, image in pages_data:
        # --- Left side: translated text (no tables — original scan on right) ---
        left_x = text_margin
        left_y_top = page_h - text_margin

        # Skip tables since the right-side original scan already shows them.
        # Use large font since tables are removed.
        _make_large_styles(styles)
        story = markdown_to_story(md_text, styles, frame_w, compact=False, skip_tables=True)
        y = left_y_top
        for flowable in story:
            if y < text_margin + 20:
                break  # Out of space on this page
            w, h = flowable.wrap(frame_w, y - text_margin)
            if isinstance(flowable, Paragraph):
                y -= h
                flowable.drawOn(c, left_x, y)
            elif hasattr(flowable, 'drawOn'):
                y -= h
                flowable.drawOn(c, left_x, y)
            else:
                y -= h
                if hasattr(flowable, 'wrap'):
                    flowable.wrap(frame_w, y - text_margin)
                    flowable.drawOn(c, left_x, y)

        # --- Right side: original scanned image ---
        right_x = page_w
        if image:
            # Embed as JPEG to keep file size reasonable (PNG lossless would
            # blow up a 19 MB scan to 300+ MB).
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=60)
            buf.seek(0)
            img_reader = ImageReader(buf)
            c.drawImage(img_reader, right_x, 0, width=page_w, height=page_h)

        c.showPage()

    c.save()
    return output_path


def generate_vision_dual_pdf_v2(
    pages_data: list[tuple[int, str]],
    source_path: str | Path,
    output_path: str | Path,
    translation_margin_cm: float = 3.5,
) -> Path:
    """Generate a dual spread PDF using PyMuPDF for image embedding.

    Unlike the reportlab-only version, this preserves the original page
    images' native compression (e.g. JPEG2000), keeping the output file
    size close to the original PDF — typically only slightly larger than
    the source because the left side adds vector text.

    Flow:
      1. Create a text-only A4 PDF with reportlab (Chinese translation).
      2. Use PyMuPDF to build the final 2×A4 PDF:
         - left half  = show the text page
         - right half = show the original source page (embeds as XObject,
           preserving JPEG2000 and all other native compression)
    """
    import fitz
    from python_engine.md_renderer import markdown_to_story

    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_w, page_h = A4
    text_margin = translation_margin_cm * cm
    frame_w = page_w - 2 * text_margin
    styles = _create_styles()

    # ------------------------------------------------------------------
    # Step 1 — text-only PDF (A4) via reportlab
    # ------------------------------------------------------------------
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        text_pdf_path = Path(f.name)

    c = pdfcanvas.Canvas(str(text_pdf_path), pagesize=A4)

    for _page_num, md_text in pages_data:
        left_x = text_margin
        left_y_top = page_h - text_margin

        _make_large_styles(styles)
        story = markdown_to_story(
            md_text, styles, frame_w, compact=False, skip_tables=True
        )
        y = left_y_top
        for flowable in story:
            if y < text_margin + 20:
                break  # Out of space — truncate
            _w, h = flowable.wrap(frame_w, y - text_margin)
            if isinstance(flowable, Paragraph):
                y -= h
                flowable.drawOn(c, left_x, y)
            elif hasattr(flowable, "drawOn"):
                y -= h
                flowable.drawOn(c, left_x, y)
            else:
                y -= h
                if hasattr(flowable, "wrap"):
                    flowable.wrap(frame_w, y - text_margin)
                    flowable.drawOn(c, left_x, y)

        c.showPage()

    c.save()

    # ------------------------------------------------------------------
    # Step 2 — merge text + original images side-by-side via PyMuPDF
    # ------------------------------------------------------------------
    src_doc = fitz.open(str(source_path))
    text_doc = fitz.open(str(text_pdf_path))
    out_doc = fitz.open()

    try:
        for i, (page_num, _md_text) in enumerate(pages_data):
            new_page = out_doc.new_page(width=2 * page_w, height=page_h)

            # Left half: translated text page
            if i < len(text_doc):
                new_page.show_pdf_page(
                    fitz.Rect(0, 0, page_w, page_h), text_doc, i
                )

            # Right half: show the original page directly.
            # Using show_pdf_page() instead of insert_image() because
            # insert_image() corrupts JPEG2000 images (PyMuPDF limitation).
            # show_pdf_page() embeds the entire source page as an XObject,
            # preserving the original image perfectly.
            if page_num < len(src_doc):
                new_page.show_pdf_page(
                    fitz.Rect(page_w, 0, 2 * page_w, page_h),
                    src_doc,
                    page_num,
                )

        out_doc.save(str(output_path))
    finally:
        out_doc.close()
        src_doc.close()
        text_doc.close()
        text_pdf_path.unlink(missing_ok=True)

    return output_path


def generate_alternating_pdf(
    blocks: list[ContentBlock],
    output_path: str | Path,
    translation_first: bool = True,
    translation_margin_cm: float = 3.5,
) -> Path:
    """Generate alternating pages PDF.

    All translation pages first, then all original pages (or vice versa).

    Args:
        blocks: Content blocks
        output_path: Output file path
        translation_first: If True, translation pages first
        translation_margin_cm: Page margin in cm.

    Returns:
        Path to generated PDF
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    margin = translation_margin_cm * cm
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    styles = _create_styles()
    story = []

    # Helper to add blocks for a language
    def add_blocks(use_translation: bool):
        for block in blocks:
            if block.type == "paragraph" and block.paragraph:
                text = (
                    block.paragraph.translated
                    if use_translation
                    else block.paragraph.original
                )
                style = (
                    styles["cn_heading"]
                    if block.paragraph.is_heading
                    else styles["cn_normal"]
                )
                story.append(Paragraph(_escape_text(text), style))
            elif block.type == "image" and block.image:
                try:
                    img = RLImage(
                        block.image.image_path,
                        width=16 * cm,
                        height=10 * cm,
                        kind="proportional",
                    )
                    story.append(img)
                except Exception:
                    pass
            story.append(Spacer(1, 0.3 * cm))
        # Page break
        story.append(
            Paragraph("<pageBreak/>", styles["cn_normal"])
        )

    if translation_first:
        add_blocks(True)   # Translation pages
        add_blocks(False)  # Original pages
    else:
        add_blocks(False)  # Original pages
        add_blocks(True)   # Translation pages

    doc.build(story)
    return output_path


def generate_pdf(
    blocks: list[ContentBlock],
    output_path: str | Path,
    config: OutputConfig,
    source_path: str | Path | None = None,
) -> Path:
    """Generate PDF based on output configuration.

    Args:
        blocks: Content blocks
        output_path: Output file path
        config: Output configuration
        source_path: Optional path to the original PDF (used for dual mode)

    Returns:
        Path to generated PDF
    """
    mode = config.mode
    translation_first = config.dual_lang_order == "translation_first"
    margin_cm = getattr(config, "translation_margin_cm", 3.5)

    if mode == "mono":
        return generate_mono_pdf(
            blocks, output_path, translation_first=True,
            translation_margin_cm=margin_cm,
        )
    elif mode == "dual":
        return generate_dual_pdf(
            blocks, output_path, translation_first, source_path,
            translation_margin_cm=margin_cm,
        )
    elif mode == "alternating":
        return generate_alternating_pdf(
            blocks, output_path, translation_first,
            translation_margin_cm=margin_cm,
        )
    else:
        raise ValueError(f"Unknown output mode: {mode}")
