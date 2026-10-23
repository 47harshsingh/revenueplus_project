"""Database connection pools for the API.

Two pools, two logins:
- pool:      DATABASE_URL, used by the KPI endpoints.
- chat_pool: CHAT_DATABASE_URL, the chatbot_ro role (db/04_roles.sql). Model-written SQL
             only ever runs here, so even a guard bug can't reach raw tables.
Every connection is forced read-only with a 5s statement timeout.
"""

import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool: ConnectionPool | None = None
chat_pool: ConnectionPool | None = None


class DatabaseUnavailable(Exception):
    """No connection string configured, so there is nothing to connect to."""


def _configure(conn: psycopg.Connection) -> None:
    conn.autocommit = True  # each SELECT is its own transaction; SETs below last for the session
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '5s'")


def _make_pool(env_var: str, max_size: int) -> ConnectionPool | None:
    url = os.environ.get(env_var)
    if not url:
        return None  # /health still works; endpoints needing this pool answer 503
    p = ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,  # both pools together stay well inside Supabase's free pooler limit
        timeout=10,  # seconds to wait for a connection before giving up (-> 503)
        kwargs={"row_factory": dict_row},
        configure=_configure,
        open=False,
    )
    p.open(wait=False)  # don't block startup while Supabase wakes up
    return p


def open_pool() -> None:
    global pool, chat_pool
    pool = _make_pool("DATABASE_URL", max_size=4)
    chat_pool = _make_pool("CHAT_DATABASE_URL", max_size=2)


def close_pool() -> None:
    global pool, chat_pool
    for p in (pool, chat_pool):
        if p is not None:
            p.close()
    pool = chat_pool = None


def _fetch(p: ConnectionPool | None, query, params: dict | None) -> list[dict]:
    if p is None:
        raise DatabaseUnavailable()
    with p.connection() as conn:
        return conn.execute(query, params).fetchall()


def fetch_all(query, params: dict | None = None) -> list[dict]:
    return _fetch(pool, query, params)


def fetch_one(query, params: dict | None = None) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def chat_fetch_all(query, params: dict | None = None) -> list[dict]:
    """Run model-written SQL as chatbot_ro."""
    return _fetch(chat_pool, query, params)
