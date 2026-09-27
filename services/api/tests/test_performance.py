"""E6.6 — Performance/load harness (PRD §4.2).

PRD §4.2 targets under test::

    P95 interactive API reads under 500 ms where cacheable;
    form saves under 750 ms; jobs asynchronous.

Method (MVP-appropriate and CI-hermetic):

* A throwaway Postgres (pgserver, or ``DATABASE_URL`` when set) with all
  six migrations applied and a small synthetic seed: one firm, one
  firm_admin, one attorney, one primary-client household member, one
  household, one matter snapshotted to the on-disk US-VA 0.1.0 pack.
* Requests go through FastAPI's ``TestClient`` in-process: no network, no
  TLS, no Supabase. Reported latencies are app + query cost — a lower
  bound for true end-to-end latency, and exactly the signal needed to
  catch request-path regressions (N+1 queries, missing indexes, per-request
  pack reloads).
* A ``ThreadPoolExecutor`` drives concurrent workers per scenario after a
  warmup; P50/P95/P99 are computed from the measured samples and asserted
  against the PRD targets. Run with ``pytest -s`` to see the table.
* "Jobs asynchronous" is covered by ``POST /v1/admin/reminders/dispatch``:
  with the broker enqueue mocked, the endpoint must return 202 having done
  no inline work (the heavy send runs in the worker process).

What this does NOT claim: production P95 under real network load. That
needs a staging deployment; this harness is a regression guardrail.

All fixture data is synthetic. Nothing sensitive is printed.
"""

from __future__ import annotations

import math
import os
import pathlib
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    TEST_AUDIENCE,
    TEST_EMAIL,
    TEST_ISSUER,
    TEST_SECRET,
    mint_token,
)

# ---------------------------------------------------------------------------
# PRD §4.2 targets and harness shape
# ---------------------------------------------------------------------------

READ_P95_MS = 500.0
WRITE_P95_MS = 750.0

WORKERS = 16
REQUESTS_PER_WORKER = 25
WARMUP_REQUESTS = 10

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
assert MIGRATIONS_DIR.is_dir(), f"migrations dir missing: {MIGRATIONS_DIR}"

# Synthetic identities (fixed UUIDs; never real data)
ADMIN_SUB = "aaaaaaaa-1111-2222-3333-444444444444"  # firm_admin
ATTORNEY_SUB = "aaaaaaaa-5555-6666-7777-888888888888"  # attorney
CLIENT_SUB = "aaaaaaaa-9999-0000-1111-222222222222"  # primary_client
FIRM_ID = uuid.uuid4()
HOUSEHOLD_ID = uuid.uuid4()
MATTER_ID = uuid.uuid4()
JURISDICTION_ID = uuid.uuid4()
PACK_ID = uuid.uuid4()

VA_SNAPSHOT = {
    "pack_id": "us-va",
    "jurisdiction_code": "US-VA",
    "semantic_version": "0.1.0",
}


def _auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(sub=sub)}"}


# ---------------------------------------------------------------------------
# Database fixtures (pgserver or DATABASE_URL, all migrations, synthetic seed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    """Connection string: DATABASE_URL (CI) or a throwaway pgserver instance."""
    url = os.environ.get("DATABASE_URL")
    if url:
        yield url
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("no database available (set DATABASE_URL or install pgserver)")
    datadir = tempfile.mkdtemp(prefix="lf-perf-")
    server = pgserver.get_server(datadir)
    try:
        yield server.get_uri()
    finally:
        server.cleanup()
        # pgserver.cleanup() stops the server but keeps the data directory;
        # remove it so repeated runs cannot fill /tmp.
        shutil.rmtree(datadir, ignore_errors=True)


@pytest.fixture(scope="module")
def seeded_db(db_url: str) -> str:
    """Fresh database: auth shim, all migrations, minimal synthetic seed."""
    import psycopg

    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("drop schema if exists public cascade")
            cur.execute("create schema public")
            cur.execute("drop schema if exists auth cascade")
            cur.execute("create schema auth")
            # profiles.id references auth.users(id) per §7.2 DDL; RLS policies
            # call auth.uid(), shimmed here like the supabase RLS suite does.
            cur.execute("create table auth.users (id uuid primary key)")
            cur.execute(
                """
                create or replace function auth.uid() returns uuid
                language sql stable
                as $$ select nullif(current_setting('app.user_id', true), '')::uuid $$;
                """
            )
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(migration.read_text())
            _seed(cur)
    return db_url


