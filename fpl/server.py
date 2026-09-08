"""Local HTTP service: JSON endpoints plus the single page that uses them.

Standard library only — `http.server` with a thread pool, which is the right
size of tool for something that runs on one machine for one person. There is
no framework here because there is nothing for a framework to do: five routes,
no auth, no writes.

**Errors are a contract, not a stack trace.** Every failure leaves through
`_fail` and arrives as the same envelope with a machine-readable code:

    {"error": {"code": "team_not_found", "message": "...", "status": 404}}

so the page can decide what to say without parsing prose. The codes are the
ones raised in `engine.InputError` and `data.FPLError`, and they are listed in
`ERROR_CODES` below so the set is documented rather than discovered.
"""

import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import advice, coach, data, engine, squad as squad_mod
from .rules import Rules

WEB_DIR = Path(__file__).resolve().parent.parent / "fplweb"

#: Every code this service can return, and what it means. Kept here so the set
#: is a documented contract rather than whatever the code happens to raise.
ERROR_CODES = {
    "bad_request":          (400, "The request was malformed."),
    "invalid_team_id":      (400, "The team id must be a positive whole number."),
    "unknown_player":       (400, "A named player is not in this season's game."),
    "ambiguous_player":     (400, "A name matched more than one player."),
    "squad_size":           (400, "A squad must be exactly 15 players."),
    "duplicate_player":     (400, "The same player appears twice."),
    "position_quota":       (400, "The squad breaks the 2/5/5/3 position quota."),
    "club_limit":           (400, "More than three players from one club."),
    "over_budget":          (400, "The squad costs more than the money available."),
    "negative_bank":        (400, "The bank cannot be negative."),
    "not_found":            (404, "No such route."),
    "team_not_found":       (404, "No FPL team with that id."),
    "team_not_started":     (409, "That team has not played a gameweek yet."),
    "season_over":          (409, "The season has finished."),
    "method_not_allowed":   (405, "That route does not accept this method."),
    "payload_too_large":    (413, "The request body is too large."),
    "upstream_error":       (502, "Fantasy Premier League returned an error."),
    "upstream_unreachable": (504, "Could not reach Fantasy Premier League."),
    "coach_no_key":         (503, "No GEMINI_API_KEY in the server environment."),
    "coach_upstream_error": (502, "Gemini returned an error."),
    "coach_unreachable":    (504, "Could not reach Gemini."),
    "coach_empty":          (502, "Gemini returned no usable text."),
    "internal_error":       (500, "Something broke on this side."),
}

MAX_BODY = 64 * 1024

#: A loaded Context is a megabyte of projections and takes the best part of a
#: minute to build on a cold cache, so it is held and reused.
_ctx_cache: dict[int, engine.Context] = {}

#: One lock per horizon, plus a lock guarding the lock table itself. A single
#: global lock meant a request building the 6-gameweek context blocked every
#: other request — including the health check, which then timed out and made a
#: perfectly healthy server look dead. Locks are per-horizon so two different
#: builds proceed independently, and no lock is ever held by a reader.
_locks_guard = threading.Lock()
_locks: dict[int, threading.Lock] = {}


