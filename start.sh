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

# The python.org Windows installer ships python.exe and the py launcher but no
# python3.exe, and the python3 that PATH does find there is a Microsoft Store
# alias stub that only prints "Python was not found" and exits 49. So the name
# cannot be trusted: every candidate has to prove it runs and is new enough.
#
# What a candidate reports back is sys.executable rather than the name we
# called it by, because `py -3` is a launcher that runs the real interpreter
# as a child process. Kill the launcher afterwards and the child lives on,
# still holding the port -- which is how stop.sh came to print "Stopped"
# over a server that was still serving. Starting the interpreter itself
# keeps $! pointing at the process that actually has to die.
probe='import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)'

PY=""
for candidate in "${PYTHON:-}" python3 "py -3" python; do
  [ -n "$candidate" ] || continue
  # Unquoted on purpose: "py -3" must split into a command and an argument.
  # shellcheck disable=SC2086
  PY=$($candidate -c "$probe" 2>/dev/null) || PY=""
  if [ -n "$PY" ]; then
    break
  fi
done

if [ -z "$PY" ]; then
  echo "No Python 3.10 or newer found (tried python3, py -3, python)." >&2
  echo "  install it:    https://www.python.org/downloads/" >&2
  echo "  or name yours: PYTHON=/path/to/python ./start.sh" >&2
  exit 1
fi

nohup "$PY" -m fpl.server --port "$PORT" >server.log 2>&1 &
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