def _seed(cur: Any) -> None:
    """Insert the synthetic firm / users / household / matter tree."""
    for sub, role in (
        (ADMIN_SUB, "firm_admin"),
        (ATTORNEY_SUB, "attorney"),
    ):
        cur.execute("insert into auth.users (id) values (%s)", (sub,))
        cur.execute(
            "insert into profiles (id, display_name, email) values (%s, %s, %s)",
            (sub, f"Synthetic Perf {role}", f"perf-{role}@example.com"),
        )
    cur.execute("insert into auth.users (id) values (%s)", (CLIENT_SUB,))
    cur.execute(
        "insert into profiles (id, display_name, email) values (%s, %s, %s)",
        (CLIENT_SUB, "Synthetic Perf Client", TEST_EMAIL),
    )
    cur.execute(
        "insert into firms (id, legal_name, display_name, status) values (%s, %s, %s, 'active')",
        (str(FIRM_ID), "Synthetic Perf Firm LLC", "Synthetic Perf Firm"),
    )
    for sub, role in ((ADMIN_SUB, "firm_admin"), (ATTORNEY_SUB, "attorney")):
        cur.execute(
            "insert into firm_memberships (firm_id, user_id, role, status)"
            " values (%s, %s, %s, 'active')",
            (str(FIRM_ID), sub, role),
        )
    cur.execute(
        "insert into households (id, firm_id, display_name, created_by_user_id)"
        " values (%s, %s, %s, %s)",
        (str(HOUSEHOLD_ID), str(FIRM_ID), "Synthetic Perf Household", ADMIN_SUB),
    )
    cur.execute(
        "insert into household_members"
        " (household_id, user_id, relationship_role, access_status)"
        " values (%s, %s, 'primary_client', 'active')",
        (str(HOUSEHOLD_ID), CLIENT_SUB),
    )
    cur.execute(
        "insert into jurisdictions"
        " (id, country_code, subdivision_code, display_name,"
        "  jurisdiction_type, is_enabled)"
        " values (%s, 'US', 'VA', 'Virginia', 'us_state', true)",
        (str(JURISDICTION_ID),),
    )
    cur.execute(
        "insert into jurisdiction_packs"
        " (id, jurisdiction_id, version, status, effective_from,"
        "  manifest, source_bundle_hash)"
        " values (%s, %s, '0.1.0', 'internal_testing', '2026-09-26',"
        "         '{}'::jsonb, 'synthetic')",
        (str(PACK_ID), str(JURISDICTION_ID)),
    )
    import json as _json

    cur.execute(
        "insert into matters"
        " (id, firm_id, household_id, primary_jurisdiction_id,"
        "  jurisdiction_pack_id, plan_type, status, jurisdiction_snapshot)"
        " values (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s::jsonb)",
        (
            str(MATTER_ID),
            str(FIRM_ID),
            str(HOUSEHOLD_ID),
            str(JURISDICTION_ID),
            str(PACK_ID),
            _json.dumps(VA_SNAPSHOT),
        ),
    )


