"""Matter detail truthfulness: ``household_name`` + ``jurisdiction_code``.

The matter DETAIL path loads the raw matters row (``SELECT *`` via
``authz.get_matter_scope`` — no joins), while the LIST path computes
``household_name`` (households join) and ``jurisdiction_code``
(jurisdictions join). The detail response must agree with the list for
the same matter, or clients see a blank household name and a missing
jurisdiction code on first load (the web intake header showed
"Tati Family ·" with no US-VA).

Pinned here:
- API level: detail == list for both matter origins (firm household
  and self-serve).
- Serializer level: ``matter_out`` derives ``jurisdiction_code`` from
  the row's own ``jurisdiction_snapshot`` when no joined value is
  present, a joined value always wins, and legacy snapshot shapes
  (absent / keyless / non-dict / non-string) degrade to None — never
  an exception.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domains.matters.service import matter_out

# Fixtures + builders shared with the self-serve provisioning suite
# (pattern: tests/test_files_download_token_hygiene.py).
from tests.test_self_serve_provisioning import (  # noqa: F401
    CLIENT,
    FIRMLESS,
    api_client,
    auth,
    create_matter_api,
    db,
    pg_uri,
    setup_default_firm,
    setup_household_world,
)


# ---------------------------------------------------------------------------
# API level — detail must equal list for the same matter
# ---------------------------------------------------------------------------


def _list_item(api_client, sub: str, matter_id: str) -> dict:
    response = api_client.get("/v1/matters", headers=auth(sub))
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    return next(item for item in items if item["id"] == matter_id)


def _detail_matter(api_client, sub: str, matter_id: str) -> dict:
    response = api_client.get(f"/v1/matters/{matter_id}", headers=auth(sub))
    assert response.status_code == 200, response.text
    return response.json()["matter"]


def test_detail_matches_list_for_household_matter(db, api_client):
    _, household_id = setup_household_world(db)
    created = create_matter_api(
        api_client,
        CLIENT,
        {
            "household_id": household_id,
            "jurisdiction_code": "US-VA",
            "title": "Sharma Estate Plan",
        },
    )
    assert created.status_code == 201, created.text
    matter_id = created.json()["id"]

    listed = _list_item(api_client, CLIENT, matter_id)
    assert listed["household_name"] == "Sharma Household"
    assert listed["jurisdiction_code"] == "US-VA"

    detail = _detail_matter(api_client, CLIENT, matter_id)
    assert detail["household_name"] == listed["household_name"]
    assert detail["jurisdiction_code"] == listed["jurisdiction_code"]
    # The embedded household summary must carry the real name too, not
    # the generic "Household" fallback.
    assert detail["household"]["name"] == "Sharma Household"


def test_detail_matches_list_for_self_serve_matter(db, api_client):
    setup_default_firm(db)
    created = create_matter_api(
        api_client,
        FIRMLESS,
        {
            "jurisdiction_code": "US-VA",
            "household_name": "Tati Family",
            "title": "Tati Family Estate Plan",
        },
    )
    assert created.status_code == 201, created.text
    matter_id = created.json()["id"]

    listed = _list_item(api_client, FIRMLESS, matter_id)
    assert listed["household_name"] == "Tati Family"
    assert listed["jurisdiction_code"] == "US-VA"

    detail = _detail_matter(api_client, FIRMLESS, matter_id)
    assert detail["household_name"] == listed["household_name"]
    assert detail["jurisdiction_code"] == listed["jurisdiction_code"]
    assert detail["household"]["name"] == "Tati Family"


# ---------------------------------------------------------------------------
# Serializer level — jurisdiction derivation + graceful degradation
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    row = {
        "id": uuid4(),
        "firm_id": uuid4(),
        "household_id": uuid4(),
        "primary_jurisdiction_id": uuid4(),
        "jurisdiction_pack_id": uuid4(),
        "plan_type": "will",
        "status": "INTAKE_IN_PROGRESS",
        "status_version": 1,
        "jurisdiction_confirmation_status": "confirmed",
        "title": "Plan",
        "jurisdiction_snapshot": {"pack_id": "us-va", "jurisdiction_code": "US-VA"},
        "completeness_pct": 10,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def test_matter_out_derives_jurisdiction_code_from_snapshot():
    out = matter_out(_row())
    assert out.jurisdiction_code == "US-VA"


def test_matter_out_joined_jurisdiction_code_wins_over_snapshot():
    out = matter_out(_row(jurisdiction_code="US-CA"))
    assert out.jurisdiction_code == "US-CA"


def test_matter_out_missing_snapshot_stays_none():
    out = matter_out(_row(jurisdiction_snapshot=None))
    assert out.jurisdiction_code is None


def test_matter_out_snapshot_without_code_stays_none():
    out = matter_out(_row(jurisdiction_snapshot={"pack_id": "us-va"}))
    assert out.jurisdiction_code is None


def test_matter_out_non_dict_snapshot_never_raises():
    out = matter_out(_row(jurisdiction_snapshot="US-VA"))
    assert out.jurisdiction_code is None


def test_matter_out_non_string_snapshot_code_stays_none():
    out = matter_out(_row(jurisdiction_snapshot={"jurisdiction_code": 42}))
    assert out.jurisdiction_code is None


def test_matter_out_empty_joined_code_falls_back_to_snapshot():
    out = matter_out(_row(jurisdiction_code=""))
    assert out.jurisdiction_code == "US-VA"


def test_matter_out_padded_snapshot_code_is_stripped():
    out = matter_out(_row(jurisdiction_snapshot={"jurisdiction_code": " US-VA "}))
    assert out.jurisdiction_code == "US-VA"


def test_matter_out_non_dict_snapshot_serializes_as_none():
    out = matter_out(_row(jurisdiction_snapshot="US-VA"))
    assert out.jurisdiction_snapshot is None
