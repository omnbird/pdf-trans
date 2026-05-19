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

    Args:
        input_path: Original PDF path
        page_indices: 0-based page indices to include

    Returns:
        Path to the temporary sliced PDF
    """
    doc = fitz.open(input_path)
    out = fitz.open()
    for i in page_indices:
        out.insert_pdf(doc, from_page=i, to_page=i)

    tmp = Path(tempfile.mktemp(suffix=".pdf"))
    out.save(tmp)
    out.close()
    doc.close()
    return tmp


# Mistral OCR API limit: max 1,000 pages per upload
MISTRAL_MAX_PAGES = 1000


class MistralOCRClient:
    """Async client for Mistral OCR (SDK v2.4.5)."""

    def __init__(self, api_key: str):
        self.client = Mistral(api_key=api_key)

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

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        uploaded_file = await self.client.files.upload_async(
            file=MistralFile(file_name=pdf_path.name, content=pdf_bytes),
            purpose="ocr",
        )

        if on_progress:
            on_progress("Getting signed URL...")

        signed_url = await self.client.files.get_signed_url_async(
            file_id=uploaded_file.id,
            expiry=1,  # 1 hour
        )

        if on_progress:
            on_progress("Mistral OCR processing pages...")

        ocr_response = await self.client.ocr.process_async(
            document=DocumentURLChunk(document_url=signed_url.url),
            model="mistral-ocr-latest",
            include_image_base64=True,
        )

        # Clean up uploaded file from Mistral servers
        try:
            await self.client.files.delete_async(file_id=uploaded_file.id)
        except Exception:
            logger.warning("Failed to delete uploaded file %s from Mistral", uploaded_file.id)

        pages: list[tuple[int, str]] = []
        for page in ocr_response.pages:
            markdown = _clean_mistral_markdown(page.markdown)
            pages.append((page.index + page_offset, markdown))

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

        try:
            # Split page indices into chunks within the API limit
            all_results: list[tuple[int, str]] = []
            chunks = [
                page_indices[i:i + MISTRAL_MAX_PAGES]
                for i in range(0, len(page_indices), MISTRAL_MAX_PAGES)
            ]

            for chunk_idx, chunk in enumerate(chunks):
                if len(chunks) > 1 and on_progress:
                    on_progress(f"Processing chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk)} pages)...")

                # Slice this chunk into a temp PDF
                sliced = slice_pdf(input_path, chunk)
                temp_files.append(sliced)

                # Upload and OCR
                chunk_results = await self._upload_and_ocr(
                    sliced,
                    page_offset=chunk[0],
                    on_progress=on_progress,
                )
                all_results.extend(chunk_results)

            # Sort by original page index and deduplicate (each page processed once)
            all_results.sort(key=lambda x: x[0])
            return all_results[:len(page_indices)]

        finally:
            # Clean up all temp sliced files
            for tmp in temp_files:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
