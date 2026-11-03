#!/usr/bin/env python3
"""Improve BabelDOC's CJK fit behaviour (optional post-install patch).

Problem
-------
BabelDOC typesets the translation inside the *original* paragraph's box. When
the Chinese text needs more vertical space (CJK line-skip is 1.5 vs 1.3 for
Latin), it shrinks the font in 0.05 steps and — by design — only starts trying
to *expand* the box once the scale drops below 0.7:

    if scale < 0.7:            # typesetting.py, _find_optimal_scale_and_layout
        ... try expanding the box downward / rightward ...

So a paragraph that is only slightly too long gets rendered at e.g. 0.85 scale
(9.35pt instead of 11pt) even though the page has plenty of free space below
it. That is the "one paragraph suddenly looks smaller" artefact.

Fix
---
Raise the threshold so the box-expansion attempt happens earlier (default 0.9).
Reversible: pass --revert.

Usage
-----
    .venv/bin/python scripts/patch_babeldoc_typesetting.py            # apply
    .venv/bin/python scripts/patch_babeldoc_typesetting.py --revert   # undo
    .venv/bin/python scripts/patch_babeldoc_typesetting.py --threshold 0.85
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARKER = "# [pdf-trans] expanded-box threshold lowered for CJK"
ORIGINAL = "            if scale < 0.7:\n"
PATCHED_TMPL = (
    "            {marker}\n"
    "            if scale < {threshold}:\n"
)


def locate() -> Path:
    try:
        import babeldoc  # noqa: F401
    except ImportError:
        sys.exit("babeldoc is not installed in this interpreter")
    import babeldoc.format.pdf.document_il.midend.typesetting as ts

    return Path(ts.__file__)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.9)
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    path = locate()
    src = path.read_text(encoding="utf-8")

    if args.revert:
        if MARKER not in src:
            print("nothing to revert (patch not applied)")
            return
        src = re.sub(
            re.escape("            " + MARKER + "\n") + r"            if scale < [0-9.]+:\n",
            ORIGINAL,
            src,
        )
        path.write_text(src, encoding="utf-8")
        print(f"reverted: {path}")
        return

    if MARKER in src:
        print("already patched")
        return
    if ORIGINAL not in src:
        sys.exit("unexpected typesetting.py layout — patch not applied")

    patched = PATCHED_TMPL.format(marker=MARKER, threshold=args.threshold)
    src = src.replace(ORIGINAL, patched, 1)
    path.write_text(src, encoding="utf-8")
    print(f"patched {path}: box expansion now starts at scale < {args.threshold}")


if __name__ == "__main__":
    main()
