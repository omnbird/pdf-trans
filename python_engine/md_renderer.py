"""Markdown to reportlab flowables renderer.

Converts Markdown text (headings, paragraphs, tables, lists) into
reportlab Paragraph/Table/Spacer elements for PDF generation.
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle


def _escape_xml(text: str) -> str:
    """Escape XML special characters for reportlab Paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _parse_markdown_table(
    lines: list[str],
    style: ParagraphStyle,
    available_width: float,
    compact: bool = False,
):
    """Parse Markdown table lines into a reportlab Table."""
    data: list[list[Paragraph]] = []
    for line in lines:
        if "---" in line:
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue
        data.append([Paragraph(_escape_xml(c), style) for c in cells])

    if not data:
        return None

    col_count = len(data[0])
    col_width = available_width / col_count if col_count > 0 else available_width

    table = Table(data, colWidths=[col_width] * col_count)
    if compact:
        table.setStyle(
            TableStyle(
                [
                    # Compact: thin horizontal rules, no grid, tight padding
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                    ("TOPPADDING", (0, 0), (-1, -1), 0.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
                ]
            )
        )
    else:
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
    return table


def _make_compact_styles(styles: dict[str, ParagraphStyle]) -> None:
    """Replace styles dict entries with compact (smaller) variants in-place."""
    styles["cn_normal"] = ParagraphStyle(
        "CN_Compact", parent=styles["cn_normal"],
        fontSize=7.5, leading=11, spaceAfter=3,
    )
    styles["cn_heading1"] = ParagraphStyle(
        "CN_CompactH1", parent=styles["cn_heading1"],
        fontSize=11, leading=15, spaceAfter=4,
    )
    styles["cn_heading2"] = ParagraphStyle(
        "CN_CompactH2", parent=styles["cn_heading2"],
        fontSize=9, leading=13, spaceAfter=3,
    )
    styles["cn_heading3"] = ParagraphStyle(
        "CN_CompactH3", parent=styles["cn_heading3"],
        fontSize=8, leading=12, spaceAfter=3,
    )
    styles["cn_quote"] = ParagraphStyle(
        "CN_CompactQuote", parent=styles["cn_normal"],
        fontSize=7.5, leading=11, spaceAfter=3, leftIndent=6,
    )
    styles["cn_table"] = ParagraphStyle(
        "CN_CompactTable", parent=styles["cn_normal"],
        fontSize=6, leading=8, spaceAfter=0,
    )


def markdown_to_story(
    md_text: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
    compact: bool = False,
    skip_tables: bool = False,
) -> list:
    """Convert Markdown text into reportlab flowables.

    Args:
        md_text: Markdown content
        styles: Dict of reportlab ParagraphStyle (cn_heading1/2/3, cn_normal, cn_quote)
        available_width: Width available for tables
        compact: If True, use smaller fonts, tighter spacing, and compact
            table layout so more content fits per page.
        skip_tables: If True, skip table rendering entirely. Useful for dual
            PDF left column where the original scan already shows the tables.

    Returns:
        List of reportlab flowables (Paragraph, Table, Spacer)
    """
    story: list = []
    lines = md_text.splitlines()
    i = 0

    if compact:
        _make_compact_styles(styles)
        story.append(Spacer(1, 3))
    else:
        story.append(Spacer(1, 6))

    # Table cell style: compact mode uses dedicated smaller font (6pt)
    table_style = styles.get("cn_table", styles["cn_normal"]) if compact else styles["cn_normal"]

    # Compact spacers
    spacer_h1 = 3 if compact else 6
    spacer_h2 = 2 if compact else 4
    spacer_h3 = 2 if compact else 4
    spacer_para = 1 if compact else 2
    spacer_table = 2 if compact else 6
    spacer_list = 1 if compact else 2
    spacer_quote = 1 if compact else 2

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1

        if not line:
            continue

        # Skip diagram placeholders
        if line.startswith("[DIAGRAM:"):
            continue

        # Heading 1
        if line.startswith("# ") and not line.startswith("## "):
            story.append(Paragraph(_escape_xml(line[2:].strip()), styles["cn_heading1"]))
            story.append(Spacer(1, spacer_h1))
            continue

        # Heading 2
        if line.startswith("## ") and not line.startswith("### "):
            story.append(Paragraph(_escape_xml(line[3:].strip()), styles["cn_heading2"]))
            story.append(Spacer(1, spacer_h2))
            continue

        # Heading 3+
        if line.startswith("### "):
            story.append(Paragraph(_escape_xml(line[4:].strip()), styles["cn_heading3"]))
            story.append(Spacer(1, spacer_h3))
            continue

        # Table — skip entirely for dual PDF left column (original scan shows them)
        if line.startswith("|"):
            if not skip_tables:
                table_lines = [line]
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                table = _parse_markdown_table(table_lines, table_style, available_width, compact=compact)
                if table:
                    story.append(table)
                    story.append(Spacer(1, spacer_table))
            else:
                # Consume remaining table lines without rendering
                while i < len(lines) and lines[i].strip().startswith("|"):
                    i += 1
            continue

        # List item
        if line.startswith("- ") or line.startswith("* "):
            story.append(Paragraph(f"• {_escape_xml(line[2:])}", styles["cn_normal"]))
            story.append(Spacer(1, spacer_list))
            continue

        # Blockquote
        if line.startswith("> "):
            story.append(Paragraph(_escape_xml(line[2:]), styles["cn_quote"]))
            story.append(Spacer(1, spacer_quote))
            continue

        # Code block markers
        if line.startswith("```"):
            continue

        # Regular paragraph
        story.append(Paragraph(_escape_xml(line), styles["cn_normal"]))
        story.append(Spacer(1, spacer_para))

    return story
