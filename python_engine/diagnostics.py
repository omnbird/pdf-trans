"""Runtime diagnostics for API requests: what was sent, what it cost.

Why this exists
---------------
Hybrid-reasoning models (doubao-seed-2.1, Qwen3, Gemini 2.5, ...) emit
"thinking" tokens that providers bill as *output*. For a pure translation job
that produces a silent 5-10x cost blow-up which is impossible to notice from
the UI. So for every request we record

  * the model and the exact `extra_body` that was sent (i.e. whether the
    thinking-off switch actually reached the wire), and
  * prompt / completion / reasoning token counts from `usage`.

A per-run summary is appended to a log file so it can be inspected after the
fact (the packaged app hides stderr, so a file is the reliable channel).

Log location: ~/.config/pdf-trans/diagnostics.log
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("pdf_trans.usage")

# Budget in CNY per 1M tokens, keyed by a substring of the model id.
# Only providers that bill in CNY are listed; others get ratio-only reporting.
_CNY_PRICES: dict[str, tuple[float, float]] = {
    "doubao-seed-2-1-lite": (0.8, 2.7),
    "doubao-seed-2-0-lite": (0.8, 2.7),
    "doubao-seed-1-6-flash": (0.15, 1.5),
    "deepseek-chat": (2.0, 8.0),
}


def log_path() -> Path:
    """Return the diagnostics log path (created on demand)."""
    return Path.home() / ".config" / "pdf-trans" / "diagnostics.log"


def configure(enable_file: bool = True) -> Path | None:
    """Attach a file handler to the root logger so all INFO logs are persisted.

    Returns the log path, or None when file logging is disabled.
    """
    if not enable_file:
        return None
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Keep the file from growing without bound across runs.
    try:
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            path.write_text("", encoding="utf-8")
    except OSError:
        pass

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == str(path):
            return path

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    return path


def _looks_like_reasoning_model(model: str) -> bool:
    """Best-effort guess: is this a hybrid-reasoning model that thinks by default?"""
    lowered = (model or "").lower()
    hints = (
        "seed-2",
        "thinking",
        "reasoner",
        "qwen3",
        "gemini-2.5",
        "gemini-3",
        "-o1",
        "-o3",
        "-o4",
        "glm-4.5",
        "glm-4.6",
    )
    return any(hint in lowered for hint in hints)


def _get(usage: Any, *names: str) -> int:
    """Read an integer field from a usage object or dict, tolerating absence."""
    for name in names:
        value = None
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _reasoning_tokens(usage: Any) -> int:
    """Extract reasoning/thinking tokens across provider usage shapes."""
    details = None
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
    else:
        details = getattr(usage, "completion_tokens_details", None) or getattr(
            usage, "output_tokens_details", None
        )
    if not details:
        return 0
    return _get(details, "reasoning_tokens", "thinking_tokens")


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Rough CNY cost estimate for known CNY-billed models."""
    lowered = (model or "").lower()
    for key, (in_price, out_price) in _CNY_PRICES.items():
        if key in lowered:
            return prompt_tokens / 1e6 * in_price + completion_tokens / 1e6 * out_price
    return None


