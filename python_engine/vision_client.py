"""Multi-modal vision API client for OCR of scanned PDF pages.

Uses OpenAI-compatible vision APIs (Gemini, GPT-4o, etc.) to convert
scanned page images into structured Markdown with preserved layout
(headings, paragraphs, tables, lists).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Callable

import openai
from PIL import Image

from python_engine.config import VisionConfig

logger = logging.getLogger(__name__)

OCR_PROMPT = """You are a precise OCR engine for academic books on astrology and Vedic texts.
Convert this scanned book page into clean, well-structured Markdown.

Rules:
1. Use Markdown headings (# ## ###) for section titles
2. Use paragraphs for body text
3. Use Markdown tables for tabular data (| column | column |)
4. For diagrams, charts, star wheels, or images that contain no translatable text, insert: [DIAGRAM: brief description]
5. For Sanskrit in Devanagari script, provide IAST transliteration in parentheses
6. Preserve ordered/unordered lists
7. Output ONLY the Markdown content, no explanations
8. Do NOT translate — keep original English/Sanskrit text"""


class VisionClient:
    """OpenAI-compatible vision API client for page-level OCR."""

    def __init__(self, config: VisionConfig):
        base_url = config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        self.client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=config.api_key,
            timeout=120.0,  # 2 min per request — don't hang forever
        )
        self.model = config.model
        self.max_tokens = 4096

    @staticmethod
    def _image_to_base64(image: Image.Image) -> str:
        """Convert PIL Image to base64 PNG string."""
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    async def ocr_page(
        self,
        image: Image.Image,
        page_num: int = 0,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """OCR a single page image to Markdown.

        Args:
            image: PIL Image (RGB)
            page_num: Page number for logging
            on_progress: Optional progress callback

        Returns:
            Markdown string representing the page content
        """
        if on_progress:
            on_progress(f"Sending page {page_num + 1} to vision model...")

        img_b64 = self._image_to_base64(image)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            }],
            temperature=0.1,
            max_tokens=self.max_tokens,
        )

        content = response.choices[0].message.content or ""
        # Strip markdown code fences if the model wrapped output in ```markdown
        content = content.strip()
        if content.startswith("```markdown"):
            content = content[len("```markdown"):].strip()
        if content.startswith("```"):
            content = content[3:].strip()
        if content.endswith("```"):
            content = content[:-3].strip()

        return content

    async def ocr_pages(
        self,
        pages: list[tuple[int, Image.Image]],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[tuple[int, str]]:
        """OCR multiple pages concurrently.

        Args:
            pages: List of (page_num, image) tuples
            on_progress: Optional progress callback(message)

        Returns:
            List of (page_num, markdown) results in input order
        """
        import asyncio

        async def _ocr_one(page_num: int, image: Image.Image) -> tuple[int, str]:
            md = await self.ocr_page(image, page_num, on_progress)
            return page_num, md

        results = await asyncio.gather(*[
            _ocr_one(num, img) for num, img in pages
        ])
        return list(results)
