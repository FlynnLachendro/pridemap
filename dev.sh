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

# Load .env if present so MQ_SERVER_URL, MQ_TOKEN, etc. are available without
# requiring the user to export them manually. Never commit .env itself.
if [ -f .env ]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
  echo "ℹ️  Loaded .env"
fi

echo "▶ PRIDE Map dev startup"
echo ""

# 1. Data. Default is the bundled data/demo shards: no download, ~20 s, every tagged dataset.
#    PRIDEMAP_DATA_SOURCE=release (or auto) pulls the full 41,548-project pride.db.zst from the
#    GitHub release instead — 276 MB down, 1.3 GB on disk; auto falls back to the shards offline.
DATA_SOURCE=${PRIDEMAP_DATA_SOURCE:-demo}
if [ ! -f data/pride.db ]; then
  echo "📦 Building data/pride.db (source: $DATA_SOURCE)..."
  uv run fetch_db.py --source "$DATA_SOURCE"
  echo "✅ Data loaded"
else
  echo "✅ data/pride.db already exists — skipping load"
fi
echo ""

# Port recorded by the last run, if it is still free. Next.js inlines NEXT_PUBLIC_API_URL into the
# BROWSER bundle at compile time, so the API port is baked into every page the user already has
# open. Picking the next free port on each restart therefore orphans running tabs silently: the UI
# polls a dead port, every fetch rejects, and the job looks failed while it runs happily on Modal.
# Reusing the recorded port keeps restarts transparent. PRIDEMAP_API_PORT pins it outright.
previous_api_port() {
  [ -f web/.env.local ] || return 1
  sed -n 's/^NEXT_PUBLIC_API_URL=.*:\([0-9][0-9]*\)$/\1/p' web/.env.local | tail -1
}

# 2. Resolve ports (reuse the last API port when free; else fall back to the next free one)
PREFERRED_API_PORT=${PRIDEMAP_API_PORT:-$(previous_api_port || echo 8000)}
PREFERRED_API_PORT=${PREFERRED_API_PORT:-8000}
API_PORT=$(find_free_port "$PREFERRED_API_PORT")
WEB_PORT=$(find_free_port 3000)

if [ "$API_PORT" != "$PREFERRED_API_PORT" ]; then
  echo "⚠️  Port $PREFERRED_API_PORT is in use — API will use :$API_PORT instead"
  echo "   Any browser tab opened against :$PREFERRED_API_PORT will poll a port this API is NOT on,"
  echo "   and will report running jobs as failed. Reload the UI after startup."
fi

if [ "$WEB_PORT" != "3000" ]; then
  echo "⚠️  Port 3000 is in use — Web UI will use :$WEB_PORT instead"
  echo "   :3000 is a web server from an earlier run, serving a bundle baked with THAT run's API"
  echo "   port. Use http://localhost:$WEB_PORT — the old tab reports healthy jobs as failed."
fi

# 2b. Record the resolved API URL for the web app.
# web/lib/api.ts falls back to http://localhost:8000 when NEXT_PUBLIC_API_URL is unset, so a web
# server started on its own — plain `npm run dev` in web/, not this script — calls whatever holds
# :8000, which is a foreign process whenever find_free_port moved the API. Next.js reads
# web/.env.local automatically, so recording the resolved URL there keeps both start paths in
# agreement. An already-exported NEXT_PUBLIC_API_URL wins, so a deliberate override still stands.
API_URL=${NEXT_PUBLIC_API_URL:-http://localhost:$API_PORT}
if [ -f web/.env.local ]; then
  grep -v '^NEXT_PUBLIC_API_URL=' web/.env.local > web/.env.local.tmp || true
  mv web/.env.local.tmp web/.env.local
fi
echo "NEXT_PUBLIC_API_URL=$API_URL" >> web/.env.local
echo "📝 web/.env.local → NEXT_PUBLIC_API_URL=$API_URL"

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
  exec env PORT=$WEB_PORT NEXT_PUBLIC_API_URL=$API_URL npm run dev -- --port $WEB_PORT
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
