#!/bin/zsh
# Start API (8000) and web (3000) together. Ctrl-C stops both.
cd "$(dirname "$0")"
[ -f data/pride.db ] || uv run load_demo.py
uv run api/main.py &
API=$!
( cd web && ([ -d node_modules ] || npm i) && npm run dev ) &
WEB=$!
trap "kill $API $WEB 2>/dev/null" EXIT INT TERM
echo "API  http://localhost:8000/docs"
echo "Web  http://localhost:3000"
wait
