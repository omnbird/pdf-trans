#!/usr/bin/env bash
# Start the pdf-trans web front-end (serves dist/ and the translation API).
# Usage: web/start.sh [port]        (default port 8123)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-${PORT:-8123}}"

if [ ! -f dist/index.html ]; then
  echo "dist/ not found — building frontend…"
  npm run build
fi

exec python3 -m web.server --port "$PORT"