class UsageTracker:
    """Accumulates token usage for the current run."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.models: set[str] = set()
        self.warned = False
        self.warned_missing_extra_body = False
        self._logged_params: set[tuple[str, str]] = set()

    def record(
        self,
        *,
        model: str,
        extra_body: dict[str, Any] | None,
        usage: Any,
        stage: str = "",
    ) -> None:
        """Log one API response's parameters and token usage."""
        prompt = _get(usage, "prompt_tokens", "input_tokens")
        completion = _get(usage, "completion_tokens", "output_tokens")
        reasoning = _reasoning_tokens(usage)
        total = _get(usage, "total_tokens") or (prompt + completion)

        self.requests += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.reasoning_tokens += reasoning
        self.models.add(model)

        params_key = (model, repr(sorted((extra_body or {}).items(), key=lambda kv: kv[0])))
        if params_key not in self._logged_params:
            self._logged_params.add(params_key)
            logger.info(
                "[req] stage=%s model=%s extra_body=%s",
                stage or "-",
                model,
                extra_body if extra_body else "{} (none sent)",
            )

        logger.info(
            "[usage] stage=%s prompt=%d completion=%d reasoning=%d total=%d",
            stage or "-",
            prompt,
            completion,
            reasoning,
            total,
        )

        if not extra_body and not self.warned_missing_extra_body:
            if _looks_like_reasoning_model(model):
                self.warned_missing_extra_body = True
                logger.warning(
                    "[req] model=%s looks like a hybrid-reasoning model but extra_body "
                    "is EMPTY — nothing disables thinking, so every response may include "
                    "thinking tokens billed as output. Set it in Settings → Translation API "
                    "→ Extra Request Body (one-click preset for 豆包/方舟).",
                    model,
                )

        # A pure translation should emit roughly as many tokens as it reads.
        # Anything near 2x on a single request means the model is thinking.
        if prompt >= 100 and completion >= 2 * prompt and not self.warned:
            self.warned = True
            logger.warning(
                "[usage] completion tokens (%d) are %.1fx prompt tokens (%d) — "
                "the model is very likely thinking. Thinking tokens are billed as "
                "OUTPUT. Check that extra_body disables reasoning for this model "
                "(doubao/GLM/DeepSeek: {\"thinking\": {\"type\": \"disabled\"}}; "
                "Qwen: {\"enable_thinking\": false}; Gemini: "
                "{\"google\": {\"thinking_config\": {\"thinking_budget\": 0}}}).",
                completion,
                completion / max(prompt, 1),
                prompt,
            )

    def summary_lines(self) -> list[str]:
        """Human-readable multi-line summary for the current run."""
        ratio = self.completion_tokens / self.prompt_tokens if self.prompt_tokens else 0.0
        models = ",".join(sorted(self.models)) or "-"
        lines = [
            f"requests={self.requests} model={models}",
            f"prompt_tokens={self.prompt_tokens} completion_tokens={self.completion_tokens} "
            f"reasoning_tokens={self.reasoning_tokens} output/input={ratio:.2f}x",
        ]
        cost = estimate_cost_cny(models, self.prompt_tokens, self.completion_tokens)
        if cost is not None:
            lines.append(f"estimated_cost=CNY {cost:.4f}")
        if self.reasoning_tokens:
            lines.append(
                "WARNING: reasoning tokens > 0 — thinking is still ON; "
                "they are billed at the output price."
            )
        elif ratio >= 2:
            lines.append(
                "WARNING: output/input ratio >= 2x for a translation task; "
                "thinking may still be enabled."
            )
        else:
            lines.append("OK: token profile looks like plain translation.")
        return lines

    def short_summary(self) -> str:
        """One-line summary suitable for a UI progress message."""
        ratio = self.completion_tokens / self.prompt_tokens if self.prompt_tokens else 0.0
        cost = estimate_cost_cny(",".join(sorted(self.models)), self.prompt_tokens, self.completion_tokens)
        parts = [
            f"API usage: {self.requests} req",
            f"in={self.prompt_tokens:,} out={self.completion_tokens:,} ({ratio:.2f}x)",
        ]
        if self.reasoning_tokens:
            parts.append(f"reasoning={self.reasoning_tokens:,} (thinking ON!)")
        if cost is not None:
            parts.append(f"~CNY {cost:.2f}")
        return " | ".join(parts)

    def log_summary(self) -> None:
        if self.requests == 0:
            logger.warning("[summary] no API requests were recorded")
            return
        logger.info("[summary] ---- run summary ----")
        for line in self.summary_lines():
            logger.info("[summary] %s", line)
        logger.info("[summary] -------------------")


tracker = UsageTracker()


def reset() -> None:
    """Reset counters at the start of a run."""
    tracker.reset()
    logger.info("[summary] PID=%d diagnostics file=%s", os.getpid(), log_path())
