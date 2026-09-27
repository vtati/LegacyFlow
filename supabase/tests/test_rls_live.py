"""RLS allow/deny tests against the LIVE Supabase project (via PostgREST).

This is the live-project counterpart of ``test_rls.py`` (which targets a
throwaway local database). It exercises the real RLS policies end to end:
PostgREST authenticates each request with a synthetic user's JWT, so
``auth.uid()`` inside the policies resolves exactly as it does in production.

Required environment (CI secrets or a local shell):
  SUPABASE_URL, SUPABASE_ANON_KEY,
  LIVE_TEST_USER_A_EMAIL, LIVE_TEST_USER_A_PASSWORD,
  LIVE_TEST_USER_B_EMAIL, LIVE_TEST_USER_B_PASSWORD

One-time provisioning by the project owner (dashboard):
  1. Apply ``supabase/migrations/*.sql`` via the SQL editor.
  2. Authentication > Users: create the two users above (auto-confirm, set
     the passwords that become the CI secrets).
  3. Run ``supabase/seeds/02_live_rls_fixtures.sql`` via the SQL editor.

The suite SKIPS gracefully when any requirement is missing (no credentials,
sign-in fails, or the tables are absent), so CI stays green until
provisioning is complete. Once provisioned, every test here must pass.

All fixture data is synthetic. Nothing sensitive is printed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# Fixed fixture UUIDs — must match supabase/seeds/02_live_rls_fixtures.sql.
FIRM_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FIRM_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
MATTER_A = "60000000-0000-0000-0000-000000000000"
MATTER_B = "70000000-0000-0000-0000-000000000000"
VA_JURISDICTION = "11111111-1111-1111-1111-111111111111"
VA_PACK = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def live() -> dict[str, str]:
    """Live-project configuration from the environment (or skip)."""
    required = (
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "LIVE_TEST_USER_A_EMAIL",
        "LIVE_TEST_USER_A_PASSWORD",
        "LIVE_TEST_USER_B_EMAIL",
        "LIVE_TEST_USER_B_PASSWORD",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live RLS tests need env: {', '.join(missing)}")
    return {name: os.environ[name] for name in required}


def _signin(base_url: str, anon_key: str, email: str, password: str) -> dict[str, Any] | None:
    """Password-grant sign-in; returns the session dict or None."""
    import httpx

    try:
        response = httpx.post(
            f"{base_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=30.0,
        )
    except Exception:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


@pytest.fixture(scope="module")
def sessions(live: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Access tokens + user ids for the two synthetic users (or skip)."""
    base_url = live["SUPABASE_URL"].rstrip("/")
    anon_key = live["SUPABASE_ANON_KEY"]
    out: dict[str, dict[str, Any]] = {}
    for key, email_var, password_var in (
        ("a", "LIVE_TEST_USER_A_EMAIL", "LIVE_TEST_USER_A_PASSWORD"),
        ("b", "LIVE_TEST_USER_B_EMAIL", "LIVE_TEST_USER_B_PASSWORD"),
    ):
        session = _signin(base_url, anon_key, live[email_var], live[password_var])
        if not session or not session.get("access_token") or not session.get("user", {}).get("id"):
            pytest.skip(
                "live RLS tests need provisioned users: create them under "
                "Authentication > Users and set the LIVE_TEST_USER_* secrets"
            )
        out[key] = {"token": session["access_token"], "user_id": session["user"]["id"]}
    return out


