"""Runtime configuration, read from the environment with dev-friendly defaults."""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
SQL_DIR = BACKEND_DIR / "sql"
WEB_DIR = PROJECT_DIR / "web"

#: Postgres in production, SQLite for local dev. A DATABASE_URL beginning
#: postgres:// or postgresql:// switches the driver over; anything else (or
#: nothing at all) uses the SQLite file below.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

SQLITE_PATH = Path(os.environ.get("TRANSFER_ROOM_DB", BACKEND_DIR / "transferroom.db"))

#: Seconds. Matches the Cache-Control the API sets (BACKEND.md §7).
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))

#: Alert threshold from §9: a pipeline that succeeded but ingested nothing is
#: the failure mode that hurts, so /healthz degrades on staleness, not errors.
STALE_AFTER_HOURS = int(os.environ.get("STALE_AFTER_HOURS", "6"))

FEATURE_VERSION = os.environ.get("FEATURE_VERSION", "fv-1")

DEV_HOST = os.environ.get("HOST", "127.0.0.1")
DEV_PORT = int(os.environ.get("PORT", "8000"))


def using_postgres() -> bool:
    return DATABASE_URL.startswith(("postgres://", "postgresql://"))
