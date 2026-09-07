"""Database access.

One narrow interface over Postgres (production) and SQLite (local dev), so the
service layer can be written once. Queries are written with `?` placeholders
and rewritten to `%s` when talking to Postgres.

Deliberately not an ORM. The queries in this project are the interesting part
of the backend and are clearer written out.
"""

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from . import config


def _connect_sqlite() -> sqlite3.Connection:
    config.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _connect_postgres():
    import psycopg  # imported lazily so dev never needs the driver installed

    return psycopg.connect(config.DATABASE_URL, row_factory=psycopg.rows.dict_row)


@contextmanager
def connect():
    """Yield a connection, committing on success and rolling back on error."""
    conn = _connect_postgres() if config.using_postgres() else _connect_sqlite()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _adapt(sql: str) -> str:
    return sql.replace("?", "%s") if config.using_postgres() else sql


def query(conn, sql: str, params: Sequence[Any] = ()) -> list[dict]:
    """Run a SELECT and return rows as plain dicts."""
    cur = conn.cursor()
    cur.execute(_adapt(sql), tuple(params))
    rows = cur.fetchall()
    if config.using_postgres():
        return [dict(r) for r in rows]
    return [dict(r) for r in rows]


def query_one(conn, sql: str, params: Sequence[Any] = ()) -> dict | None:
    rows = query(conn, sql, params)
    return rows[0] if rows else None


def execute(conn, sql: str, params: Sequence[Any] = ()) -> None:
    conn.cursor().execute(_adapt(sql), tuple(params))


def execute_many(conn, sql: str, rows: Iterable[Sequence[Any]]) -> None:
    conn.cursor().executemany(_adapt(sql), [tuple(r) for r in rows])


def as_json(value: Any) -> Any:
    """Read a JSON column. Postgres hands back parsed values, SQLite strings."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def to_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def init_schema() -> None:
    """Create the schema if it is not already there."""
    name = "schema.postgres.sql" if config.using_postgres() else "schema.sqlite.sql"
    sql = (config.SQL_DIR / name).read_text()
    with connect() as conn:
        if config.using_postgres():
            conn.cursor().execute(sql)
        else:
            conn.executescript(sql)


def reset() -> None:
    """Drop everything and recreate. Dev only."""
    if config.using_postgres():
        raise RuntimeError("reset() refuses to run against Postgres")
    if config.SQLITE_PATH.exists():
        config.SQLITE_PATH.unlink()
    init_schema()
