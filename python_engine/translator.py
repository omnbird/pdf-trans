"""Translation engine using OpenAI-compatible APIs (supports DeepSeek, etc.).

Features:
- Context-aware chunk translation: several paragraphs per request so the model
  sees neighbouring text (and prompt overhead is amortised)
- Document-level context + user glossary injected into every prompt
- Async batch translation with rate limiting
- Retry with exponential backoff
- Graceful fallback to per-segment translation when the model breaks the
  JSON contract
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import openai
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from python_engine.config import TranslationConfig
from python_engine import diagnostics
from python_engine.rate_limiter import RateLimit, TokenBucketRateLimiter

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a professional translator."

CHUNK_PROMPT = """Translate the following segments into Chinese.

Rules:
- `input` is the text to translate; `id` identifies the segment.
- Return ONLY a JSON array of objects: [{{"id": 0, "output": "<translation>"}}]
- Keep every id, keep the same order, never merge or split segments.
- No explanations, no markdown fences, no notes.

Context: {context}

{preceding}Glossary (use these translations exactly):
{glossary}

Segments:
{segments}"""


@dataclass
class TranslationResult:
    """Result of a single translation."""

    original: str
    translated: str
    tokens_used: int = 0


class Translator:
    """Async translator with rate limiting, batching and retry."""

    def __init__(self, config: TranslationConfig):
        self.config = config
        # Normalize base_url: strip trailing /chat/completions if present
        base_url = config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=config.api_key,
        )
        self.model = config.model
        # Provider-specific extra fields (e.g. disable thinking/reasoning)
        self.extra_body = dict(config.extra_body or {})
        # Book-level digest (opening text), used when the caller sets it.
        self._digest = ""

        # Setup rate limiter — each mode uses only its own parameter
        if config.rate_limit.mode == "rpm":
            # RPM alone governs the rate; concurrency is effectively unlimited
            rate_limit = RateLimit.from_rpm(config.rate_limit.rpm, max_workers=9999)
        elif config.rate_limit.mode == "concurrent":
            # Concurrent count alone governs; no QPS limit
            rate_limit = RateLimit.from_concurrent(config.rate_limit.concurrent)
        else:
            # Custom: both QPS and max_workers apply independently
            rate_limit = RateLimit(
                qps=config.rate_limit.qps,
                max_workers=config.rate_limit.max_workers,
            )
        self.rate_limiter = TokenBucketRateLimiter(rate_limit)

    # ------------------------------------------------------------- prompts --

    def _system_prompt(self, digest: str = "") -> str:
        """System message: document-level context and/or a book digest."""
        context = (self.config.document_context or "").strip()
        parts = [DEFAULT_SYSTEM_PROMPT]
        if context:
            parts.append(f"About this document:\n{context}")
        digest = (digest or self._digest or "").strip()
        if digest:
            parts.append(
                "Opening of the document (reference only, do not translate; "
                f"use it to keep names and terminology consistent):\n{digest}"
            )
        return "\n\n".join(parts)

    def _glossary_text(self, glossary: dict[str, str] | None) -> str:
        merged = {**(self.config.glossary or {}), **(glossary or {})}
        if not merged:
            return "None"
        return "\n".join(f"{source} = {target}" for source, target in merged.items())

    def _build_prompt(
        self,
        text: str,
        context: str = "",
        glossary: dict[str, str] | None = None,
    ) -> str:
        """Build the translation prompt with optional context and glossary."""
        return self.config.prompt_template.format(
            context=context or "None",
            glossary=self._glossary_text(glossary),
            text=text,
        )

    # ---------------------------------------------------------- translation --

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
                openai.InternalServerError,
            )
        ),
        reraise=True,
    )
    async def _translate_single(
        self,
        text: str,
        context: str = "",
        glossary: dict[str, str] | None = None,
    ) -> TranslationResult:
        """Translate a single text segment with retry."""
        prompt = self._build_prompt(text, context, glossary)

        async with self.rate_limiter:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_body=self.extra_body or None,
            )

        translated = (response.choices[0].message.content or "").strip()
        tokens_used = response.usage.total_tokens if response.usage else 0
        diagnostics.tracker.record(
            model=self.model,
            extra_body=self.extra_body,
            usage=response.usage,
            stage="translate",
        )

        return TranslationResult(
            original=text,
            translated=translated,
            tokens_used=tokens_used,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
                openai.InternalServerError,
            )
        ),
        reraise=True,
    )
    async def _translate_chunk(
        self,
        segments: list[tuple[int, str]],
        context: str = "",
        glossary: dict[str, str] | None = None,
        preceding: str = "",
    ) -> dict[int, str]:
        """Translate several segments in one request (context-aware).

        Returns a mapping id -> translation. Raises ValueError when the model
        does not honour the JSON contract or the answer was truncated, so the
        caller can split the chunk and retry.
        """
        payload = [{"id": idx, "input": text} for idx, text in segments]
        preceding_block = ""
        if preceding.strip():
            preceding_block = (
                "Preceding text (context only — do NOT translate or output it):\n"
                f"{preceding.strip()}\n\n"
            )
        prompt = CHUNK_PROMPT.format(
            context=context or "None",
            preceding=preceding_block,
            glossary=self._glossary_text(glossary),
            segments=json.dumps(payload, ensure_ascii=False, indent=1),
        )

        async with self.rate_limiter:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_body=self.extra_body or None,
            )

        diagnostics.tracker.record(
            model=self.model,
            extra_body=self.extra_body,
            usage=response.usage,
            stage="translate-batch",
        )

        raw = (response.choices[0].message.content or "").strip()
        if response.choices[0].finish_reason == "length":
            raise ValueError(f"response truncated at max_tokens={self.config.max_tokens}")
        return self._parse_chunk_response(raw, segments)

    @staticmethod
    def _parse_chunk_response(raw: str, segments: list[tuple[int, str]]) -> dict[int, str]:
        """Parse the model's JSON answer and validate it against the request."""
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # Tolerate a short preamble before the JSON payload
        start = min(
            [i for i in (text.find("["), text.find("{")) if i >= 0],
            default=-1,
        )
        if start > 0:
            text = text[start:]

        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("translations", "segments", "data", "output"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]

        expected = {idx for idx, _ in segments}
        result: dict[int, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                item_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            value = item.get("output", item.get("translation", item.get("text")))
            if isinstance(value, str) and value.strip() and item_id in expected:
                result[item_id] = value.strip()

        if len(result) != len(expected):
            missing = sorted(expected - set(result))
            raise ValueError(f"chunk response missing ids: {missing}")
        return result

    def _chunk_segments(self, texts: list[str]) -> list[list[tuple[int, str]]]:
        """Group segments so each request carries enough context.

        The character budget is auto-derived from `max_tokens` when the user
        leaves `batch_char_limit` at 0. Measured on a real run (doubao-seed-2.1,
        20 pages): completion tokens ≈ 0.25 × source characters. Budgeting 2×
        the cap keeps the response at roughly half of max_tokens, leaving room
        for JSON scaffolding and for the model being more verbose than average.
        """
        size = max(1, int(self.config.batch_size or 1))
        char_limit = int(self.config.batch_char_limit or 0)
        if char_limit <= 0:
            char_limit = max(1000, int((self.config.max_tokens or 4096) * 2))

        chunks: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        current_chars = 0

        for idx, text in enumerate(texts):
            length = len(text)
            too_many = len(current) >= size
            too_long = char_limit and current and current_chars + length > char_limit
            if current and (too_many or too_long):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append((idx, text))
            current_chars += length

        if current:
            chunks.append(current)
        return chunks

    async def translate_batch(
        self,
        texts: list[str],
        context: str = "",
        glossary: dict[str, str] | None = None,
        on_progress: callable | None = None,
        neighbors: bool = True,
        digest: str = "",
    ) -> list[TranslationResult]:
        """Translate multiple texts with context-aware batching.

        Args:
            texts: List of text segments to translate
            context: Shared context (e.g., chapter title)
            glossary: Terminology dictionary (merged with config.glossary)
            on_progress: Callback(current, total) for progress updates
            neighbors: Group segments into chunks so the model sees adjacent
                text. Set False to keep one request per segment.
            digest: Opening text of the document (book-level context) added to
                the system prompt of every request.

        Returns:
            List of TranslationResult in the same order as input
        """
        total = len(texts)
        if total == 0:
            return []

        if digest:
            self._digest = digest

        if not neighbors or max(1, int(self.config.batch_size or 1)) == 1:
            chunks = [[(i, t)] for i, t in enumerate(texts)]
        else:
            chunks = self._chunk_segments(texts)

        # Preceding-context window per chunk: the tail of the previous chunk's
        # source text. Computed from the source (not from translations) so
        # chunks can still run concurrently.
        window = max(0, int(self.config.context_window_chars or 0))
        preceding: list[str] = [""] * len(chunks)
        if window:
            for i in range(1, len(chunks)):
                previous_text = "\n".join(t for _, t in chunks[i - 1])
                preceding[i] = previous_text[-window:] if len(previous_text) > window else previous_text

        results: list[TranslationResult | None] = [None] * total
        completed = 0
        lock = asyncio.Lock()

        async def _translate_chunk_tree(
            chunk: list[tuple[int, str]], pre: str, depth: int = 0
        ) -> None:
            """Translate a chunk, splitting it in half when the model errs."""
            nonlocal completed
            translations: dict[int, str] = {}
            if len(chunk) > 1:
                try:
                    translations = await self._translate_chunk(
                        chunk, context, glossary, preceding=pre
                    )
                except Exception as exc:  # noqa: BLE001 - split, never lose text
                    half = len(chunk) // 2
                    first_half = chunk[:half]
                    tail = "\n".join(t for _, t in first_half)
                    if window and len(tail) > window:
                        tail = tail[-window:]
                    logger.warning(
                        "[Translator] chunk of %d segments failed (%s: %s); "
                        "splitting into %d + %d (depth %d)",
                        len(chunk), type(exc).__name__, exc,
                        half, len(chunk) - half, depth,
                    )
                    await _translate_chunk_tree(first_half, pre, depth + 1)
                    await _translate_chunk_tree(chunk[half:], tail if window else "", depth + 1)
                    return

            for idx, text in chunk:
                if idx in translations:
                    results[idx] = TranslationResult(
                        original=text,
                        translated=translations[idx],
                        tokens_used=0,
                    )
                else:
                    results[idx] = await self._translate_single(text, context, glossary)
                async with lock:
                    completed += 1
                    if on_progress:
                        on_progress(completed, total)

        # Chunks run concurrently; the rate limiter controls actual throughput.
        await asyncio.gather(*[
            _translate_chunk_tree(chunk, preceding[i]) for i, chunk in enumerate(chunks)
        ])

        return [r for r in results if r is not None]
