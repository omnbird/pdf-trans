"""Core translation engine.

Orchestrates PDF processing, translation, and output generation.
Works with both digital and scanned PDFs.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Callable

from python_engine.config import EngineConfig
from python_engine.pdf_generator import ContentBlock, PageImage, ParagraphData, generate_pdf
from python_engine.md_generator import generate_markdown
from python_engine.ocr_engine import PaddleOCREngine
from python_engine.pdf_processor import PDFDocument, is_diagram_or_table_page, is_readable_text, merge_blocks_to_paragraphs, parse_page_ranges, process_pdf
from python_engine.translator import Translator

logger = logging.getLogger(__name__)


class TranslationEngine:
    """Main translation engine."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.translator = Translator(config.translation)
        # Only load the OCR model when the user has explicitly marked the PDF
        # as scanned AND chose the local OCR backend. Loading it for digital
        # PDFs is wasted work.
        self.ocr_engine = (
            PaddleOCREngine(
                language=config.scanned.ocr_language,
                on_loading=lambda msg: self._report_progress("processing", 5, 100, msg),
            )
            if config.scanned.is_scanned and config.scanned.mode == "ocr"
            else None
        )

    def _report_progress(self, stage: str, current: int, total: int, message: str = "") -> None:
        """Report progress as JSON to stdout (for Tauri integration)."""
        event = {
            "type": "progress",
            "stage": stage,
            "current": current,
            "total": total,
            "message": message,
        }
        print(json.dumps(event), flush=True)

    def _report_finish(self, output_files: dict[str, str]) -> None:
        """Report completion with output file paths."""
        event = {
            "type": "finish",
            "output_files": output_files,
        }
        print(json.dumps(event), flush=True)

    def _report_error(self, error: str) -> None:
        """Report error."""
        event = {"type": "error", "message": error}
        print(json.dumps(event), flush=True)

    def _build_content_blocks(self, doc: PDFDocument) -> list[ContentBlock]:
        """Build content blocks from PDF document.

        For digital PDFs: extract and merge paragraphs.
        For scanned PDFs: keep original images and filter OCR garbage.
        """
        blocks = []
        filtered_count = 0
        total_text_count = 0

        for page in doc.pages:
            # For scanned PDFs, include original page image
            if doc.pdf_type == "scanned" and page.image and self.config.output.keep_original_image:
                # Save page image to temp
                temp_dir = Path(self.config.temp_dir)
                temp_dir.mkdir(parents=True, exist_ok=True)
                img_path = temp_dir / f"page_{page.page_num:04d}.jpg"
                page.image.save(img_path, "JPEG", quality=85)
                blocks.append(ContentBlock(
                    type="image",
                    image=PageImage(
                        image_path=str(img_path),
                        caption=f"Page {page.page_num + 1}",
                    ),
                    page_num=page.page_num,
                ))

            # For scanned PDFs: detect diagram/chart/table pages and skip translation
            if doc.pdf_type == "scanned" and is_diagram_or_table_page(page):
                self._report_progress(
                    "processing", 15, 100,
                    f"Page {page.page_num + 1} looks like a diagram/chart/table — skipping text translation"
                )
                continue

            # Merge text blocks into paragraphs
            paragraphs = merge_blocks_to_paragraphs(page.text_blocks)

            for para in paragraphs:
                total_text_count += 1
                # For scanned PDFs, skip text that looks like OCR garbage
                if doc.pdf_type == "scanned" and not is_readable_text(para.text):
                    filtered_count += 1
                    continue

                is_heading = para.font_size > 14 or para.is_bold
                level = 1 if is_heading else 0

                blocks.append(ContentBlock(
                    type="paragraph",
                    paragraph=ParagraphData(
                        original=para.text,
                        translated="",  # Will be filled later
                        is_heading=is_heading,
                        level=level,
                    ),
                    page_num=page.page_num,
                ))

        if doc.pdf_type == "scanned" and filtered_count > 0:
            pct = int(100 * filtered_count / total_text_count) if total_text_count else 0
            self._report_progress(
                "processing", 20, 100,
                f"Detected {doc.pdf_type} PDF, {len(doc.pages)} pages selected, filtered {filtered_count}/{total_text_count} ({pct}%) unreadable OCR segments"
            )

        return blocks

    async def _run_mistral_pipeline(
        self,
        input_path: Path,
    ) -> dict[str, str]:
        """Run Mistral OCR + translation pipeline for scanned PDFs.

        Uses Mistral's dedicated OCR API, then translates each page's
        Markdown via DeepSeek. Supports page range slicing and automatic
        chunking when the PDF exceeds Mistral's 1000-page upload limit.

        The entire pipeline is self-contained — it does not use process_pdf().
        """
        from python_engine.mistral_client import MistralOCRClient
        import asyncio
        import fitz
        from PIL import Image

        output_files: dict[str, str] = {}
        logger.info("[Pipeline] Mistral pipeline starting: %s", input_path)

        # Parse page ranges
        try:
            doc = fitz.open(input_path)
            total_pages = len(doc)
            page_indices = parse_page_ranges(self.config.pages, total_pages)
            doc.close()
            logger.info("[Pipeline] PDF parsed: total_pages=%d, selected=%d, pages=%s", total_pages, len(page_indices), page_indices[:10] if len(page_indices) <= 10 else f"{page_indices[0]}..{page_indices[-1]}")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Failed to parse PDF %s: %s\n%s", input_path, err_msg, traceback.format_exc())
            self._report_error(f"Failed to parse PDF: {err_msg}")
            return output_files

        if not page_indices:
            logger.warning("[Pipeline] No pages selected")
            self._report_error("No pages selected")
            return output_files

        api_key = self.config.scanned.mistral_api_key
        if not api_key:
            logger.warning("[Pipeline] Mistral API key not configured")
            self._report_error("Mistral API key not configured. Set it in Settings > Scanned PDF.")
            return output_files

        logger.info("[Pipeline] API key present (len=%d), creating client", len(api_key))
        self._report_progress(
            "processing", 5, 100,
            f"Processing {len(page_indices)} pages via Mistral OCR...",
        )

        client = MistralOCRClient(api_key=api_key)

        # process_pages handles chunking (splitting into ≤1000-page uploads)
        try:
            mistral_pages = await client.process_pages(
                input_path,
                page_indices,
                on_progress=lambda msg: self._report_progress("processing", 10, 100, msg),
            )
            logger.info("[Pipeline] Mistral OCR returned %d pages", len(mistral_pages))
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Mistral OCR failed: %s\n%s", err_msg, traceback.format_exc())
            self._report_error(f"Mistral OCR failed: {err_msg}")
            return output_files

        self._report_progress(
            "processing", 40, 100,
            f"Mistral OCR complete ({len(mistral_pages)} pages). Starting translation...",
        )

        # Translate each page's Markdown via DeepSeek
        mistral_translation_prompt = (
            "Translate the following Markdown content into Chinese. "
            "Preserve ALL Markdown syntax exactly: headings (# ## ###), tables (|), lists (- *). "
            "Do NOT translate Sanskrit proper nouns unless a standard Chinese name exists. "
            "Output ONLY the translated Markdown, no explanations."
        )
        logger.info("[Pipeline] Translating %d pages via DeepSeek...", len(mistral_pages))

        async def _translate_one(page_num: int, md: str, idx: int) -> tuple[int, str]:
            logger.debug("[Pipeline] Translating page %d/%d (page_num=%d, md_len=%d)", idx + 1, len(mistral_pages), page_num + 1, len(md))
            try:
                result = await self.translator._translate_single(
                    md,
                    context=mistral_translation_prompt,
                )
                logger.debug("[Pipeline] Translated page %d/%d (page_num=%d, result_len=%d)", idx + 1, len(mistral_pages), page_num + 1, len(result.translated))
                self._report_progress(
                    "translating",
                    40 + int(50 * (idx + 1) / len(mistral_pages)),
                    100,
                    f"Translated page {page_num + 1}/{total_pages}",
                )
                return page_num, result.translated
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
                logger.error("[Pipeline] Translation failed for page %d: %s\n%s", page_num + 1, err_msg, traceback.format_exc())
                raise

        try:
            translated_results = await asyncio.gather(*[
                _translate_one(num, md, i) for i, (num, md) in enumerate(mistral_pages)
            ])
            translated_results.sort(key=lambda x: x[0])
            logger.info("[Pipeline] Translation complete: %d pages", len(translated_results))
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Translation batch failed: %s", err_msg)
            self._report_error(f"Translation failed: {err_msg}")
            return output_files

        self._report_progress("generating", 90, 100, "Generating output files...")
        logger.info("[Pipeline] Generating output files...")

        # Prepare output paths
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_name = input_path.stem

        temp_dir = Path(self.config.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Extract original page images for Markdown references.
        # We resize to 120 DPI and JPEG q60 to keep Markdown side-car files
        # small (originals are JPEG2000 ~5-10x more efficient than JPEG).
        try:
            doc = fitz.open(input_path)
            logger.info("[Pipeline] Extracting images from %d pages...", len(mistral_pages))
            for page_num, _ in mistral_pages:
                page = doc[page_num]
                image_list = page.get_images(full=True)
                if image_list:
                    largest = max(image_list, key=lambda img: img[2] * img[3])
                    info = doc.extract_image(largest[0])
                    img = Image.open(io.BytesIO(info["image"]))
                    target_w = int(page.rect.width * 120 / 72)
                    target_h = int(page.rect.height * 120 / 72)
                    img = img.resize((target_w, target_h), Image.LANCZOS)
                else:
                    pix = page.get_pixmap(dpi=120)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img_path = temp_dir / f"mistral_page_{page_num:04d}.jpg"
                img.save(img_path, "JPEG", quality=60)
            doc.close()
            logger.info("[Pipeline] Images extracted to %s", temp_dir)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Image extraction failed: %s\n%s", err_msg, traceback.format_exc())
            self._report_error(f"Image extraction failed: {err_msg}")
            return output_files

        # 1. Original Mistral OCR Markdown
        original_md_path = output_dir / f"{base_name}_mistral_original.md"
        logger.info("[Pipeline] Writing original markdown: %s", original_md_path)
        try:
            with open(original_md_path, "w", encoding="utf-8") as f:
                f.write(f"# {base_name} (Mistral OCR)\n\n")
                for page_num, md in mistral_pages:
                    f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                    f.write(md)
                    f.write("\n")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Failed to write original markdown: %s", err_msg)

        # 2. Translated Markdown
        translated_md_path = output_dir / f"{base_name}_mistral_translated.md"
        logger.info("[Pipeline] Writing translated markdown: %s", translated_md_path)
        try:
            with open(translated_md_path, "w", encoding="utf-8") as f:
                f.write(f"# {base_name} (Translation)\n\n")
                for page_num, md in translated_results:
                    f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                    f.write(md)
                    f.write("\n")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Failed to write translated markdown: %s", err_msg)

        # 3. Dual Markdown (original image + translated text)
        dual_md_path = output_dir / f"{base_name}_mistral_dual.md"
        logger.info("[Pipeline] Writing dual markdown: %s", dual_md_path)
        try:
            with open(dual_md_path, "w", encoding="utf-8") as f:
                f.write(f"# {base_name} (Dual)\n\n")
                f.write("> Each section shows the original scanned page followed by its translation.\n\n")
                for i, (page_num, _) in enumerate(mistral_pages):
                    img_path = temp_dir / f"mistral_page_{page_num:04d}.jpg"
                    f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                    f.write("### Original Page\n\n")
                    f.write(f"![Page {page_num + 1}]({img_path})\n\n")
                    f.write("### Translation\n\n")
                    f.write(translated_results[i][1])
                    f.write("\n")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
            logger.error("[Pipeline] Failed to write dual markdown: %s", err_msg)

        output_files["markdown"] = str(translated_md_path)
        output_files["markdown_original"] = str(original_md_path)
        output_files["markdown_dual"] = str(dual_md_path)
        logger.info("[Pipeline] Markdown outputs: %s", list(output_files.keys()))

        # 4. Dual PDF — PyMuPDF hybrid to preserve original image compression
        if "pdf" in self.config.output.formats:
            from python_engine.pdf_generator import generate_vision_dual_pdf_v2

            pdf_path = output_dir / f"{base_name}_mistral_translated.pdf"
            pages_data = [
                (page_num, md) for page_num, md in translated_results
            ]
            logger.info("[Pipeline] Generating dual PDF: %s (%d pages)", pdf_path, len(pages_data))
            try:
                generate_vision_dual_pdf_v2(
                    pages_data,
                    source_path=input_path,
                    output_path=pdf_path,
                    translation_margin_cm=self.config.output.translation_margin_cm,
                )
                output_files["pdf"] = str(pdf_path)
                logger.info("[Pipeline] PDF generated: %s", pdf_path)
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
                logger.error("[Pipeline] PDF generation failed: %s\n%s", err_msg, traceback.format_exc())

        logger.info("[Pipeline] Mistral pipeline finished. Output files: %s", output_files)
        return output_files

    async def _run_vision_pipeline(
        self,
        input_path: Path,
        doc: PDFDocument,
    ) -> dict[str, str]:
        """Run vision-based OCR + translation pipeline for scanned PDFs.

        1. Send each page image to a multi-modal model (Gemini/GPT-4o) for OCR
        2. Model returns structured Markdown (headings, tables, paragraphs)
        3. Translate each page's Markdown with DeepSeek (preserving syntax)
        4. Output: original MD, translated MD, and dual MD with images
        """
        import asyncio
        from python_engine.vision_client import VisionClient

        output_files: dict[str, str] = {}
        vision = VisionClient(self.config.vision)

        total_pages = len(doc.pages)

        # Step 1: OCR all pages to Markdown (concurrent)
        self._report_progress("processing", 10, 100, f"OCR {total_pages} pages with vision model...")

        page_images = [(p.page_num, p.image) for p in doc.pages if p.image]

        async def _ocr_one(page_num: int, image: Image.Image) -> tuple[int, str]:
            try:
                md = await asyncio.wait_for(
                    vision.ocr_page(
                        image,
                        page_num=page_num,
                        on_progress=lambda msg: self._report_progress(
                            "processing", 10 + int(30 * page_num / total_pages), 100, msg
                        ),
                    ),
                    timeout=120.0,
                )
                return page_num, md
            except asyncio.TimeoutError:
                self._report_progress(
                    "processing", 10 + int(30 * page_num / total_pages), 100,
                    f"Page {page_num + 1} vision OCR timed out, skipping"
                )
                return page_num, ""

        ocr_results = await asyncio.gather(*[
            _ocr_one(num, img) for num, img in page_images
        ])
        ocr_results.sort(key=lambda x: x[0])

        self._report_progress("processing", 40, 100, "OCR complete. Starting translation...")

        # Step 2: Translate each page's Markdown (concurrent via rate limiter)
        md_translation_prompt = (
            "Translate the following Markdown content into Chinese. "
            "Preserve ALL Markdown syntax exactly: headings (# ## ###), tables (|), lists (- *), "
            "and [DIAGRAM: ...] placeholders. "
            "Do NOT translate content inside [DIAGRAM: ...] placeholders. "
            "Do NOT translate Sanskrit proper nouns unless a standard Chinese name exists. "
            "Output ONLY the translated Markdown, no explanations."
        )

        async def _translate_one(page_num: int, md: str, idx: int) -> tuple[int, str]:
            result = await self.translator._translate_single(
                md,
                context=md_translation_prompt,
            )
            self._report_progress(
                "translating",
                40 + int(50 * (idx + 1) / len(ocr_results)),
                100,
                f"Translated page {page_num + 1}/{doc.page_count}",
            )
            return page_num, result.translated

        translated_results = await asyncio.gather(*[
            _translate_one(num, md, i) for i, (num, md) in enumerate(ocr_results)
        ])
        translated_results.sort(key=lambda x: x[0])

        self._report_progress("generating", 90, 100, "Generating Markdown outputs...")

        # Step 3: Generate output files
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_name = input_path.stem

        # Save page images for dual markdown
        temp_dir = Path(self.config.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        for page in doc.pages:
            if page.image:
                img_path = temp_dir / f"page_{page.page_num:04d}.jpg"
                page.image.save(img_path, "JPEG", quality=85)

        # 3a. Original Markdown
        original_md_path = output_dir / f"{base_name}_vision_original.md"
        with open(original_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {base_name}\n\n")
            for page_num, md in ocr_results:
                f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                f.write(md)
                f.write("\n")

        # 3b. Translated Markdown
        translated_md_path = output_dir / f"{base_name}_vision_translated.md"
        with open(translated_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {base_name} (Translation)\n\n")
            for page_num, md in translated_results:
                f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                f.write(md)
                f.write("\n")

        # 3c. Dual Markdown (original image + translated text, page by page)
        dual_md_path = output_dir / f"{base_name}_vision_dual.md"
        with open(dual_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {base_name} (Dual)\n\n")
            f.write("> Each section shows the original scanned page followed by its translation.\n\n"
            )
            for i, (page_num, _) in enumerate(ocr_results):
                img_path = temp_dir / f"page_{page_num:04d}.jpg"
                f.write(f"\n---\n\n## Page {page_num + 1}\n\n")
                f.write(f"### Original Page\n\n")
                f.write(f"![Page {page_num + 1}]({img_path})\n\n")
                f.write(f"### Translation\n\n")
                f.write(translated_results[i][1])
                f.write("\n")

        output_files["markdown"] = str(translated_md_path)
        output_files["markdown_original"] = str(original_md_path)
        output_files["markdown_dual"] = str(dual_md_path)

        # Step 4: Generate dual PDF if requested
        # Left side: translated Markdown (headings, tables, paragraphs)
        # Right side: original scanned page image
        if "pdf" in self.config.output.formats:
            from python_engine.pdf_generator import generate_vision_dual_pdf

            pdf_path = output_dir / f"{base_name}_vision_translated.pdf"
            pages_data = []
            for page in doc.pages:
                page_md = ""
                for num, md in translated_results:
                    if num == page.page_num:
                        page_md = md
                        break
                pages_data.append((page.page_num, page_md, page.image))

            generate_vision_dual_pdf(
                pages_data,
                pdf_path,
                translation_margin_cm=self.config.output.translation_margin_cm,
            )
            output_files["pdf"] = str(pdf_path)

        return output_files

    async def _run_babeldoc_pipeline(
        self,
        input_path: Path,
    ) -> dict[str, str]:
        """Run BabelDOC for layout-preserving translation of digital PDFs.

        Returns:
            Dictionary with output file paths
        """
        from python_engine.babeldoc_engine import translate_with_babeldoc

        def progress_callback(stage: str, current: int, total: int, message: str) -> None:
            self._report_progress("translating", current, total, message)

        return await translate_with_babeldoc(
            input_path,
            self.config,
            progress_callback=progress_callback,
        )

    async def _run_legacy_pipeline(
        self,
        input_path: Path,
        doc: PDFDocument,
        skip_pdf: bool = False,
    ) -> dict[str, str]:
        """Run legacy translation pipeline (for scanned PDFs or markdown output).

        Args:
            input_path: Input PDF path
            doc: Processed PDF document
            skip_pdf: If True, skip PDF generation (BabelDOC already handled it)

        Returns:
            Dictionary with output file paths
        """
        output_files: dict[str, str] = {}

        # Step 2: Build content blocks
        blocks = self._build_content_blocks(doc)
        text_blocks = [b for b in blocks if b.type == "paragraph" and b.paragraph]

        if not text_blocks:
            self._report_error("No text found in PDF")
            return output_files

        # Step 3: Extract texts for translation
        texts_to_translate = [b.paragraph.original for b in text_blocks]

        # Step 4: Translate
        self._report_progress("translating", 30, 100, f"Translating {len(texts_to_translate)} segments...")

        def progress_callback(current: int, total: int):
            pct = 30 + int(60 * current / total)
            self._report_progress("translating", pct, 100, f"Translated {current}/{total}")

        results = await self.translator.translate_batch(
            texts=texts_to_translate,
            context="Astrology and Vedic texts",
            on_progress=progress_callback,
        )

        # Step 5: Fill translated text back into blocks
        for i, result in enumerate(results):
            text_blocks[i].paragraph.translated = result.translated

        self._report_progress("generating", 90, 100, "Generating output files...")

        # Step 6: Generate outputs
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        base_name = input_path.stem

        for fmt in self.config.output.formats:
            if fmt == "pdf" and not skip_pdf:
                pdf_path = output_dir / f"{base_name}_translated.pdf"
                generate_pdf(blocks, pdf_path, self.config.output, source_path=input_path)
                output_files["pdf"] = str(pdf_path)

            elif fmt == "markdown":
                md_path = output_dir / f"{base_name}_translated.md"
                generate_markdown(blocks, md_path, self.config.output, image_dir=self.config.temp_dir)
                output_files["markdown"] = str(md_path)

        return output_files

    async def translate_document(
        self,
        input_path: str | Path,
        on_progress: Callable | None = None,
    ) -> dict[str, str]:
        """Translate a PDF document.

        Args:
            input_path: Path to input PDF
            on_progress: Optional progress callback(current, total, stage)

        Returns:
            Dictionary of output format -> file path
        """
        input_path = Path(input_path)

        # Mistral OCR pipeline — only used when the user marks the PDF as scanned
        if self.config.scanned.is_scanned and self.config.scanned.mode == "mistral_ocr":
            logger.info("[Engine] Starting Mistral OCR pipeline for %s", input_path)
            self._report_progress("processing", 0, 100, "Starting Mistral OCR pipeline...")
            try:
                mistral_outputs = await self._run_mistral_pipeline(input_path)
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
                logger.error("[Engine] Mistral pipeline crashed: %s\n%s", err_msg, traceback.format_exc())
                self._report_error(f"Mistral pipeline crashed: {err_msg}")
                return {}

            if not mistral_outputs:
                logger.warning("[Engine] Mistral pipeline returned no outputs")
                self._report_error("No output files were generated")
                return {}

            self._report_progress("generating", 100, 100, "Done!")
            self._report_finish(mistral_outputs)
            logger.info("[Engine] Mistral pipeline finished: %s", mistral_outputs)
            return mistral_outputs

        # Step 1: Process PDF
        self._report_progress("processing", 0, 100, "Processing PDF...")
        is_scanned = self.config.scanned.is_scanned
        doc = process_pdf(
            input_path,
            scan_dpi=self.config.scanned.dpi,
            pages_str=self.config.pages,
            ocr_engine=self.ocr_engine if is_scanned else None,
            is_scanned=is_scanned,
        )
        self._report_progress(
            "processing", 20, 100,
            f"{'Scanned' if is_scanned else 'Digital'} PDF, {len(doc.pages)} pages selected",
        )

        output_files: dict[str, str] = {}
        babeldoc_success = False

        # For digital PDFs with PDF output: use BabelDOC (layout-preserving)
        if doc.pdf_type == "digital" and "pdf" in self.config.output.formats:
            try:
                self._report_progress("translating", 25, 100, "Starting BabelDOC layout-preserving translation...")
                babeldoc_outputs = await self._run_babeldoc_pipeline(input_path)
                output_files.update(babeldoc_outputs)
                babeldoc_success = True
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}"
                msg = f"BabelDOC failed: {err_msg}"
                self._report_progress("translating", 25, 100, msg)
                # Continue to fallback pipeline

        # Determine pipeline: vision OCR for scanned PDFs when mode is "vision"
        if doc.pdf_type == "scanned" and self.config.scanned.mode == "vision":
            vision_outputs = await self._run_vision_pipeline(input_path, doc)
            output_files.update(vision_outputs)
        else:
            # Legacy pipeline (PaddleOCR or digital fallback)
            need_markdown = "markdown" in self.config.output.formats
            need_pdf_fallback = doc.pdf_type == "scanned" and "pdf" in self.config.output.formats
            need_pdf_fallback = need_pdf_fallback or (doc.pdf_type == "digital" and "pdf" in self.config.output.formats and not babeldoc_success)

            if need_markdown or need_pdf_fallback:
                skip_pdf = doc.pdf_type == "digital" and babeldoc_success
                legacy_outputs = await self._run_legacy_pipeline(input_path, doc, skip_pdf=skip_pdf)
                output_files.update(legacy_outputs)

        if not output_files:
            self._report_error("No output files were generated")
            return {}

        self._report_progress("generating", 100, 100, "Done!")
        self._report_finish(output_files)

        return output_files
