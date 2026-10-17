#!/bin/zsh
# Start API (8000) and web (3000) together. Ctrl-C stops both, including their child processes.
set -euo pipefail
cd "$(dirname "$0")"

# Find the lowest free TCP port at or above $1
find_free_port() {
  local port=$1
  while nc -z localhost $port 2>/dev/null; do
    (( port++ ))
  done
  echo $port
}

echo "▶ PRIDE Map dev startup"
echo ""

# 1. Demo data
if [ ! -f data/pride.db ]; then
  echo "📦 Loading demo data (296 datasets) into data/pride.db..."
  uv run load_demo.py
  echo "✅ Demo data loaded"
else
  echo "✅ data/pride.db already exists — skipping demo load"
fi
echo ""

# 2. Resolve ports (fall back to next free port if preferred is taken)
API_PORT=$(find_free_port 8000)
WEB_PORT=$(find_free_port 3000)

if [ "$API_PORT" != "8000" ]; then
  echo "⚠️  Port 8000 is in use — API will use :$API_PORT instead"
fi
if [ "$WEB_PORT" != "3000" ]; then
  echo "⚠️  Port 3000 is in use — Web UI will use :$WEB_PORT instead"
fi

# 3. API
echo ""
echo "🚀 Starting API server on http://localhost:$API_PORT ..."
PORT=$API_PORT uv run api/main.py &
API=$!

# 4. Web
echo "🌐 Starting web UI on http://localhost:$WEB_PORT ..."
(
  cd web
  if [ ! -d node_modules ]; then
    echo "📥 node_modules not found — running npm install (this may take ~30s)..."
    npm i
    echo "✅ npm install complete"
  fi
  exec env PORT=$WEB_PORT NEXT_PUBLIC_API_URL=http://localhost:$API_PORT npm run dev -- --port $WEB_PORT
) &
WEB=$!

cleanup() { kill $API $WEB 2>/dev/null; }
trap cleanup EXIT INT TERM

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  API docs  →  http://localhost:$API_PORT/docs"
echo "  Web UI    →  http://localhost:$WEB_PORT"
echo "  (Ctrl-C to stop both)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
wait
