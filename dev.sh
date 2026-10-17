#!/bin/zsh
# Start API (8000) and web (3000) together. Ctrl-C stops both, including their child processes.
cd "$(dirname "$0")"
[ -f data/pride.db ] || uv run load_demo.py
# clear anything stale on our ports from a previous run
for port in 8000 3000; do lsof -ti tcp:$port | xargs kill 2>/dev/null; done
sleep 0.5
uv run api/main.py &
API=$!
( cd web && ([ -d node_modules ] || npm i) && exec npm run dev ) &
WEB=$!
cleanup() { for port in 8000 3000; do lsof -ti tcp:$port | xargs kill 2>/dev/null; done; kill $API $WEB 2>/dev/null; }
trap cleanup EXIT INT TERM
echo "API  http://localhost:8000/docs"
echo "Web  http://localhost:3000"
wait
