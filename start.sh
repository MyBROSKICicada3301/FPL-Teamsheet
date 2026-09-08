#!/usr/bin/env bash
# Serve the FPL Assistant on http://127.0.0.1:8765 in the background.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8765}"
URL="http://127.0.0.1:$PORT"
PIDFILE=".server.pid"

if curl -fs -o /dev/null "$URL/api/healthz" 2>/dev/null; then
  echo "Already running at $URL"
  exit 0
fi

nohup python3 -m fpl.server --port "$PORT" >server.log 2>&1 &
echo $! >"$PIDFILE"

# --retry-connrefused waits for the port without a sleep guess. The first
# request builds the projections, so allow it a while.
if curl -fs -o /dev/null --retry 30 --retry-delay 1 --retry-connrefused "$URL/api/healthz"; then
  echo "FPL Assistant running at $URL"
  echo "Stop it with ./stop.sh   ·   request log in server.log"
else
  echo "Server failed to start. Last lines of server.log:" >&2
  tail -n 20 server.log >&2
  rm -f "$PIDFILE"
  exit 1
fi
