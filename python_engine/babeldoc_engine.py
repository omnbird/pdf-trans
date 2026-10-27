"""BabelDOC integration for layout-preserving PDF translation.

BabelDOC replaces text in-place within the PDF structure, preserving:
- Original images, charts, and diagrams
- Text positioning and font styling
- Page layout and formatting
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from babeldoc.docvision.doclayout import DocLayoutModel
from babeldoc.format.pdf.high_level import async_translate
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode
from babeldoc.translator.translator import OpenAITranslator
from babeldoc.translator.translator import set_translate_rate_limiter

from python_engine.config import EngineConfig


class CustomOpenAITranslator(OpenAITranslator):
    """OpenAITranslator with domain-specific system prompt and concurrency tracking."""

    def __init__(self, config: EngineConfig, **kwargs):
        super().__init__(**kwargs)
        self._engine_config = config
        # Merge user-configured extra body fields (e.g. disable thinking)
        # into whatever BabelDOC set up. BabelDOC's do_translate() sends
        # self.extra_body with every chat.completions request.
        extra_body = dict(config.translation.extra_body or {})
        if extra_body:
            self.extra_body.update(extra_body)

    def prompt(self, text):
        """Override prompt to inject astrology/vedic domain context."""
        domain_hint = (
            "You are a professional translator specializing in astrology and Vedic texts. "
            "Translate accurately and fluently into Chinese. Maintain original formatting."
        )
        return [
            {"role": "system", "content": domain_hint},
            {
                "role": "user",
                "content": (
                    f";; Treat next line as plain text input and translate it into {self.lang_out}, "
                    f"output translation ONLY. If translation is unnecessary (e.g. proper nouns, codes, "
                    f"{{{{1}}}}, etc. ), return the original text. NO explanations. NO notes. Input:\n\n{text}"
                ),
            },
        ]


def _map_output_mode(config: EngineConfig) -> tuple[bool, bool, bool, bool]:
    """Map our output mode to BabelDOC's config flags.

    Returns:
        (no_dual, no_mono, use_alternating, dual_translate_first)
    """
    mode = config.output.mode

    if mode == "mono":
        no_dual = True
        no_mono = False
        use_alternating = False
    elif mode == "dual":
        no_dual = False
        no_mono = False
        use_alternating = False
    elif mode == "alternating":
        no_dual = False
        no_mono = False
        use_alternating = True
    else:
        no_dual = False
        no_mono = False
        use_alternating = False

    dual_translate_first = config.output.dual_lang_order == "translation_first"
    return no_dual, no_mono, use_alternating, dual_translate_first


def _compute_qps(config: EngineConfig) -> int:
    """Compute QPS from rate limit config."""
    rate_limit = config.translation.rate_limit
    if rate_limit.mode == "custom":
        return max(1, int(rate_limit.qps))
    elif rate_limit.mode == "rpm":
        return max(1, rate_limit.rpm // 60)
    else:
        return max(1, rate_limit.concurrent)


def _compute_pool_max_workers(config: EngineConfig, qps: int) -> int:
    """Compute pool max workers matching PDFMathTranslate-next formula.

    RPM:    min(1000, qps * 10)
    Concurrent: min(1000, max(1, min(int(n * 0.9), max(1, n - 20))))
    Custom: user-specified max_workers, fallback to qps
    """
    rate_limit = config.translation.rate_limit
    if rate_limit.mode == "custom":
        if rate_limit.max_workers and rate_limit.max_workers > 0:
            return rate_limit.max_workers
        return qps
    elif rate_limit.mode == "rpm":
        return min(1000, qps * 10)
    else:  # concurrent
        # Use max(1, concurrent) for intuitive small-value behavior
        # (PDFMathTranslate-next's formula min(1000, max(1, min(int(n*0.9), max(1, n-20))))
        # is designed for defaults of 40+ and yields 1 worker for n=5)
        return max(1, rate_limit.concurrent)


def _clean_system_prompt(prompt_template: str) -> str:
    """Extract domain context from our prompt template for BabelDOC.

    BabelDOC constructs its own user message, so we only use the
    domain/context instructions from our template (not the variable
    placeholders like {text}).
    """
    lines = []
    for line in prompt_template.splitlines():
        stripped = line.strip()
        # Skip empty lines and variable placeholders
        if not stripped:
            continue
        if stripped.startswith("{") and stripped.endswith("}"):
            continue
        if "text to translate" in stripped.lower():
            continue
        if stripped.lower().startswith("chinese translation"):
            continue
        lines.append(line)

    if not lines:
        return "You are a professional translator. Translate accurately into Chinese."

    return "\n".join(lines)


async def translate_with_babeldoc(
    input_path: str | Path,
    config: EngineConfig,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict[str, str]:
    """Translate a digital PDF using BabelDOC for layout preservation.

    Args:
        input_path: Path to input PDF
        config: Engine configuration
        progress_callback: Optional callback(stage, current_pct, total_pct, message)

    Returns:
        Dictionary mapping output keys to file paths
    """
    input_path = Path(input_path)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = input_path.stem

    # Configure rate limiting
    qps = _compute_qps(config)
    set_translate_rate_limiter(qps)

    # Create translator with custom domain context
    translator = CustomOpenAITranslator(
        config=config,
        lang_in="en",
        lang_out="zh",
        model=config.translation.model,
        base_url=config.translation.base_url,
        api_key=config.translation.api_key,
        ignore_cache=False,
    )

    # Map output mode
    no_dual, no_mono, use_alternating, dual_translate_first = _map_output_mode(config)

    # If user doesn't want PDF output at all, skip BabelDOC PDF generation
    want_pdf = "pdf" in config.output.formats
    if not want_pdf:
        no_dual = True
        no_mono = True

    # Determine pool workers (match PDFMathTranslate-next formula)
    pool_max_workers = _compute_pool_max_workers(config, qps)

    # Build BabelDOC config
    system_prompt = _clean_system_prompt(config.translation.prompt_template)

    babeldoc_config = TranslationConfig(
        translator=translator,
        input_file=str(input_path),
        lang_in="en",
        lang_out="zh",
        doc_layout_model=DocLayoutModel.load_available(),
        output_dir=str(output_dir),
        pages=config.pages or None,
        no_dual=no_dual,
        no_mono=no_mono,
        qps=qps,
        pool_max_workers=pool_max_workers,
        dual_translate_first=dual_translate_first,
        use_alternating_pages_dual=use_alternating,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        custom_system_prompt=system_prompt,
        min_text_length=3,
        only_include_translated_page=bool(config.pages),
    )

    output_files: dict[str, str] = {}
    result_obj = None

    # Run BabelDOC translation and stream progress
    async for event in async_translate(babeldoc_config):
        event_type = event.get("type")

        if event_type == "progress_start":
            stage = event.get("stage", "processing")
            if progress_callback:
                progress_callback(stage, 0, 100, f"Started: {stage}")

        elif event_type == "progress_update":
            overall = event.get("overall_progress", 0)
            stage = event.get("stage", "processing")
            current = event.get("stage_current", 0)
            total = event.get("stage_total", 1)
            if stage == "Translate Paragraphs":
                msg = f"{stage}: {current}/{total} ({pool_max_workers} workers)"
            else:
                msg = f"{stage}: {current}/{total}"
            if progress_callback:
                progress_callback("translating", int(overall), 100, msg)

        elif event_type == "progress_end":
            stage = event.get("stage", "processing")
            overall = event.get("overall_progress", 100)
            if progress_callback:
                progress_callback("translating", int(overall), 100, f"Completed: {stage}")

        elif event_type == "finish":
            result_obj = event.get("translate_result")
            break

        elif event_type == "error":
            error = event.get("error", "Unknown error")
            raise RuntimeError(f"BabelDOC translation failed: {error}")

    if result_obj is None:
        raise RuntimeError("BabelDOC did not return a result")

    # Collect output files and rename to our convention
    pdf_outputs = []

    # Mono PDF (translation only, layout preserved)
    if hasattr(result_obj, "mono_pdf_path") and result_obj.mono_pdf_path:
        src = Path(result_obj.mono_pdf_path)
        if src.exists():
            pdf_outputs.append(("mono", src))

    # Dual PDF (side-by-side or alternating)
    if hasattr(result_obj, "dual_pdf_path") and result_obj.dual_pdf_path:
        src = Path(result_obj.dual_pdf_path)
        if src.exists():
            pdf_outputs.append(("dual", src))

    # No-watermark variants (fallback)
    if hasattr(result_obj, "no_watermark_mono_pdf_path") and result_obj.no_watermark_mono_pdf_path:
        src = Path(result_obj.no_watermark_mono_pdf_path)
        if src.exists() and ("mono", src) not in pdf_outputs:
            pdf_outputs.append(("mono_nowm", src))

    if hasattr(result_obj, "no_watermark_dual_pdf_path") and result_obj.no_watermark_dual_pdf_path:
        src = Path(result_obj.no_watermark_dual_pdf_path)
        if src.exists() and ("dual", src) not in pdf_outputs:
            pdf_outputs.append(("dual_nowm", src))

    # Select the best output based on user's mode preference
    selected_pdf: Path | None = None

    if config.output.mode == "mono" and pdf_outputs:
        # Prefer mono
        for mode, path in pdf_outputs:
            if mode == "mono":
                selected_pdf = path
                break
        if selected_pdf is None:
            selected_pdf = pdf_outputs[0][1]
    elif config.output.mode in ("dual", "alternating") and pdf_outputs:
        # Prefer dual
        for mode, path in pdf_outputs:
            if mode == "dual":
                selected_pdf = path
                break
        if selected_pdf is None:
            selected_pdf = pdf_outputs[0][1]
    elif pdf_outputs:
        selected_pdf = pdf_outputs[0][1]

    if selected_pdf:
        dest = output_dir / f"{base_name}_translated.pdf"
        shutil.copy2(selected_pdf, dest)
        output_files["pdf"] = str(dest)

    # Clean up intermediate BabelDOC files (keep only final output)
    _cleanup_babeldoc_files(output_dir, base_name, dest if selected_pdf else None)

    return output_files


def _cleanup_babeldoc_files(output_dir: Path, base_name: str, keep_file: Path | None) -> None:
    """Remove BabelDOC intermediate files, keeping only the final output."""
    patterns = [
        f"{base_name}.no_watermark.*.pdf",
        f"{base_name}.no_watermark.*.csv",
        f"{base_name}.*.glossary.csv",
        f"{base_name}.*.mono.pdf",
        f"{base_name}.*.dual.pdf",
        f"{base_name}.*.watermark.pdf",
    ]
    for pattern in patterns:
        for f in output_dir.glob(pattern):
            if keep_file and f.resolve() == keep_file.resolve():
                continue
            try:
                f.unlink()
            except OSError:
                pass
