"""Postgres access for the worker (psycopg 3, pooled — PERF-021).

Mirrors the API's ``app/core/db.py`` pattern: one process-wide
``ConnectionPool`` (small: the worker runs a handful of batch actors),
connections checked out per actor invocation instead of a fresh
``psycopg.connect`` per run. The pool is keyed by DSN so a settings
change (tests pointing at a throwaway pgserver) swaps pools instead of
silently reusing a stale one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from worker.settings import get_worker_settings

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5

_pool: ConnectionPool | None = None
_pool_dsn: str | None = None


def get_pool() -> ConnectionPool:
    """Return the process-wide pool for the configured DATABASE_URL."""
    global _pool, _pool_dsn
    settings = get_worker_settings()
    dsn = settings.DATABASE_URL
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured.")
    if _pool is None or _pool_dsn != dsn:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:  # a stale pool must not block the swap
                pass
        _pool = ConnectionPool(
            dsn,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
        _pool_dsn = dsn
    return _pool


@contextmanager
def get_connection() -> Iterator[Any]:
    """Check out a pooled connection (dict rows) for one actor run."""
    with get_pool().connection() as conn:
        yield conn


def reset_pool() -> None:
    """Drop the cached pool (tests, config reloads)."""
    global _pool, _pool_dsn
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
    _pool = None
    _pool_dsn = None
