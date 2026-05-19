"""OCR engine wrapper using PaddleOCR 3.x.

Provides text extraction from scanned PDF page images.
Uses lightweight mobile models and disables unnecessary features
(doc orientation, unwarping, textline orientation) for faster loading.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np
from PIL import Image

from python_engine.pdf_processor import TextBlock

logger = logging.getLogger(__name__)


class PaddleOCREngine:
    """Lazy-initialized PaddleOCR wrapper with lightweight models."""

    def __init__(self, language: str = "en", on_loading: Callable[[str], None] | None = None):
        self.language = language
        self._ocr = None
        self._on_loading = on_loading

    def _get_ocr(self):
        """Lazy init to avoid slow import when not needed."""
        if self._ocr is None:
            from paddleocr import PaddleOCR

            if self._on_loading:
                self._on_loading("Loading OCR models (first time, may take 5-10s)...")

            logger.info("Initializing PaddleOCR with lightweight models...")
            self._ocr = PaddleOCR(
                # Use mobile models instead of server models for much faster load
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                # Disable features we don't need for clean scanned book pages
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            logger.info("PaddleOCR ready")

            if self._on_loading:
                self._on_loading("OCR models loaded")

        return self._ocr

    def predict(self, image: Image.Image) -> list[TextBlock]:
        """Run OCR on a single PIL Image.

        Args:
            image: PIL Image (RGB)

        Returns:
            List of TextBlock with coordinates and text
        """
        results = self.predict_batch([image])
        return results[0] if results else []

    def predict_batch(
        self, images: list[Image.Image]
    ) -> list[list[TextBlock]]:
        """Run OCR on multiple images in one batch call.

        Much faster than calling predict() per-page because PaddleOCR
        can reuse the loaded model and batch the forward passes.

        Args:
            images: List of PIL Images (RGB)

        Returns:
            List (one per image) of TextBlock lists
        """
        if not images:
            return []

        ocr = self._get_ocr()
        # Convert PIL images to numpy arrays
        img_arrays = [np.array(img) for img in images]

        # PaddleOCR 3.x predict accepts a list of images and returns
        # a list of result dicts (one per image)
        all_results = ocr.predict(img_arrays)
        if not all_results:
            return [[] for _ in images]

        # Ensure list wrapping when only one image is passed
        if not isinstance(all_results, list):
            all_results = [all_results]

        page_blocks: list[list[TextBlock]] = []
        for result in all_results:
            rec_texts = result.get("rec_texts", [])
            rec_scores = result.get("rec_scores", [])
            rec_boxes = result.get("rec_boxes", [])

            blocks = []
            for i, text in enumerate(rec_texts):
                if not text or not text.strip():
                    continue

                score = rec_scores[i] if i < len(rec_scores) else 0.0
                if score < 0.5:
                    continue

                box = rec_boxes[i] if i < len(rec_boxes) else None
                if box is not None and len(box) >= 4:
                    if hasattr(box, "tolist"):
                        box = box.tolist()
                    x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
                else:
                    x1 = y1 = x2 = y2 = 0.0

                blocks.append(TextBlock(
                    text=text.strip(),
                    x=float(x1),
                    y=float(y1),
                    width=float(x2 - x1),
                    height=float(y2 - y1),
                    font_size=12.0,
                    is_bold=False,
                    page_num=0,  # Set by caller
                ))

            # Sort by reading order: top-to-bottom, then left-to-right
            blocks.sort(key=lambda b: (b.y, b.x))
            page_blocks.append(blocks)

        return page_blocks
