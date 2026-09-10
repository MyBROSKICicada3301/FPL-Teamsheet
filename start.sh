#!/usr/bin/env bash
# Serve the FPL Teamsheet on http://127.0.0.1:8765 in the background.
set -euo pipefail

cd "$(dirname "$0")"

# The Python side loads .env itself; this is only so PORT is known here, and so
# a key exported into this shell reaches the server process. Values already in
# the environment are left alone, matching fpl/env.py.
if [ -f .env ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    key=${line%%=*}
    key=${key#export }
    key=$(printf '%s' "$key" | tr -d '[:space:]')
    [ -z "$key" ] && continue
    eval "current=\${$key-}"
    [ -n "$current" ] && continue
    value=${line#*=}
    export "$key=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e "s/^[\"']//" -e "s/[\"']$//")"
  done < .env
fi

PORT="${PORT:-8765}"
URL="http://127.0.0.1:$PORT"
PIDFILE=".server.pid"

if curl -fs -m 5 -o /dev/null "$URL/api/healthz" 2>/dev/null; then
  echo "Already running at $URL"
  exit 0
fi

# A port that is occupied but not answering our health check is somebody
# else's process, not a failed start of ours. Saying so beats launching a
# server that cannot bind and then blaming the log it never wrote.
if command -v ss >/dev/null && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  echo "Port $PORT is in use, but nothing there answers /api/healthz." >&2
  echo "  what is on it:  ss -ltnp | grep $PORT" >&2
  echo "  or use another: PORT=8766 ./start.sh" >&2
  exit 1
fi

nohup python3 -m fpl.server --port "$PORT" >server.log 2>&1 &
echo $! >"$PIDFILE"

# --retry-connrefused waits for the port to open without a sleep guess. The
# health check answers immediately now — projections load in the background and
# report themselves as "starting" — so this only has to cover process startup.
if curl -fs -m 10 -o /dev/null --retry 15 --retry-delay 1 --retry-connrefused "$URL/api/healthz"; then
  echo "FPL Teamsheet running at $URL"
  echo "Stop it with ./stop.sh   ·   request log in server.log"
else
  echo "Server failed to start. Last lines of server.log:" >&2
  tail -n 20 server.log >&2
  rm -f "$PIDFILE"
  exit 1
fi
