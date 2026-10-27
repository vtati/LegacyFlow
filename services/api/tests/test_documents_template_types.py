"""DocumentTemplateType widening (family draft generation plan, Task 1.1).

The DB CHECK (migration 20261006040000) allows 8 template types, but the
API Literal historically allowed only 4 — firm-copied rows typed
``advance_directive`` / ``guardianship`` would fail response validation in
``_template_out``. These unit tests pin the widened Literal: every
DB-allowed type must round-trip through ``_template_out``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domains.documents.service import _template_out

ALL_DB_TYPES = [
    "will",
    "trust",
    "poa",
    "advance_directive",
    "guardianship",
    "codicil",
    "trust_amendment",
    "instruction_letter",
]


def _row(template_type: str) -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "firm_id": uuid4(),
        "name": f"Synthetic {template_type}",
        "template_type": template_type,
        "pack_id": "us-va",
        "version": "1.0.0",
        "content": {"body": "Body for {{household_name}}"},
        "created_by_user_id": uuid4(),
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.parametrize("template_type", ALL_DB_TYPES)
def test_template_out_accepts_every_db_allowed_type(template_type: str) -> None:
    out = _template_out(_row(template_type))
    assert out.template_type == template_type
    assert out.content == "Body for {{household_name}}"
