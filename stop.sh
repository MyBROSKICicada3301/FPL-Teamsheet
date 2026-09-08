#!/usr/bin/env bash
# Stop the server started by ./start.sh.
set -euo pipefail

cd "$(dirname "$0")"
PIDFILE=".server.pid"

if [ ! -f "$PIDFILE" ]; then
  echo "Not running (no $PIDFILE)."
  exit 0
fi

PID="$(cat "$PIDFILE")"
rm -f "$PIDFILE"

# Kill by PID, not `pkill -f`: -f matches whole command lines and would also
# hit any other shell that happens to have that string in its arguments.
if kill "$PID" 2>/dev/null; then
  echo "Stopped (pid $PID)."
else
  echo "Nothing to stop — pid $PID was already gone."
fi
