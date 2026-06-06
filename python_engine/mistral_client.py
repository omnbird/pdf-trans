"""Mistral OCR client for processing full PDFs to page-level Markdown.

Uses the Mistral OCR API (file upload + signed URL + OCR endpoint)
to convert scanned PDF pages into structured Markdown with embedded
image references.

The flow is:
  1. Upload the PDF file to Mistral servers
  2. Get a signed URL (1-hour expiry)
  3. Call the OCR endpoint on the signed URL
  4. Delete the uploaded file
  5. Return per-page Markdown
"""

from __future__ import annotations

import logging
import re
import tempfile
import traceback
from pathlib import Path
from typing import Callable

import fitz
from mistralai.client.models.documenturlchunk import DocumentURLChunk
from mistralai.client.models.file import File as MistralFile
from mistralai.client.sdk import Mistral

from python_engine.pdf_processor import parse_page_ranges

logger = logging.getLogger(__name__)

# Regex to match ![alt](url) image references
_IMG_REF_RE = re.compile(r"!\[.*?\]\(.*?\)")


def _clean_mistral_markdown(md: str) -> str:
    """Strip embedded image references from Mistral OCR output.

    We render the original scanned page image ourselves for the dual PDF,
    so Mistral's embedded base64 images are not needed.
    """
    md = _IMG_REF_RE.sub("", md)
    # Collapse excessive blank lines left by removal
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def slice_pdf(input_path: Path, page_indices: list[int]) -> Path:
    """Extract a subset of pages into a new temporary PDF.

    Uses PyMuPDF's ``select()`` when possible to avoid duplicating
    shared resources (images, fonts) that blow up file size.

    Args:
        input_path: Original PDF path
        page_indices: 0-based page indices to include

    Returns:
        Path to the temporary sliced PDF
    """
    doc = fitz.open(input_path)
    out = fitz.open()

    # Check if page_indices is a single contiguous range.
    # If so, insert the whole range in one go — this preserves shared
    # resources (images, fonts) and keeps the file size close to the
    # original. Page-by-page insert duplicates shared images, causing
    # file size to explode (e.g. 17 MB → 850 MB).
    is_contiguous = len(page_indices) > 0 and all(
        page_indices[i] + 1 == page_indices[i + 1]
        for i in range(len(page_indices) - 1)
    )

    if is_contiguous:
        out.insert_pdf(doc, from_page=page_indices[0], to_page=page_indices[-1])
    else:
        # Non-contiguous: fall back to page-by-page (rare case)
        for i in page_indices:
            out.insert_pdf(doc, from_page=i, to_page=i)

    tmp = Path(tempfile.mktemp(suffix=".pdf"))
    # garbage=4 removes duplicate objects; deflate compresses streams
    out.save(tmp, garbage=4, deflate=True)
    out.close()
    doc.close()
    return tmp


# Mistral OCR API limit: max 1,000 pages per upload
MISTRAL_MAX_PAGES = 1000


