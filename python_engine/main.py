"""CLI entry point for PDF translation engine.

Usage:
    python -m python_engine.main input.pdf --config config.toml
    python -m python_engine.main input.pdf --json-config '{"translation": {...}}'

Progress events are emitted as JSON lines to stdout for Tauri integration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from python_engine.config import EngineConfig, load_config
from python_engine import diagnostics
from python_engine.engine import TranslationEngine


def _setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to write to stderr so it doesn't collide with stdout JSON events."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF Translation Engine")
    parser.add_argument("input", help="Input PDF file path")
    parser.add_argument("--config", "-c", help="Path to TOML config file")
    parser.add_argument("--json-config", "-j", help="JSON config string (for Tauri)")
    parser.add_argument("--output-dir", "-o", help="Output directory")
    parser.add_argument("--pages", "-p", help="Page ranges, e.g. 1-10 or 1,3,5")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    # Load configuration
    if args.json_config:
        config_data = json.loads(args.json_config)
        config = EngineConfig.from_dict(config_data)
    elif args.config:
        config = load_config(args.config)
    else:
        config = load_config()

    # Override with CLI args
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.pages:
        config.pages = args.pages
    if args.debug:
        config.debug = True

    # Setup logging — must go to stderr because stdout is reserved for JSON events
    log_level = logging.DEBUG if config.debug else logging.INFO
    _setup_logging(log_level)
    # Persist logs to a file as well: the packaged app hides stderr, so without
    # this there is no way to check what was actually sent to the API.
    log_file = diagnostics.configure()
    logger = logging.getLogger(__name__)
    diagnostics.reset()
    logger.info("Engine starting: input=%s pages=%s mode=%s", args.input, config.pages, config.scanned.mode)

    # Echo the effective request configuration so a missing/ineffective
    # thinking-off switch is visible immediately instead of showing up on the bill.
    logger.info(
        "[config] base_url=%s model=%s temperature=%s max_tokens=%s",
        config.translation.base_url,
        config.translation.model,
        config.translation.temperature,
        config.translation.max_tokens,
    )
    if config.translation.extra_body:
        logger.info("[config] extra_body=%s", config.translation.extra_body)
    else:
        logger.warning(
            "[config] extra_body is EMPTY — nothing is disabling reasoning. "
            "Hybrid-reasoning models (doubao-seed-2.x, Qwen3, Gemini 2.5) will "
            "emit thinking tokens that are billed as OUTPUT."
        )
    if log_file:
        logger.info("[config] diagnostics log: %s", log_file)

    # Resolve output_dir: CLI flag wins, otherwise fall back to the user-facing
    # output.default_output_dir UI setting. Expand `~` so paths like
    # `~/Downloads` work on every platform.
    chosen = config.output_dir or config.output.default_output_dir or "~/Downloads"
    config.output_dir = str(Path(chosen).expanduser())

    # Validate API key
    if not config.translation.api_key:
        print(json.dumps({"type": "error", "message": "API key not configured"}), file=sys.stderr)
        return 1

    # Run translation
    engine = TranslationEngine(config)

    try:
        output_files = await engine.translate_document(args.input)
        diagnostics.tracker.log_summary()
        if not output_files:
            return 1
        return 0
    except Exception as e:
        diagnostics.tracker.log_summary()
        msg = str(e)
        # Truncate very long messages for UI display
        if len(msg) > 800:
            msg = msg[:800] + "..."
        print(json.dumps({"type": "error", "message": msg}), file=sys.stderr)
        if config.debug:
            raise
        return 1


def cli() -> None:
    """Synchronous CLI entry point."""
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
