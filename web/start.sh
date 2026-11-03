#!/usr/bin/env bash
# Start the pdf-trans web front-end (serves dist/ and the translation API).
# Usage: web/start.sh [port]        (default port 8123)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-${PORT:-8123}}"

# Prefer the project venv (it has babeldoc, so layout-preserving PDF output
# works). Fall back to the system interpreter with a warning.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "warning: .venv not found — babeldoc (layout-preserving PDF) will be unavailable" >&2
  PY="$(command -v python3)"
fi

# Apply the optional BabelDOC typesetting fix (CJK paragraphs that are only
# slightly too long get shrunk instead of using the free space below them).
# Idempotent; skip with PDF_TRANS_NO_BABELDOC_PATCH=1.
if [ "${PDF_TRANS_NO_BABELDOC_PATCH:-0}" != "1" ] && [ -x "$PY" ]; then
  "$PY" scripts/patch_babeldoc_typesetting.py >/dev/null 2>&1 || true
fi

if [ ! -f dist/index.html ]; then
  echo "dist/ not found — building frontend…"
  npm run build
fi

exec "$PY" -m web.server --port "$PORT"
