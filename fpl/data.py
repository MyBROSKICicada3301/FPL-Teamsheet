"""The FPL public API, with a cache that keeps us off it.

Everything this tool needs is public and unauthenticated: the whole player
list, the fixture list, and any manager's squad by team id. No key, no signup.

Three manners are built in, because this is somebody else's server:

- **Cache to disk.** `bootstrap-static` is 1.7 MB and changes at most a few
  times a day outside of price changes. Re-downloading it per request would be
  rude and slow. TTLs below are per-endpoint and short enough to catch a price
  change on the day it lands.
- **One connection at a time, with a real user agent.** No parallel fan-out.
- **Fail loudly, with the status.** A 404 on a manager id means the id is
  wrong, and the caller needs to be able to say so rather than showing an
  empty squad.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://fantasy.premierleague.com/api"

CACHE_DIR = Path(os.environ.get(
    "FPL_CACHE", Path(__file__).resolve().parent.parent / ".cache" / "fpl"
))

#: Seconds. Prices settle overnight and squads change up to the deadline, so
#: the static list is held for an hour and anything manager-specific for two
#: minutes.
TTL = {
    "bootstrap-static": 3600,
    "fixtures": 3600,
    "default": 120,
}

TIMEOUT = 30
RETRIES = 3
USER_AGENT = "fpl-assistant/0.1 (+local tool)"


class FPLError(RuntimeError):
    """An API call failed. Carries the status so callers can tell 404 from 503."""

    def __init__(self, message: str, status: int | None = None, code: str = "upstream_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def ttl_for(path: str) -> int:
    """How long a response for this endpoint stays fresh, in seconds."""
    for key, seconds in TTL.items():
        if key != "default" and path.startswith(key):
            return seconds
    return TTL["default"]


def _cache_path(path: str) -> Path:
    safe = path.strip("/").replace("/", "_").replace("?", "_").replace("=", "-") or "root"
    return CACHE_DIR / f"{safe}.json"


def get(path: str, max_age: int | None = None) -> dict | list:
    """GET one endpoint, from the cache when it is fresh enough."""
    cached = _cache_path(path)
    ttl = ttl_for(path) if max_age is None else max_age

    if cached.exists() and time.time() - cached.stat().st_mtime < ttl:
        try:
            return json.loads(cached.read_text())
        except ValueError:
            cached.unlink(missing_ok=True)     # truncated write; refetch

    url = f"{BASE}/{path.lstrip('/')}"
    last: Exception | None = None

    for attempt in range(RETRIES):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                payload = json.load(res)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FPLError(f"Not found upstream: /{path}", 404, "not_found") from None
            if e.code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                last = e
                continue
            raise FPLError(
                f"Fantasy Premier League returned {e.code} for /{path}",
                e.code, "upstream_error",
            ) from None
        except urllib.error.URLError as e:
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                last = e
                continue
            raise FPLError(
                f"Could not reach Fantasy Premier League: {e.reason}",
                None, "upstream_unreachable",
            ) from None
    else:
        raise FPLError(f"Gave up on /{path}: {last}", None, "upstream_unreachable")

    # Write via a temporary file so a killed process cannot leave a half
    # written cache entry that later parses as valid-but-truncated.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(cached)
    return payload


# ------------------------------------------------------------------ endpoints


def bootstrap() -> dict:
    """Players, teams, gameweeks, chips and the rule configuration."""
    return get("bootstrap-static/")


def fixtures(event: int | None = None) -> list:
    """All fixtures, or one gameweek's."""
    return get("fixtures/" if event is None else f"fixtures/?event={event}")


def entry(team_id: int) -> dict:
    """A manager's profile: name, overall rank, current gameweek."""
    return get(f"entry/{team_id}/")


def picks(team_id: int, event: int) -> dict:
    """A manager's squad for one gameweek, with bank and squad value."""
    return get(f"entry/{team_id}/event/{event}/picks/")


def history(team_id: int) -> dict:
    """Per-gameweek history — used to work out banked free transfers."""
    return get(f"entry/{team_id}/history/")


def clear_cache() -> int:
    """Drop every cached response. Returns how many files went."""
    if not CACHE_DIR.exists():
        return 0
    gone = 0
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()
        gone += 1
    return gone
