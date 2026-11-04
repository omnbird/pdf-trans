#!/usr/bin/env bash
# Start the pdf-trans web front-end (serves dist/ and the translation API).
# Usage: web/start.sh [port]        (default port 8123)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-${PORT:-8123}}"

# PaddleOCR pulls opencv-contrib, which links libGL even on a headless box.
# A user-local copy of libGL is used when the distro package is missing.
if [ -d "$HOME/.local/libgl/usr/lib/x86_64-linux-gnu" ]; then
  export LD_LIBRARY_PATH="$HOME/.local/libgl/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# Prefer the project venv (it has babeldoc, so layout-preserving PDF output
# works). Fall back to the system interpreter with a warning.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "warning: .venv not found — babeldoc (layout-preserving PDF) will be unavailable" >&2
  PY="$(command -v python3)"
fi

# Optional BabelDOC typesetting tweaks — see scripts/patch_babeldoc_typesetting.py.
# Off by default (measurements show they only help marginally). Opt in with:
#   PDF_TRANS_BABELDOC_LINE_SKIP=1.3 web/start.sh
if [ -n "${PDF_TRANS_BABELDOC_LINE_SKIP:-}" ] && [ -x "$PY" ]; then
  "$PY" scripts/patch_babeldoc_typesetting.py \
    --line-skip "$PDF_TRANS_BABELDOC_LINE_SKIP" >/dev/null 2>&1 || true
fi

if [ ! -f dist/index.html ]; then
  echo "dist/ not found — building frontend…"
  npm run build
fi

exec "$PY" -m web.server --port "$PORT"
