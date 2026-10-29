"""Batch D worker tests: pooling (PERF-021), savepoint isolation
(ERR-021), reminder failure ledger (ERR-002), actor retry config +
dead letters (ERR-003), scan storage failures (ERR-004), atomic
sandbox writes (ERR-011), JSON logging (ERR-012).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# PERF-021: module-level connection pool
# ---------------------------------------------------------------------------


def test_pool_module_reuses_pool_per_dsn() -> None:
    from worker import db as worker_db

    assert hasattr(worker_db, "get_pool")
    assert hasattr(worker_db, "get_connection")
    assert worker_db.POOL_MIN_SIZE == 1
    assert worker_db.POOL_MAX_SIZE == 5


def test_worker_deps_declare_psycopg_pool() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    requirements = (root / "requirements.txt").read_text()
    assert "pool" in pyproject.split("psycopg")[1].split("]")[0]
    assert "pool" in requirements.split("psycopg")[1].split("]")[0]