def _lock_for(horizon: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(horizon, threading.Lock())


def peek(horizon: int) -> engine.Context | None:
    """The context if it is already built, without building or waiting."""
    return _ctx_cache.get(horizon)


def context(horizon: int) -> engine.Context:
    ready = _ctx_cache.get(horizon)
    if ready is not None:
        return ready

    with _lock_for(horizon):
        # Another thread may have finished while this one waited.
        ready = _ctx_cache.get(horizon)
        if ready is None:
            ready = engine.load(horizon)
            _ctx_cache[horizon] = ready
        return ready


def invalidate() -> None:
    _ctx_cache.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "fpl-assistant"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):          # one tidy line per request
        print(f"  {self.address_string()} {fmt % args}")

    def _send(self, status: int, payload: dict | list, cache: int = 0) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         f"public, max-age={cache}" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code: str, message: str | None = None, status: int | None = None):
        known_status, known_message = ERROR_CODES.get(code, (500, "Unexpected error."))
        self._send(status or known_status, {
            "error": {
                "code": code,
                "message": message or known_message,
                "status": status or known_status,
            }
        })

    # ---------------------------------------------------------------- routes

    def do_GET(self):                            # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path.rstrip("/") or "/"

        try:
            if route == "/api/healthz":
                return self._healthz()
            if route == "/api/gameweek":
                return self._gameweek(query)
            if route == "/api/players":
                return self._players(query)
            if route == "/api/advice":
                return self._advice_get(query)
            if route == "/api/coach":
                return self._coach_get(query)
            if route.startswith("/api"):
                return self._fail("not_found", f"No route {url.path}.")
            return self._static(url.path)
        except engine.InputError as e:
            return self._fail(e.code, str(e), e.status)
        except data.FPLError as e:
            return self._fail(e.code, str(e))
        except Exception:                        # noqa: BLE001
            traceback.print_exc()
            return self._fail("internal_error")

    def do_POST(self):                           # noqa: N802
        url = urlparse(self.path)
        if url.path.rstrip("/") != "/api/advice":
            return self._fail("not_found", f"No route {url.path}.")

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._fail("payload_too_large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                return self._fail("bad_request", "The body must be a JSON object.")
            return self._advice_post(body)
        except json.JSONDecodeError as e:
            return self._fail("bad_request", f"The body is not valid JSON: {e.msg}.")
        except engine.InputError as e:
            return self._fail(e.code, str(e), e.status)
        except data.FPLError as e:
            return self._fail(e.code, str(e))
        except Exception:                        # noqa: BLE001
            traceback.print_exc()
            return self._fail("internal_error")

    def do_HEAD(self):                           # noqa: N802
        self.do_GET()

    # --------------------------------------------------------------- handlers

    def _healthz(self):
        """Is the process up and serving.

        Deliberately does no work: it reports the context only if one is
        already built. A health check that waits on a projection build is not a
        health check — it is the thing that made `start.sh` declare a running
        server dead, because the probe queued behind a cold-cache load and was
        eventually reset.

        "starting" is a 200. The server is up and answering; it simply has not
        finished loading, which is exactly what a startup probe needs to know.
        """
        ctx = peek(advice.DEFAULT_HORIZON)
        if ctx is None:
            return self._send(200, {"status": "starting",
                                    "detail": "projections are still loading"})
        return self._send(200, {
            "status": "ok",
            "gameweek": ctx.next_gw,
            "deadline": ctx.deadline,
            "players": len(ctx.players),
        })

    def _gameweek(self, query):
        ctx = context(self._horizon(query))
        return self._send(200, {
            "gameweek": ctx.next_gw,
            "deadline": ctx.deadline,
            "horizon": ctx.horizon,
            "rules": {
                "squad_size": ctx.rules.squad_size,
                "budget": ctx.rules.total_spend,
                "team_limit": ctx.rules.team_limit,
                "max_free_transfers": ctx.rules.max_free_transfers,
                "hit": advice.POINTS_PER_EXTRA_TRANSFER,
            },
            # So the page can hide the briefing button rather than offering
            # something that is certain to fail.
            "coach": {
                "available": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
                "model": coach.MODEL,
            },
            # Convenience only, and only ever the operator's own id from .env.
            # An FPL team id is public, the number in every league table, so
            # this is saving a copy and paste rather than exposing anything.
            "default_team": os.environ.get("FPL_TEAM_ID", "").strip() or None,
        }, cache=300)

    def _players(self, query):
        """Name search, for typing a squad in by hand."""
        ctx = context(self._horizon(query))
        term = (query.get("search", [""])[0] or "").strip().casefold()
        limit = min(50, max(1, int(query.get("limit", ["25"])[0] or 25)))

        rows = []
        for pid, p in ctx.players.items():
            if term and term not in p.name.casefold():
                continue
            rows.append({
                "id": pid, "name": p.name, "team": p.team_short,
                "position": p.position_name, "cost": p.cost,
                "xp_next": round(ctx.projections[pid].per_gw.get(ctx.next_gw, 0.0), 2),
            })
        rows.sort(key=lambda r: -r["xp_next"])
        return self._send(200, {"players": rows[:limit]}, cache=300)

    def _advice_get(self, query):
        raw = (query.get("team", [""])[0] or "").strip()
        if not re.fullmatch(r"\d{1,12}", raw):
            return self._fail(
                "invalid_team_id",
                "Pass ?team= with your FPL team id — the number in the URL when "
                "you view your team on the FPL site.",
            )

        ctx = context(self._horizon(query))
        team_id = int(raw)
        sq = engine.squad_from_team_id(team_id, ctx)
        hist = data.history(team_id)

        override = query.get("free_transfers", [None])[0]
        if override is not None and override.isdigit():
            sq.free_transfers = max(0, min(ctx.rules.max_free_transfers, int(override)))

        result = engine.advise(sq, ctx, self._max_transfers(query), hist, team_id)
        return self._send(200, result, cache=60)

    def _coach_get(self, query):
        """The written briefing, generated on demand rather than with the advice.

        Kept as a separate request for two reasons. It costs an upstream call
        against a quota that is small on a free key, so it should happen when
        somebody asks for it and not on every page load. And it takes tens of
        seconds, which is far too long to hold up the numbers the reader came
        for.
        """
        raw = (query.get("team", [""])[0] or "").strip()
        if not re.fullmatch(r"\d{1,12}", raw):
            return self._fail(
                "invalid_team_id",
                "Pass ?team= with your FPL team id.",
            )

        ctx = context(self._horizon(query))
        team_id = int(raw)
        sq = engine.squad_from_team_id(team_id, ctx)
        hist = data.history(team_id)

        override = query.get("free_transfers", [None])[0]
        if override is not None and override.isdigit():
            sq.free_transfers = max(0, min(ctx.rules.max_free_transfers, int(override)))

        report = engine.advise(sq, ctx, self._max_transfers(query), hist, team_id)
        try:
            written = coach.advise(report)
        except coach.CoachError as e:
            return self._fail(e.code, str(e), e.status)
        return self._send(200, written)

    def _advice_post(self, body):
        ctx = context(self._horizon({}))
        names = body.get("players")
        if not isinstance(names, list) or not names:
            return self._fail(
                "bad_request",
                'Send {"players": [...]} with 15 player names or ids.',
            )
        if len(names) != ctx.rules.squad_size:
            return self._fail(
                "squad_size",
                f"A squad is {ctx.rules.squad_size} players; {len(names)} were sent.",
            )

        ids = []
        for entry in names:
            if isinstance(entry, int):
                ids.append(entry)
            elif isinstance(entry, str) and entry.strip().isdigit():
                ids.append(int(entry))
            else:
                ids.append(entry)
        if any(isinstance(i, str) for i in ids):
            ids = engine.resolve_names([str(i) for i in ids], ctx.players)

        bank = body.get("bank", 0)
        try:
            bank = int(round(float(bank) * 10)) if isinstance(bank, float) else int(bank)
        except (TypeError, ValueError):
            return self._fail("bad_request", "`bank` must be a number, in millions.")

        free = body.get("free_transfers", 1)
        try:
            free = max(0, min(ctx.rules.max_free_transfers, int(free)))
        except (TypeError, ValueError):
            return self._fail("bad_request", "`free_transfers` must be a whole number.")

        sq = squad_mod.Squad(ids, bank=bank, free_transfers=free)
        result = engine.advise(sq, ctx, self._max_transfers(body))
        return self._send(200, result)

    # ----------------------------------------------------------------- static

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()

        # Refuse anything that resolves outside the web directory, which is
        # what stops /../../etc/passwd from being served.
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._fail("not_found", f"No such file: {path}")

        types = {".html": "text/html; charset=utf-8", ".css": "text/css",
                 ".js": "text/javascript", ".json": "application/json",
                 ".svg": "image/svg+xml", ".ico": "image/x-icon",
                 ".woff2": "font/woff2"}
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ----------------------------------------------------------------- params

    @staticmethod
    def _horizon(query) -> int:
        raw = query.get("horizon", [None])[0] if isinstance(query, dict) else None
        if isinstance(raw, str) and raw.isdigit():
            return max(1, min(10, int(raw)))
        if isinstance(query, dict) and isinstance(query.get("horizon"), int):
            return max(1, min(10, query["horizon"]))
        return advice.DEFAULT_HORIZON

    @staticmethod
    def _max_transfers(query) -> int:
        raw = query.get("max_transfers", [None])[0] if isinstance(query, dict) and \
            isinstance(query.get("max_transfers"), list) else query.get("max_transfers")
        try:
            return max(1, min(5, int(raw)))
        except (TypeError, ValueError):
            return 3


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with a deeper backlog and quieter disconnects."""

    daemon_threads = True

    #: socketserver defaults to 5. The preload below means several requests can
    #: arrive before any is answered, and a queue of five overflows into resets
    #: that look like server errors from the client side.
    request_queue_size = 64

    def handle_error(self, request, client_address):
        """A client hanging up is normal, not an error worth a traceback.

        Printing the full ConnectionResetError stack made an ordinary
        disconnect look like a crash — it was the first thing `start.sh` showed
        when something unrelated went wrong, and it sent the diagnosis in
        entirely the wrong direction.
        """
        import sys

        kind = sys.exc_info()[0]
        if kind is not None and issubclass(
            kind, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
        ):
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        httpd = Server((host, port), Handler)
    except OSError as e:
        if e.errno == 98:            # EADDRINUSE
            raise SystemExit(
                f"Port {port} is already in use by another process.\n"
                f"  find it:  ss -ltnp | grep {port}\n"
                f"  or serve elsewhere:  PORT=8766 ./start.sh"
            ) from None
        raise

    print(f"FPL Assistant on http://{host}:{port}", flush=True)

    def preload():
        """Build the projections behind the server rather than in front of it.

        This used to run before `serve_forever()`. The socket was already
        bound, so clients could connect and then wait a full minute for a
        thread that did not exist yet. Loading in the background means the
        server answers from the first instant, and /api/healthz says
        "starting" until this finishes.
        """
        try:
            ctx = context(advice.DEFAULT_HORIZON)
            print(f"  ready, gameweek {ctx.next_gw}, deadline {ctx.deadline}",
                  flush=True)
        except data.FPLError as e:
            print(f"  warning: could not preload ({e}); will retry per request",
                  flush=True)

    threading.Thread(target=preload, name="preload", daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="python3 -m fpl.server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    serve(args.host, args.port)