def _rest(
    live: dict[str, str],
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """PostgREST request; ``token=None`` means unauthenticated (anon key only)."""
    import httpx

    headers = {"apikey": live["SUPABASE_ANON_KEY"]}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.request(
        method,
        f"{live['SUPABASE_URL'].rstrip('/')}/rest/v1/{path}",
        headers=headers,
        json=json_body,
        timeout=30.0,
    )
    return response


@pytest.fixture(scope="module")
def ready(live: dict[str, str], sessions: dict[str, dict[str, Any]]) -> Iterator[None]:
    """Skip unless the migrations are applied (matters table exists)."""
    response = _rest(live, "GET", "matters?select=id&limit=1")
    if response.status_code == 404:
        pytest.skip("live RLS tests need the migrations applied (public.matters is absent)")
    assert response.status_code == 200, f"unexpected matters probe: {response.status_code}"
    yield


def _ids(rows: Any) -> set[str]:
    assert isinstance(rows, list)
    return {str(r["id"]) for r in rows}


# ---------------------------------------------------------------------------
# Unauthenticated (anon key, no JWT): auth.uid() is null -> sees nothing.
# ---------------------------------------------------------------------------


def test_unauthenticated_sees_no_matters(live: dict[str, str], ready: None) -> None:
    response = _rest(live, "GET", "matters?select=id")
    assert response.status_code == 200
    assert response.json() == []


def test_unauthenticated_sees_no_reference_data(live: dict[str, str], ready: None) -> None:
    response = _rest(live, "GET", "jurisdictions?select=id")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Cross-firm isolation: each user sees exactly their own firm's data.
# ---------------------------------------------------------------------------


def test_user_a_sees_only_own_firm(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    response = _rest(live, "GET", "firms?select=id", token=sessions["a"]["token"])
    assert response.status_code == 200
    assert _ids(response.json()) == {FIRM_A}


def test_user_b_sees_only_own_firm(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    response = _rest(live, "GET", "firms?select=id", token=sessions["b"]["token"])
    assert response.status_code == 200
    assert _ids(response.json()) == {FIRM_B}


def test_cross_firm_matter_isolation(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    token_a = sessions["a"]["token"]
    token_b = sessions["b"]["token"]

    rows_a = _rest(live, "GET", "matters?select=id,firm_id", token=token_a).json()
    assert _ids(rows_a) == {MATTER_A}
    assert {r["firm_id"] for r in rows_a} == {FIRM_A}

    rows_b = _rest(live, "GET", "matters?select=id,firm_id", token=token_b).json()
    assert _ids(rows_b) == {MATTER_B}
    assert {r["firm_id"] for r in rows_b} == {FIRM_B}

    # Explicit cross-firm filters return nothing (RLS, not just absence).
    cross = _rest(live, "GET", f"matters?select=id&firm_id=eq.{FIRM_B}", token=token_a)
    assert cross.status_code == 200
    assert cross.json() == []
    cross = _rest(live, "GET", f"matters?select=id&firm_id=eq.{FIRM_A}", token=token_b)
    assert cross.status_code == 200
    assert cross.json() == []


def test_roster_isolation(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    rows = _rest(
        live, "GET", "firm_memberships?select=firm_id,user_id", token=sessions["a"]["token"]
    ).json()
    assert rows, "expected user A to see their own firm's roster"
    assert {r["firm_id"] for r in rows} == {FIRM_A}
    assert sessions["b"]["user_id"] not in {r["user_id"] for r in rows}


def test_audit_events_isolated_per_firm(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    rows_a = _rest(live, "GET", "audit_events?select=firm_id", token=sessions["a"]["token"]).json()
    assert {r["firm_id"] for r in rows_a} == {FIRM_A}
    rows_b = _rest(live, "GET", "audit_events?select=firm_id", token=sessions["b"]["token"]).json()
    assert {r["firm_id"] for r in rows_b} == {FIRM_B}


# ---------------------------------------------------------------------------
# Reference data: readable by any authenticated user.
# ---------------------------------------------------------------------------


def test_authenticated_users_read_reference_data(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    for key in ("a", "b"):
        response = _rest(live, "GET", "jurisdictions?select=id", token=sessions[key]["token"])
        assert response.status_code == 200
        assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Write deny paths.
# ---------------------------------------------------------------------------


def test_firm_insert_denied_for_client_roles(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    response = _rest(
        live,
        "POST",
        "firms",
        token=sessions["a"]["token"],
        json_body={"legal_name": "Evil Corp", "display_name": "Evil", "status": "active"},
    )
    assert response.status_code != 201, "client roles must not create firms"


def test_cross_firm_matter_insert_denied(
    live: dict[str, str], sessions: dict[str, dict[str, Any]], ready: None
) -> None:
    response = _rest(
        live,
        "POST",
        "matters",
        token=sessions["a"]["token"],
        json_body={
            "firm_id": FIRM_B,
            "household_id": "50000000-0000-0000-0000-000000000000",
            "primary_jurisdiction_id": VA_JURISDICTION,
            "jurisdiction_pack_id": VA_PACK,
            "plan_type": "will",
            "status": "intake",
        },
    )
    assert response.status_code != 201, "user A must not insert matters into firm B"
