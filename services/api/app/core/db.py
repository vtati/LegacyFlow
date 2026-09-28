"""Postgres access helpers (psycopg 3).

Conventions (§6.4):
  * Repositories own SQL; services never touch connections directly.
  * ``get_connection()`` opens a short-lived connection with the
    ``dict_row`` factory. Longer-lived pooling is a later-slice concern;
    the seam (this module) is where it will land.
  * ``transaction()`` yields a connection inside an explicit transaction
    block for multi-statement service operations.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.core.errors import ConfigurationError


def database_url_or_raise() -> str:
    """Return the configured DATABASE_URL or raise ConfigurationError."""
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise ConfigurationError("DATABASE_URL is not configured.")
    return settings.DATABASE_URL


def get_connection() -> Any:
    """Open a new psycopg connection with dict rows.

    Caller owns the connection (use as a context manager).

    ``prepare_threshold=None`` disables server-side prepared statements.
    The app connects through a transaction-pooling proxy (Supabase pooler);
    psycopg's auto-prepared statements (``_pg3_N``) collide across pooled
    server connections and raise ``DuplicatePreparedStatement`` once any
    query repeats ~5 times on one connection (e.g. feature-flag listing).
    Disabling prepares trades a little plan-caching for correctness.
    """
    return psycopg.connect(database_url_or_raise(), row_factory=dict_row, prepare_threshold=None)


@contextmanager
def transaction() -> Iterator[Any]:
    """Yield a connection inside an explicit transaction.

    Commits on clean exit, rolls back on exception.
    """
    with get_connection() as conn:
        with conn.transaction():
            yield conn
