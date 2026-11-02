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

if [ ! -f dist/index.html ]; then
  echo "dist/ not found — building frontend…"
  npm run build
fi

exec "$PY" -m web.server --port "$PORT"
