"""Tests for API hardening: rate limiting, idempotency, response-time header,
JSON structured logging, Sentry optionality, and pack-loader caching.

All data is synthetic; no external services are touched.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi import Depends

from app.core.config import get_settings
from app.core.idempotency import clear_idempotency_store
from app.core.logging import (
    JSONFormatter,
    get_logger,
    reset_request_id,
    set_request_id,
)
from app.core.rate_limit import (
    RateTier,
    SlidingWindowRateLimiter,
    rate_limit_dependency,
    reset_rate_limiter,
    tier_for_path,
)

# Probe endpoint exercising the per-router dependency path. Registered lazily
# (see _ensure_probe_route) because importing app.main at module scope would
# construct the app before the test env fixture is active. The path is unique
# to this module so it cannot shadow real routes.


def _rate_limited_probe() -> dict[str, bool]:
    return {"ok": True}


_probe_registered = False


def _ensure_probe_route() -> None:
    global _probe_registered
    if _probe_registered:
        return
    from app.main import app

    app.get("/_test/rate-limited", dependencies=[Depends(rate_limit_dependency(RateTier.AUTH))])(
        _rate_limited_probe
    )
    _probe_registered = True


@pytest.fixture()
def fresh_limits(monkeypatch: pytest.MonkeyPatch):
    """Isolate rate-limiter state and force settings re-read."""
    reset_rate_limiter()
    clear_idempotency_store()
    # The base test fixture disables rate limiting; these tests opt back in.
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    yield monkeypatch
    reset_rate_limiter()
    clear_idempotency_store()
    get_settings.cache_clear()


@pytest.fixture()
def probe_client(client, fresh_limits):
    """TestClient with the per-router rate-limit probe route registered."""
    _ensure_probe_route()
    return client


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------


def test_tier_for_path_mapping() -> None:
    assert tier_for_path("/healthz") is None
    assert tier_for_path("/") is None
    assert tier_for_path("/docs") is None
    assert tier_for_path("/openapi.json") is None
    assert tier_for_path("/v1/invitations/accept") is RateTier.AUTH
    assert tier_for_path("/v1/firms/invitations") is RateTier.AUTH
    assert tier_for_path("/v1/firms/invitations/abc/revoke") is RateTier.AUTH
    assert tier_for_path("/v1/vault/access") is RateTier.AUTH
    assert tier_for_path("/v1/matters/123/vault-shares") is RateTier.AUTH
    assert tier_for_path("/v1/vault-shares/abc/revoke") is RateTier.AUTH
    assert tier_for_path("/v1/me") is RateTier.AUTH
    assert tier_for_path("/v1/files/abc/download-token") is RateTier.AUTH
    assert tier_for_path("/v1/matters/123/ai/ask") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/matters/123/files/intent") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/files/abc/complete") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/admin/packs/import") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/matters/123/intake/evaluate") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/packages/abc/generate-demo") is RateTier.UPLOAD_AI
    assert tier_for_path("/v1/matters") is RateTier.DEFAULT
    assert tier_for_path("/v1/matters/123/decisions") is RateTier.DEFAULT


def test_sliding_window_allows_then_blocks() -> None:
    limiter = SlidingWindowRateLimiter()
    assert limiter.check("bucket", "user-a", 2) == 0.0
    assert limiter.check("bucket", "user-a", 2) == 0.0
    assert limiter.check("bucket", "user-a", 2) > 0.0
    # A different identity gets its own bucket.
    assert limiter.check("bucket", "user-b", 2) == 0.0


# ---------------------------------------------------------------------------
# Rate limiting over HTTP
# ---------------------------------------------------------------------------


def test_rate_limiter_blocks_after_threshold(client, fresh_limits) -> None:
    fresh_limits.setenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "2")
    get_settings.cache_clear()

    first = client.get("/v1/matters")
    second = client.get("/v1/matters")
    third = client.get("/v1/matters")

    # Unauthenticated requests still count toward the bucket (brute-force
    # protection happens before auth).
    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1
    body = third.json()
    assert body["status"] == 429
    assert body["type"] == "urn:legacyflow:error:rate_limit_exceeded"
    assert body["title"] == "Rate limit exceeded"


def test_healthz_exempt_from_rate_limit(client, fresh_limits) -> None:
    fresh_limits.setenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "1")
    get_settings.cache_clear()
    for _ in range(5):
        response = client.get("/healthz")
        assert response.status_code == 200


def test_rate_limit_dependency_renders_429_with_retry_after(probe_client, fresh_limits) -> None:
    fresh_limits.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "1")
    get_settings.cache_clear()

    assert probe_client.get("/_test/rate-limited").status_code == 200
    limited = probe_client.get("/_test/rate-limited")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.json()["type"] == "urn:legacyflow:error:rate_limit_exceeded"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotency_replays_identical_request(client, fresh_limits) -> None:
    headers = {"Idempotency-Key": "hardening-key-1"}
    first = client.post("/v1/matters", json={"name": "synthetic"}, headers=headers)
    second = client.post("/v1/matters", json={"name": "synthetic"}, headers=headers)

    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.status_code == first.status_code
    assert second.json() == first.json()


def test_idempotency_rejects_conflicting_reuse(client, fresh_limits) -> None:
    headers = {"Idempotency-Key": "hardening-key-2"}
    client.post("/v1/matters", json={"name": "synthetic-a"}, headers=headers)
    conflict = client.post("/v1/matters", json={"name": "synthetic-b"}, headers=headers)

    assert conflict.status_code == 422
    body = conflict.json()
    assert body["type"] == "urn:legacyflow:error:idempotency_key_conflict"
    assert "Idempotent-Replayed" not in conflict.headers


def test_idempotency_key_absent_passes_through(client, fresh_limits) -> None:
    first = client.post("/v1/matters", json={"name": "no-key"})
    second = client.post("/v1/matters", json={"name": "no-key"})
    assert "Idempotent-Replayed" not in first.headers
    assert "Idempotent-Replayed" not in second.headers


def test_idempotency_ignores_safe_methods(client, fresh_limits) -> None:
    response = client.get("/v1/me", headers={"Idempotency-Key": "get-key"})
    assert "Idempotent-Replayed" not in response.headers


# ---------------------------------------------------------------------------
# Response headers / request ID
# ---------------------------------------------------------------------------


def test_response_time_and_request_id_headers(client) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "probe-123"})
    assert response.headers["X-Request-ID"] == "probe-123"
    assert response.headers["X-Response-Time"].endswith("ms")
    float(response.headers["X-Response-Time"][:-2])


# ---------------------------------------------------------------------------
# JSON logging
# ---------------------------------------------------------------------------


def test_json_formatter_emits_request_id() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger = get_logger("test-hardening")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        token = set_request_id("req-123")
        logger.info("hello")
        reset_request_id(token)
        logger.info("no context")
    finally:
        logger.removeHandler(handler)

    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["request_id"] == "req-123"
    assert first["service"] == "legacyflow-api"
    assert first["level"] == "INFO"
    assert first["logger"] == "test-hardening"
    assert first["message"] == "hello"
    assert "timestamp" in first
    assert second["request_id"] is None


# ---------------------------------------------------------------------------
# Sentry optionality
# ---------------------------------------------------------------------------


def test_sentry_init_is_optional(test_settings_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DSN without the SDK installed must not break app construction."""
    import sys

    from app.main import create_app

    assert "sentry_sdk" not in sys.modules
    monkeypatch.setenv("SENTRY_DSN", "https://example.invalid/1")
    get_settings.cache_clear()
    try:
        assert create_app() is not None
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Pack-loader caching
# ---------------------------------------------------------------------------