class MistralOCRClient:
    """Async client for Mistral OCR (SDK v2.4.5)."""

    # Upload timeout: large PDFs can take several minutes over slow networks
    UPLOAD_TIMEOUT_MS = 600_000  # 10 minutes

    def __init__(self, api_key: str):
        self.client = Mistral(api_key=api_key, timeout_ms=self.UPLOAD_TIMEOUT_MS)
        logger.info("[Mistral] Client created with timeout=%d ms", self.UPLOAD_TIMEOUT_MS)

    async def _upload_and_ocr(
        self,
        pdf_path: Path,
        page_offset: int,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[int, str]]:
        """Upload a single PDF and OCR it, returning page markdown.

        Args:
            pdf_path: Path to the PDF to upload
            page_offset: Offset to add to each page index (for chunked processing)
            on_progress: Optional progress callback

        Returns:
            List of (adjusted_page_index, cleaned_markdown) tuples
        """
        if on_progress:
            on_progress("Uploading PDF to Mistral...")

        file_size = pdf_path.stat().st_size
        logger.info("[Mistral] Uploading PDF: %s (%.2f MB, offset=%d)", pdf_path.name, file_size / (1024 * 1024), page_offset)

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        try:
            uploaded_file = await self.client.files.upload_async(
                file=MistralFile(file_name=pdf_path.name, content=pdf_bytes),
                purpose="ocr",
            )
            logger.info("[Mistral] Upload success: file_id=%s", uploaded_file.id)
        except Exception as e:
            logger.error("[Mistral] Upload failed: %s\n%s", e, traceback.format_exc())
            raise

        if on_progress:
            on_progress("Getting signed URL...")

        try:
            signed_url = await self.client.files.get_signed_url_async(
                file_id=uploaded_file.id,
                expiry=1,  # 1 hour
            )
            logger.info("[Mistral] Signed URL obtained: expiry=1h")
        except Exception as e:
            logger.error("[Mistral] Get signed URL failed for file_id=%s: %s\n%s", uploaded_file.id, e, traceback.format_exc())
            # Attempt cleanup even on failure
            try:
                await self.client.files.delete_async(file_id=uploaded_file.id)
                logger.info("[Mistral] Cleaned up failed upload: file_id=%s", uploaded_file.id)
            except Exception as cleanup_err:
                logger.warning("[Mistral] Failed to clean up file %s: %s", uploaded_file.id, cleanup_err)
            raise

        if on_progress:
            on_progress("Mistral OCR processing pages...")

        try:
            ocr_response = await self.client.ocr.process_async(
                document=DocumentURLChunk(document_url=signed_url.url),
                model="mistral-ocr-latest",
                include_image_base64=True,
            )
            logger.info("[Mistral] OCR complete: %d pages returned", len(ocr_response.pages) if hasattr(ocr_response, "pages") else 0)
        except Exception as e:
            logger.error("[Mistral] OCR process failed for file_id=%s: %s\n%s", uploaded_file.id, e, traceback.format_exc())
            # Attempt cleanup even on failure
            try:
                await self.client.files.delete_async(file_id=uploaded_file.id)
                logger.info("[Mistral] Cleaned up after OCR failure: file_id=%s", uploaded_file.id)
            except Exception as cleanup_err:
                logger.warning("[Mistral] Failed to clean up file %s: %s", uploaded_file.id, cleanup_err)
            raise

        # Clean up uploaded file from Mistral servers
        try:
            await self.client.files.delete_async(file_id=uploaded_file.id)
            logger.info("[Mistral] Deleted uploaded file: file_id=%s", uploaded_file.id)
        except Exception:
            logger.warning("[Mistral] Failed to delete uploaded file %s from Mistral", uploaded_file.id)

        pages: list[tuple[int, str]] = []
        for page in ocr_response.pages:
            markdown = _clean_mistral_markdown(page.markdown)
            pages.append((page.index + page_offset, markdown))

        logger.info("[Mistral] Returning %d page results", len(pages))
        return pages

    async def process_pages(
        self,
        input_path: Path,
        page_indices: list[int],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[int, str]]:
        """OCR selected pages from a PDF, splitting into chunks if needed.

        If the number of pages exceeds MISTRAL_MAX_PAGES (1000), the PDF is
        automatically split into multiple uploads to stay within the API limit.

        Args:
            input_path: Path to the original PDF
            page_indices: 0-based page indices to process
            on_progress: Optional progress callback

        Returns:
            List of (original_page_index, cleaned_markdown) tuples,
            in the same order as page_indices.
        """
        temp_files: list[Path] = []
        logger.info("[Mistral] process_pages called: %s, %d pages", input_path.name, len(page_indices))

        # Get total page count for the full-document optimization
        doc_total_pages = len(fitz.open(input_path))

        try:
            # Split page indices into chunks within the API limit
            all_results: list[tuple[int, str]] = []
            chunks = [
                page_indices[i:i + MISTRAL_MAX_PAGES]
                for i in range(0, len(page_indices), MISTRAL_MAX_PAGES)
            ]
            logger.info("[Mistral] Split into %d chunk(s) (max %d pages each)", len(chunks), MISTRAL_MAX_PAGES)

            for chunk_idx, chunk in enumerate(chunks):
                if len(chunks) > 1 and on_progress:
                    on_progress(f"Processing chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk)} pages)...")

                logger.info("[Mistral] Processing chunk %d/%d: pages %s", chunk_idx + 1, len(chunks), chunk[:5] if len(chunk) <= 5 else f"{chunk[0]}..{chunk[-1]}")

                # Optimization: if this chunk covers ALL pages in the original PDF
                # (i.e. one chunk, contiguous from 0 to total-1), skip slicing
                # and upload the original file directly.
                is_full_doc = (
                    len(chunks) == 1
                    and chunk[0] == 0
                    and len(chunk) == doc_total_pages
                )

                if is_full_doc:
                    logger.info("[Mistral] Using original file directly (no slicing needed)")
                    sliced = input_path
                else:
                    # Slice this chunk into a temp PDF
                    sliced = slice_pdf(input_path, chunk)
                    temp_files.append(sliced)
                    logger.info("[Mistral] Sliced chunk to: %s (%.2f MB)", sliced.name, sliced.stat().st_size / (1024 * 1024))

                # Upload and OCR
                try:
                    chunk_results = await self._upload_and_ocr(
                        sliced,
                        page_offset=chunk[0],
                        on_progress=on_progress,
                    )
                    all_results.extend(chunk_results)
                    logger.info("[Mistral] Chunk %d/%d done: %d pages returned", chunk_idx + 1, len(chunks), len(chunk_results))
                except Exception:
                    logger.error("[Mistral] Chunk %d/%d FAILED", chunk_idx + 1, len(chunks))
                    raise

            # Sort by original page index and deduplicate (each page processed once)
            all_results.sort(key=lambda x: x[0])
            logger.info("[Mistral] All chunks complete: %d total results", len(all_results))
            return all_results[:len(page_indices)]

        except Exception:
            logger.error("[Mistral] process_pages failed:\n%s", traceback.format_exc())
            raise

        finally:
            # Clean up all temp sliced files
            logger.info("[Mistral] Cleaning up %d temp files", len(temp_files))
            for tmp in temp_files:
                try:
                    tmp.unlink(missing_ok=True)
                    logger.debug("[Mistral] Deleted temp: %s", tmp)
                except Exception as e:
                    logger.warning("[Mistral] Failed to delete temp %s: %s", tmp, e)
