"""Database connection pool for the API.

Every pooled connection is forced read-only with a 5s statement timeout, so no
endpoint (or bug) can write, and no query can hang a request.
"""

import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool: ConnectionPool | None = None


class DatabaseUnavailable(Exception):
    """No DATABASE_URL configured, so there is nothing to connect to."""


def _configure(conn: psycopg.Connection) -> None:
    conn.autocommit = True  # each SELECT is its own transaction; SETs below last for the session
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '5s'")


def open_pool() -> None:
    global pool
    url = os.environ.get("DATABASE_URL")
    if not url:
        pool = None  # /health still works; data endpoints answer 503
        return
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=4,  # well inside Supabase's free session-pooler limit
        timeout=10,  # seconds to wait for a connection before giving up (-> 503)
        kwargs={"row_factory": dict_row},
        configure=_configure,
        open=False,
    )
    pool.open(wait=False)  # don't block startup while Supabase wakes up


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


def fetch_all(query, params: dict | None = None) -> list[dict]:
    if pool is None:
        raise DatabaseUnavailable()
    with pool.connection() as conn:
        return conn.execute(query, params).fetchall()


def fetch_one(query, params: dict | None = None) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None