@pytest.fixture()
def perf_env(seeded_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Wire the app to the test JWT override and the seeded database."""
    monkeypatch.setenv("TEST_JWT_ISSUER", TEST_ISSUER)
    monkeypatch.setenv("TEST_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_AUDIENCE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("DATABASE_URL", seeded_db)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    # Rate limiting is covered by its own dedicated tests; leave it off here.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_client() -> TestClient:
    """A fresh TestClient (one per load-generator thread)."""
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Load driver
# ---------------------------------------------------------------------------


def _percentile_ms(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, math.ceil(pct / 100.0 * len(ordered)) - 1))
    return ordered[idx] * 1000.0


def run_load(
    *,
    label: str,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    body_factory: Callable[[int, int], dict[str, Any] | None] | None = None,
    expect_status: set[int],
    workers: int = WORKERS,
    per_worker: int = REQUESTS_PER_WORKER,
) -> dict[str, float]:
    """Warm up, then drive concurrent load and return latency percentiles (ms).

    Raises on any 5xx or any status outside ``expect_status`` — a perf run
    must measure the happy path, not an error path.
    """
    warmup = make_client()
    for _ in range(WARMUP_REQUESTS):
        body = body_factory(0, 0) if body_factory else json_body
        resp = warmup.request(method, path, headers=headers, json=body)
        assert resp.status_code in expect_status, (
            f"{label}: warmup got {resp.status_code}: {resp.text[:200]}"
        )

    samples: list[float] = []
    lock = threading.Lock()

    def _worker(worker_id: int) -> None:
        client = make_client()
        local: list[float] = []
        for i in range(per_worker):
            body = body_factory(worker_id, i) if body_factory else json_body
            start = time.perf_counter()
            resp = client.request(method, path, headers=headers, json=body)
            elapsed = time.perf_counter() - start
            assert resp.status_code in expect_status, (
                f"{label}: worker {worker_id} got {resp.status_code}: {resp.text[:200]}"
            )
            local.append(elapsed)
        with lock:
            samples.extend(local)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(_worker, range(workers)))

    n = len(samples)
    stats = {
        "n": float(n),
        "p50": _percentile_ms(samples, 50),
        "p95": _percentile_ms(samples, 95),
        "p99": _percentile_ms(samples, 99),
        "max": max(samples) * 1000.0,
    }
    return stats


def _report(label: str, stats: dict[str, float], target_p95_ms: float) -> None:
    verdict = "PASS" if stats["p95"] < target_p95_ms else "FAIL"
    print(
        f"[E6.6] {label} — n={int(stats['n'])} workers={WORKERS} | "
        f"p50={stats['p50']:.1f}ms p95={stats['p95']:.1f}ms "
        f"p99={stats['p99']:.1f}ms max={stats['max']:.1f}ms | "
        f"target p95<{target_p95_ms:.0f}ms -> {verdict}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Reads — PRD §4.2: P95 under 500 ms
# ---------------------------------------------------------------------------


def test_perf_read_healthz(perf_env: None) -> None:
    stats = run_load(
        label="GET /healthz",
        method="GET",
        path="/healthz",
        expect_status={200},
    )
    _report("GET /healthz", stats, READ_P95_MS)
    assert stats["p95"] < READ_P95_MS, f"P95 {stats['p95']:.1f}ms >= {READ_P95_MS:.0f}ms"


def test_perf_read_me(perf_env: None) -> None:
    stats = run_load(
        label="GET /v1/me (authenticated profile read)",
        method="GET",
        path="/v1/me",
        headers=_auth(CLIENT_SUB),
        expect_status={200},
    )
    _report("GET /v1/me", stats, READ_P95_MS)
    assert stats["p95"] < READ_P95_MS, f"P95 {stats['p95']:.1f}ms >= {READ_P95_MS:.0f}ms"


def test_perf_read_matters_list(perf_env: None) -> None:
    stats = run_load(
        label="GET /v1/matters (firm dashboard list)",
        method="GET",
        path="/v1/matters",
        headers=_auth(ATTORNEY_SUB),
        expect_status={200},
    )
    _report("GET /v1/matters", stats, READ_P95_MS)
    assert stats["p95"] < READ_P95_MS, f"P95 {stats['p95']:.1f}ms >= {READ_P95_MS:.0f}ms"


def test_perf_read_matter_detail(perf_env: None) -> None:
    stats = run_load(
        label="GET /v1/matters/{id} (matter detail)",
        method="GET",
        path=f"/v1/matters/{MATTER_ID}",
        headers=_auth(ATTORNEY_SUB),
        expect_status={200},
    )
    _report("GET /v1/matters/{id}", stats, READ_P95_MS)
    assert stats["p95"] < READ_P95_MS, f"P95 {stats['p95']:.1f}ms >= {READ_P95_MS:.0f}ms"


# ---------------------------------------------------------------------------
# Writes / form saves — PRD §4.2: P95 under 750 ms
# ---------------------------------------------------------------------------


def _answers_body(worker_id: int, i: int) -> dict[str, Any]:
    # Both keys exist in the on-disk US-VA 0.1.0 pack; the boolean rotates
    # so each save is a genuine write, not a no-op.
    return {"answers": {"marital_status": "married", "has_minor_children": i % 2 == 0}}


def test_perf_write_intake_answers(perf_env: None) -> None:
    stats = run_load(
        label="PUT /v1/matters/{id}/intake/answers (intake autosave)",
        method="PUT",
        path=f"/v1/matters/{MATTER_ID}/intake/answers",
        headers=_auth(CLIENT_SUB),
        body_factory=_answers_body,
        expect_status={200},
    )
    _report("PUT intake answers", stats, WRITE_P95_MS)
    assert stats["p95"] < WRITE_P95_MS, f"P95 {stats['p95']:.1f}ms >= {WRITE_P95_MS:.0f}ms"


def _asset_body(worker_id: int, i: int) -> dict[str, Any]:
    return {
        "asset_type": "bank_account",
        "label": f"Perf Asset w{worker_id}-{i}",
        "estimated_value_cents": 100_00,
        "ownership": "other",
    }


def test_perf_write_create_asset(perf_env: None) -> None:
    stats = run_load(
        label="POST /v1/households/{id}/assets (asset form save)",
        method="POST",
        path=f"/v1/households/{HOUSEHOLD_ID}/assets",
        headers=_auth(ATTORNEY_SUB),
        body_factory=_asset_body,
        expect_status={201},
    )
    _report("POST asset", stats, WRITE_P95_MS)
    assert stats["p95"] < WRITE_P95_MS, f"P95 {stats['p95']:.1f}ms >= {WRITE_P95_MS:.0f}ms"


# ---------------------------------------------------------------------------
# Jobs asynchronous — the HTTP layer enqueues; the worker does the work
# ---------------------------------------------------------------------------


def test_perf_job_dispatch_is_async(perf_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /v1/admin/reminders/dispatch must return 202 fast.

    The broker enqueue is mocked: this measures the HTTP layer only. The
    real send runs in the worker process (covered by worker tests), so a
    fast 202 here proves the request path never does the heavy work inline.
    """
    monkeypatch.setenv("REDIS_URL", "redis://synthetic:6379/0")

    def fake_enqueue(redis_url: str) -> None:
        return None

    monkeypatch.setattr("app.domains.privacy.service.enqueue_reminder_dispatch", fake_enqueue)
    stats = run_load(
        label="POST /v1/admin/reminders/dispatch (job enqueue, mocked broker)",
        method="POST",
        path="/v1/admin/reminders/dispatch",
        headers=_auth(ADMIN_SUB),
        expect_status={202},
    )
    _report("POST dispatch", stats, WRITE_P95_MS)
    assert stats["p95"] < WRITE_P95_MS, f"P95 {stats['p95']:.1f}ms >= {WRITE_P95_MS:.0f}ms"