def test_pack_loader_caches_parsed_packs(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domains.legal_content.pack_loader as pack_loader

    pack_loader.clear_pack_cache()
    calls = 0
    original_read_yaml = pack_loader._read_yaml

    def counting_read_yaml(path):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original_read_yaml(path)

    monkeypatch.setattr(pack_loader, "_read_yaml", counting_read_yaml)

    pack_loader.get_pack("US-VA")
    assert calls > 0
    disk_reads = calls

    pack_loader.get_pack("US-VA")
    assert calls == disk_reads, "second get_pack must not touch disk"

    pack_loader.get_pack("US-VA", "0.1.0")
    assert calls > disk_reads, "explicit version is a distinct cache key"

    pack_loader.clear_pack_cache()
    pack_loader.get_pack("US-VA")
    assert calls > disk_reads, "clear_pack_cache must force a re-read"


def test_list_packs_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domains.legal_content.pack_loader as pack_loader

    pack_loader.clear_pack_cache()
    calls = 0
    original_read_yaml = pack_loader._read_yaml

    def counting_read_yaml(path):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original_read_yaml(path)

    monkeypatch.setattr(pack_loader, "_read_yaml", counting_read_yaml)

    first = pack_loader.list_packs()
    assert len(first) > 0
    disk_reads = calls
    second = pack_loader.list_packs()
    assert calls == disk_reads, "second list_packs must not touch disk"
    assert [m.pack_id for m in first] == [m.pack_id for m in second]
