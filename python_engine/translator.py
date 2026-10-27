"""Translation engine using OpenAI-compatible APIs (supports DeepSeek, etc.).

Features:
- Async batch translation with rate limiting
- Retry with exponential backoff
- Glossary injection
- Context-aware translation
"""

from __future__ import annotations

import asyncio
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
from python_engine.rate_limiter import RateLimit, TokenBucketRateLimiter


@dataclass
class TranslationResult:
    """Result of a single translation."""

    original: str
    translated: str
    tokens_used: int = 0


class Translator:
    """Async translator with rate limiting and retry."""

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

    def _build_prompt(
        self,
        text: str,
        context: str = "",
        glossary: dict[str, str] | None = None,
    ) -> str:
        """Build the translation prompt with optional context and glossary."""
        glossary_text = ""
        if glossary:
            glossary_text = "\n".join(
                f"{en} = {zh}" for en, zh in glossary.items()
            )

        return self.config.prompt_template.format(
            context=context or "None",
            glossary=glossary_text or "None",
            text=text,
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
                    {"role": "system", "content": "You are a professional translator."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                extra_body=self.extra_body or None,
            )

        translated = response.choices[0].message.content.strip()
        tokens_used = response.usage.total_tokens if response.usage else 0

        return TranslationResult(
            original=text,
            translated=translated,
            tokens_used=tokens_used,
        )

    async def translate_batch(
        self,
        texts: list[str],
        context: str = "",
        glossary: dict[str, str] | None = None,
        on_progress: callable | None = None,
    ) -> list[TranslationResult]:
        """Translate multiple texts concurrently with rate limiting.

        Args:
            texts: List of text segments to translate
            context: Shared context (e.g., chapter title)
            glossary: Terminology dictionary
            on_progress: Callback(current, total) for progress updates

        Returns:
            List of TranslationResult in the same order as input
        """
        total = len(texts)
        completed = 0
        results: list[TranslationResult | None] = [None] * total

        async def _translate_one(index: int, text: str) -> None:
            nonlocal completed
            result = await self._translate_single(text, context, glossary)
            results[index] = result
            completed += 1
            if on_progress:
                on_progress(completed, total)

        # Run all translations concurrently (rate limiter controls actual throughput)
        await asyncio.gather(*[
            _translate_one(i, text) for i, text in enumerate(texts)
        ])

        return [r for r in results if r is not None]
