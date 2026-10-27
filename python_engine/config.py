"""Configuration management for PDF translation engine.

All settings are defined as Pydantic models for validation.
Supports loading from TOML files and Python dicts (for JSON exchange with Tauri).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import toml
from pydantic import BaseModel, Field


DEFAULT_PROMPT_TEMPLATE = """You are a professional translator specializing in astrology and Vedic texts.
Translate the following English text into Chinese accurately and fluently.
Maintain the original paragraph structure.

Context: {context}

Glossary:
{glossary}

Text to translate:
{text}

Chinese translation:"""


class RateLimitConfig(BaseModel):
    """API rate limiting configuration."""

    mode: Literal["rpm", "concurrent", "custom"] = "rpm"
    rpm: int = 240  # requests per minute
    concurrent: int = 5  # max concurrent requests
    qps: float = 4.0  # queries per second (for custom mode)
    max_workers: int = 5  # thread pool size


class TranslationConfig(BaseModel):
    """Translation engine configuration."""

    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    temperature: float = 0.3
    max_tokens: int = 4096
    # Extra JSON fields merged into the chat.completions request body.
    # Providers disagree on how to disable reasoning/thinking, e.g.:
    #   Gemini (OpenAI-compat): {"google": {"thinking_config": {"thinking_budget": 0}}}
    #   Qwen/DashScope:         {"enable_thinking": false}
    #   GLM/Zhipu & DeepSeek:   {"thinking": {"type": "disabled"}}
    #   OpenAI o-series:        {"reasoning_effort": "minimal"}
    extra_body: dict[str, Any] = Field(default_factory=dict)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class VisionConfig(BaseModel):
    """Vision API configuration for scanned PDFs (multi-modal OCR)."""

    enabled: bool = False
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    api_key: str = ""
    model: str = "gemini-2.5-flash-preview-04-17"
    max_tokens: int = 4096
    # Same purpose as TranslationConfig.extra_body (disable thinking, etc.).
    extra_body: dict[str, Any] = Field(default_factory=dict)


class OutputConfig(BaseModel):
    """Output format configuration."""

    formats: list[Literal["pdf", "markdown"]] = ["pdf"]
    mode: Literal["mono", "dual", "alternating"] = "dual"
    dual_lang_order: Literal["translation_first", "original_first"] = "translation_first"
    # For scanned PDFs: embed original image alongside translation
    keep_original_image: bool = True
    # Margin (cm) on the translation side of dual PDFs. Larger margin = smaller
    # content area, which lets e-readers (e.g. 6-inch) auto-crop and zoom in
    # further on the actual text.
    translation_margin_cm: float = 3.5
    # Default folder where translated files are written. Supports `~` (home).
    default_output_dir: str = "~/Downloads"


class ScannedConfig(BaseModel):
    """Scanned PDF processing configuration.

    The user explicitly sets `is_scanned` for the PDF they are translating.
    When False, the engine treats the PDF as digital and skips the OCR
    pipeline entirely. When True, it uses the configured `mode` (PaddleOCR,
    Vision API, or Mistral OCR) to extract text.
    """

    is_scanned: bool = False  # user explicitly marks the input PDF as scanned
    mode: Literal["ocr", "vision", "mistral_ocr"] = "ocr"
    dpi: int = 150
    ocr_language: str = "en"
    mistral_api_key: str = ""  # API key for Mistral OCR mode


class EngineConfig(BaseModel):
    """Top-level engine configuration."""

    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    scanned: ScannedConfig = Field(default_factory=ScannedConfig)
    temp_dir: str = "./python_engine/temp"
    # Runtime output directory. If left empty, the engine falls back to
    # `output.default_output_dir` (which is the user-facing UI setting).
    output_dir: str = ""
    pages: str = ""  # e.g. "1-10" or "1,3,5" or "1-5,8,10-12"
    debug: bool = False

    @classmethod
    def from_toml(cls, path: str | Path) -> EngineConfig:
        """Load configuration from a TOML file."""
        path = Path(path)
        if not path.exists():
            return cls()
        data = toml.load(path)
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> EngineConfig:
        """Load configuration from a dict (for JSON exchange)."""
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        """Export configuration as a plain dict."""
        return self.model_dump()

    def save_toml(self, path: str | Path) -> None:
        """Save configuration to a TOML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            toml.dump(self.to_dict(), f)


def get_default_config_path() -> Path:
    """Return the default config file path."""
    config_dir = Path.home() / ".config" / "pdf-trans"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.toml"


def load_config(path: str | Path | None = None) -> EngineConfig:
    """Load configuration from file or return defaults."""
    if path is None:
        path = get_default_config_path()
    return EngineConfig.from_toml(path)
