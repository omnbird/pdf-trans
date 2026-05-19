"""Markdown generation for translated documents.

Generates Markdown with embedded images (relative paths).
Supports bilingual output modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from python_engine.config import OutputConfig
from python_engine.pdf_generator import ContentBlock


def generate_markdown(
    blocks: list[ContentBlock],
    output_path: str | Path,
    config: OutputConfig,
    image_dir: str | Path | None = None,
) -> Path:
    """Generate Markdown file from content blocks.

    Args:
        blocks: Content blocks (paragraphs + images)
        output_path: Output .md file path
        config: Output configuration
        image_dir: Directory containing images (for relative paths)

    Returns:
        Path to generated Markdown file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    mode = config.mode
    translation_first = config.dual_lang_order == "translation_first"

    # Add frontmatter
    lines.append("---")
    lines.append(f'title: "Translated Document"')
    lines.append(f"mode: {mode}")
    lines.append("---")
    lines.append("")

    for block in blocks:
        if block.type == "paragraph" and block.paragraph:
            para = block.paragraph

            if mode == "mono":
                # Translation only
                text = para.translated
                if para.is_heading:
                    prefix = "#" * max(1, min(para.level + 1, 6))
                    lines.append(f"{prefix} {text}")
                else:
                    lines.append(text)
                lines.append("")

            elif mode == "dual":
                # Bilingual side by side (use Markdown table)
                if para.is_heading:
                    prefix = "#" * max(1, min(para.level + 1, 6))
                    if translation_first:
                        lines.append(f"{prefix} {para.translated}")
                        lines.append("")
                        lines.append(f"*{para.original}*")
                    else:
                        lines.append(f"{prefix} {para.original}")
                        lines.append("")
                        lines.append(f"*{para.translated}*")
                else:
                    # Table for parallel text
                    lines.append("| Chinese | English |")
                    lines.append("|---------|---------|")
                    if translation_first:
                        lines.append(f"| {para.translated} | {para.original} |")
                    else:
                        lines.append(f"| {para.original} | {para.translated} |")
                lines.append("")

            elif mode == "alternating":
                # For alternating mode, we put all translations first
                # In Markdown, this is just sequential paragraphs
                if para.is_heading:
                    prefix = "#" * max(1, min(para.level + 1, 6))
                    lines.append(f"{prefix} {para.translated}")
                else:
                    lines.append(para.translated)
                lines.append("")

        elif block.type == "image" and block.image:
            img_path = Path(block.image.image_path)
            if image_dir:
                try:
                    rel_path = img_path.relative_to(image_dir)
                    img_ref = str(rel_path)
                except ValueError:
                    img_ref = block.image.image_path
            else:
                img_ref = block.image.image_path

            lines.append(f"![{block.image.caption or 'Page image'}]({img_ref})")
            lines.append("")

    # For alternating mode, append original text at the end
    if mode == "alternating":
        lines.append("---")
        lines.append("")
        lines.append("# Original Text")
        lines.append("")
        for block in blocks:
            if block.type == "paragraph" and block.paragraph:
                para = block.paragraph
                if para.is_heading:
                    prefix = "#" * max(1, min(para.level + 1, 6))
                    lines.append(f"{prefix} {para.original}")
                else:
                    lines.append(para.original)
                lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
