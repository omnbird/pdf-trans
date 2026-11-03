#!/usr/bin/env python3
"""Optional BabelDOC typesetting tweaks (NOT applied by default).

Background — why a paragraph sometimes renders smaller
-----------------------------------------------------
BabelDOC typesets each translation inside the box of the *original* paragraph
and shrinks the font (in 0.05 steps) until the text fits. CJK output uses a
1.5 line-skip versus 1.3 for Latin, so a Chinese paragraph of the same length
needs ~15% more vertical space and gets scaled down — typically 11pt → 9.35pt
(measured on a 551-page book: 521 of 3693 paragraphs, 14%).

Measurements (same translations, identical prompts, 20 pages of that book):

    vanilla BabelDOC             bottom paragraph 9.35pt
    --expand-threshold 0.9       unchanged (expansion never had room to help)
    --line-skip 1.3              10.45pt  (tighter leading, whole document)
    0.9 + 1.3                    10.45pt

No overflow was observed in any variant: the box expansion is bounded by the
page cropbox and by any block below with horizontal overlap, and if there is no
room BabelDOC falls back to shrinking exactly as before. Lowest text bottom
stayed at y=635.8 of a 666pt page.

So there is no free lunch: at 11pt that paragraph needs more vertical space than
the page offers. `--line-skip 1.3` is the closest fix and changes the look of
every page (denser lines), which is a taste decision — hence opt-in.

Usage
-----
    .venv/bin/python scripts/patch_babeldoc_typesetting.py --line-skip 1.3
    .venv/bin/python scripts/patch_babeldoc_typesetting.py --expand-threshold 0.9
    .venv/bin/python scripts/patch_babeldoc_typesetting.py --revert

Re-running the patcher with different options replaces the previous patch.
Re-installing/upgrading babeldoc drops the patch; re-run this script afterwards.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARK_SCALE = "# [pdf-trans] expanded-box threshold"
MARK_SKIP = "# [pdf-trans] CJK line-skip"

ORIGINAL_SCALE = "            if scale < 0.7:\n"
ORIGINAL_SKIP = "        line_skip = 1.50 if self.is_cjk else 1.3\n"

PATCHED_SCALE = "            {mark}\n            if scale < {value}:\n"
PATCHED_SKIP = "        line_skip = {value}  {mark}\n"

SCALE_RE = re.compile(r" +" + re.escape(MARK_SCALE) + r"[^\n]*\n +if scale < [0-9.]+:\n")
SKIP_RE = re.compile(r" +line_skip = [0-9.]+  " + re.escape(MARK_SKIP) + r"[^\n]*\n")


def target_file() -> Path:
    try:
        import babeldoc  # noqa: F401
    except ImportError:
        sys.exit("babeldoc is not installed in this interpreter")
    import babeldoc.format.pdf.document_il.midend.typesetting as ts

    return Path(ts.__file__)


def strip_patch(src: str) -> str:
    src = SCALE_RE.sub(ORIGINAL_SCALE, src)
    src = SKIP_RE.sub(ORIGINAL_SKIP, src)
    return src


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expand-threshold", type=float, default=None,
                    help="start trying to expand the paragraph box below this scale (BabelDOC default 0.7)")
    ap.add_argument("--line-skip", type=float, default=None,
                    help="CJK line-skip (BabelDOC default 1.5; 1.3 fits more text per box)")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    path = target_file()
    src = strip_patch(path.read_text(encoding="utf-8"))

    if args.revert or (args.expand_threshold is None and args.line_skip is None):
        path.write_text(src, encoding="utf-8")
        print(f"vanilla BabelDOC: {path}")
        return

    if not (ORIGINAL_SCALE in src and ORIGINAL_SKIP in src):
        sys.exit("unexpected typesetting.py layout — cannot patch safely")

    if args.expand_threshold is not None:
        src = src.replace(
            ORIGINAL_SCALE,
            PATCHED_SCALE.format(mark=MARK_SCALE, value=args.expand_threshold),
            1,
        )
    if args.line_skip is not None:
        src = src.replace(
            ORIGINAL_SKIP,
            PATCHED_SKIP.format(mark=MARK_SKIP, value=args.line_skip),
            1,
        )

    path.write_text(src, encoding="utf-8")
    print(
        f"patched {path}\n"
        f"  expand threshold: {args.expand_threshold}\n"
        f"  CJK line-skip    : {args.line_skip}"
    )


if __name__ == "__main__":
    main()
