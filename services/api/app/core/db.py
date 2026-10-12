"""Postgres access helpers (psycopg 3).

Conventions (§6.4):
  * Repositories own SQL; services never touch connections directly.
  * ``get_connection()`` checks out a pooled connection with the
    ``dict_row`` factory.
  * ``transaction()`` yields a pooled connection inside an explicit
    transaction block for multi-statement service operations.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.core.errors import ConfigurationError


_pool: ConnectionPool | None = None


def database_url_or_raise() -> str:
    """Return the configured DATABASE_URL or raise ConfigurationError."""
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise ConfigurationError("DATABASE_URL is not configured.")
    return settings.DATABASE_URL


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url_or_raise(),
            min_size=4,
            max_size=20,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            # prepare_threshold=None: app connects through Supabase pooler;
            # auto-prepared statements collide across pooled server
            # connections (DuplicatePreparedStatement).
            open=True,
        )
    return _pool


def get_connection() -> Any:
    """Check out a pooled connection with dict rows.

    Caller owns the checkout (use as a context manager to return it).
    """
    return get_pool().connection()


@contextmanager
def transaction() -> Iterator[Any]:
    """Yield a pooled connection inside an explicit transaction.

    Commits on clean exit, rolls back on exception.
    """
    with get_pool().connection() as conn:
        with conn.transaction():
            yield conn
