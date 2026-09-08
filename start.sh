#!/usr/bin/env bash
# Serve web/ on http://127.0.0.1:8765 in the background. Safe to run twice.
#
# The page must be served rather than opened as a file: it fetches JSON, and
# browsers block that on file://, so opening index.html directly gives a blank
# page. This is a plain static file server — there is no backend service yet.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8765}"
URL="http://127.0.0.1:$PORT"
PIDFILE=".server.pid"

if curl -fs -o /dev/null "$URL/" 2>/dev/null; then
  echo "Already running at $URL"
  exit 0
fi

nohup python3 -m http.server "$PORT" --directory web >server.log 2>&1 &
echo $! >"$PIDFILE"

# --retry-connrefused waits for the port to open without a sleep guess.
if curl -fs -o /dev/null --retry 15 --retry-delay 1 --retry-connrefused "$URL/"; then
  echo "Transfer Room running at $URL"
  echo "Stop it with ./stop.sh   ·   request log in server.log"
else
  echo "Server failed to start. Last lines of server.log:" >&2
  tail -n 20 server.log >&2
  rm -f "$PIDFILE"
  exit 1
fi
