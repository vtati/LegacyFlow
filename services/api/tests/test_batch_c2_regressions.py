"""Batch C2 (deep-review 2026-10-29) regression tests.

PERF-003: listing funding tasks must never download evidence bytes —
the sha256 digest is stored on file_uploads at upload completion and
read back; list lookups are batched.
FUNC-006: next_version_number takes a transaction advisory lock on the
package before the MAX(version) read.
PERF-004: the review-packet snapshot resolves nomination person names
from the already-fetched household persons list (one fallback lookup
only for a person outside that list).
PERF-009: documents get_file_upload selects an explicit column list —
never the ~20KB extracted_text blob.
FUNC-013/014: funding-task generation is conflict-guarded (no duplicate
tasks even when the existence check reads stale) and an ``other`` asset
yields exactly one general beneficiary-alignment task, never silence.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Fake connection/cursor recording executed SQL
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.statements.append((sql, params))

    def fetchone(self):
        return self._conn.next_row

    def fetchall(self):
        return self._conn.next_rows


class _FakeConn:
    def __init__(self, next_row=None, next_rows=None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.next_row = next_row
        self.next_rows = next_rows or []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)


# ---------------------------------------------------------------------------
# PERF-003 — evidence output reads the stored digest, never downloads
# ---------------------------------------------------------------------------


def test_evidence_out_uses_stored_digest_and_never_downloads(monkeypatch) -> None:
    from app.domains.documents import service
    from app.domains.files import service as files_service

    def _boom(stored_path: str) -> bytes:
        raise AssertionError("evidence listing must not download file bytes")

    monkeypatch.setattr(files_service, "download_bytes", _boom)

    upload = {
        "original_filename": "deed-copy.pdf",
        "size_bytes": 1234,
        "sha256": "a" * 64,
    }
    out = service._evidence_out(upload)
    assert out is not None
    assert out.file_name == "deed-copy.pdf"
    assert out.size_bytes == 1234
    assert out.sha256 == "a" * 64

    # Legacy rows (NULL digest) surface no digest rather than downloading.
    legacy = {"original_filename": "old.pdf", "size_bytes": 10, "sha256": None}
    out = service._evidence_out(legacy)
    assert out is not None
    assert out.sha256 is None

    assert service._evidence_out(None) is None


def test_list_funding_tasks_batches_upload_lookups(monkeypatch) -> None:
    from app.domains.documents import repository as docs_repo
    from app.domains.documents import service
    from app.domains.files import service as files_service

    matter_id = uuid4()
    ev1, ev2 = uuid4(), uuid4()
    rows = [
        {
            "id": uuid4(), "firm_id": uuid4(), "matter_id": matter_id,
            "asset_id": None, "task_type": "bank-tod-podb", "title": "t1",
            "status": "pending", "evidence_file_id": ev1, "due_date": None,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": uuid4(), "firm_id": uuid4(), "matter_id": matter_id,
            "asset_id": None, "task_type": "vehicle-title", "title": "t2",
            "status": "pending", "evidence_file_id": ev2, "due_date": None,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": uuid4(), "firm_id": uuid4(), "matter_id": matter_id,
            "asset_id": None, "task_type": "529-beneficiary", "title": "t3",
            "status": "pending", "evidence_file_id": None, "due_date": None,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        },
    ]
    uploads = {
        ev1: {"original_filename": "a.pdf", "size_bytes": 5, "sha256": "b" * 64},
        ev2: {"original_filename": "c.pdf", "size_bytes": 7, "sha256": None},
    }

    batch_calls: list[list] = []

    def _batch(conn, ids):
        batch_calls.append(list(ids))
        return {i: uploads[i] for i in ids if i in uploads}

    monkeypatch.setattr(docs_repo, "get_file_uploads_by_ids", _batch, raising=False)
    monkeypatch.setattr(
        docs_repo,
        "get_file_upload",
        lambda conn, uid: (_ for _ in ()).throw(
            AssertionError("per-task get_file_upload must not be used by the list path")
        ),
    )
    monkeypatch.setattr(docs_repo, "list_funding_tasks_for_matter", lambda conn, mid: rows)
    monkeypatch.setattr(service, "_funding_playbooks_by_id", lambda matter: {})

    class _Scope:
        matter = {"id": str(matter_id)}

    monkeypatch.setattr(service.authz, "get_matter_scope", lambda conn, mid, aid: _Scope())
    monkeypatch.setattr(service, "_actor_id", lambda sub: uuid4())
    monkeypatch.setattr(
        files_service,
        "download_bytes",
        lambda path: (_ for _ in ()).throw(AssertionError("no downloads on list")),
    )

    out = service.list_funding_tasks(object(), actor_sub=str(uuid4()), matter_id=matter_id)
    assert len(batch_calls) == 1, "uploads must be fetched in ONE batched query"
    assert set(batch_calls[0]) == {ev1, ev2}
    assert out[0].evidence is not None and out[0].evidence.sha256 == "b" * 64
    assert out[1].evidence is not None and out[1].evidence.sha256 is None
    assert out[2].evidence is None


def test_complete_upload_stores_sha256_digest(monkeypatch) -> None:
    from app.domains.files import repository as files_repo
    from app.domains.files import service

    upload_id = uuid4()
    content = b"%PDF-1.4 synthetic vault file"
    row = {
        "id": upload_id, "firm_id": uuid4(), "household_id": uuid4(),
        "matter_id": uuid4(), "uploader_user_id": uuid4(),
        "original_filename": "deed.pdf", "stored_path": "client-uploads/x.pdf",
        "mime_type": "application/pdf", "size_bytes": len(content),
        "scan_status": "pending", "scan_detail": None,
    }
    scope = service.UploadScope(upload=dict(row), via="firm", firm_role="staff", household_role=None)
    monkeypatch.setattr(service, "get_upload_scope", lambda conn, uid, aid: scope)

    class _Backend:
        def upload(self, bucket, key, data, mime):
            assert data == content

    monkeypatch.setattr(service, "_storage_backend", lambda: _Backend())
    monkeypatch.setattr(
        service.file_extraction, "extract_text_with_status", lambda data, mime: ("text", "done")
    )
    captured: dict = {}

    def _update_extraction(conn, uid, **kwargs):
        captured.update(kwargs)
        return {
            **row,
            "extracted_text": "text",
            "extraction_status": "done",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(files_repo, "update_extraction", _update_extraction)
    monkeypatch.setattr(service, "record_audit_event", lambda conn, **kw: None)

    out = service.complete_upload(object(), actor_sub=str(uuid4()), upload_id=upload_id, content=content)
    assert captured.get("sha256") == hashlib.sha256(content).hexdigest()
    assert out.id == upload_id


def test_documents_insert_file_upload_records_digest() -> None:
    from app.domains.documents import repository as docs_repo

    conn = _FakeConn(next_row={"id": str(uuid4())})
    docs_repo.insert_file_upload(
        conn,
        firm_id=uuid4(), household_id=uuid4(), matter_id=uuid4(),
        uploader_user_id=uuid4(), original_filename="f.pdf",
        stored_path="client-uploads/f.pdf", mime_type="application/pdf",
        size_bytes=3, sha256="c" * 64,
    )
    sql, params = conn.statements[0]
    assert "sha256" in sql
    assert "c" * 64 in params


# ---------------------------------------------------------------------------
# FUNC-006 — advisory lock before MAX(version)
# ---------------------------------------------------------------------------


def test_next_version_number_takes_advisory_lock_first() -> None:
    from app.domains.documents import repository as docs_repo

    package_id = uuid4()
    conn = _FakeConn(next_row={"max_version": 2})
    assert docs_repo.next_version_number(conn, package_id) == 3
    sqls = [sql for sql, _ in conn.statements]
    assert len(sqls) == 2, sqls
    assert "pg_advisory_xact_lock" in sqls[0] and "hashtext" in sqls[0]
    assert str(package_id) in conn.statements[0][1]
    assert "MAX(version)" in sqls[1]


# ---------------------------------------------------------------------------
# PERF-009 — explicit column list, no extracted_text
# ---------------------------------------------------------------------------


def test_get_file_upload_selects_explicit_columns_without_extracted_text() -> None:
    from app.domains.documents import repository as docs_repo

    upload_id = uuid4()
    conn = _FakeConn(next_row={"id": str(upload_id)})
    row = docs_repo.get_file_upload(conn, upload_id)
    assert row == {"id": str(upload_id)}
    sql = conn.statements[0][0]
    assert "SELECT *" not in sql
    assert "extracted_text" not in sql
    assert "sha256" in sql  # PERF-003 digest must be among the columns


def test_get_file_uploads_by_ids_uses_any_query() -> None:
    from app.domains.documents import repository as docs_repo

    id1, id2 = uuid4(), uuid4()
    conn = _FakeConn(next_rows=[{"id": str(id1)}, {"id": str(id2)}])
    result = docs_repo.get_file_uploads_by_ids(conn, [id1, id2])
    sql, params = conn.statements[0]
    assert "ANY" in sql
    assert "extracted_text" not in sql
    assert set(result) == {id1, id2}


# ---------------------------------------------------------------------------
# PERF-004 — snapshot person names from the fetched household list
# ---------------------------------------------------------------------------


def test_snapshot_resolves_nomination_names_from_household_persons(monkeypatch) -> None:
    from app.domains.review import service

    household_id = uuid4()
    matter_id = uuid4()
    in_list = uuid4()
    outside = uuid4()
    persons = [
        {"id": in_list, "full_name": "In List Person", "kind": "adult"},
    ]
    monkeypatch.setattr(
        service.household_repo, "list_persons_for_household", lambda conn, hid: persons
    )
    monkeypatch.setattr(
        service.household_repo, "list_relationships_for_household", lambda conn, hid: []
    )
    monkeypatch.setattr(service.intake_repo, "list_answers", lambda conn, mid: [])
    monkeypatch.setattr(service.decisions_repo, "list_decisions", lambda conn, mid: [])
    monkeypatch.setattr(service.escalation_repo, "list_escalations", lambda conn, mid: [])
    nominations = [
        {"nomination_type": "executor", "priority": 1, "status": "active", "person_id": in_list},
        {"nomination_type": "guardian", "priority": 1, "status": "active", "person_id": outside},
        {"nomination_type": "trustee", "priority": 1, "status": "active", "person_id": None},
    ]
    monkeypatch.setattr(
        service.nominations_repo, "list_nominations", lambda conn, mid: nominations
    )
    get_person_calls: list = []

    def _get_person(conn, pid):
        get_person_calls.append(pid)
        return {"full_name": "Outside Person"}

    monkeypatch.setattr(service.nominations_repo, "get_person", _get_person)

    matter = {
        "id": matter_id, "household_id": household_id, "title": "M",
        "status": "ATTORNEY_REVIEW", "plan_type": "will",
        "jurisdiction_snapshot": {}, "completeness_pct": 50,
        "assigned_attorney_id": None,
    }
    snapshot = service._build_snapshot(object(), matter)
    names = [n["person_name"] for n in snapshot["nominations"]]
    assert names == ["In List Person", "Outside Person", None]
    # Exactly one fallback lookup: the nomination whose person is NOT in
    # the household list. In-list and person-less nominations need none.
    assert get_person_calls == [outside]


# ---------------------------------------------------------------------------
# FUNC-013 / FUNC-014 — funding generation harness (in-memory repo)
# ---------------------------------------------------------------------------


class _FundingStore:
    """In-memory funding_tasks with ON CONFLICT DO NOTHING semantics."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def exists(self, matter_id, asset_id, task_type) -> bool:
        return any(
            r["matter_id"] == matter_id and r["asset_id"] == asset_id
            and r["task_type"] == task_type
            for r in self.rows
        )

    def insert(self, conn, *, firm_id, matter_id, asset_id, task_type, title):
        if self.exists(matter_id, asset_id, task_type):
            return None  # ON CONFLICT DO NOTHING
        row = {
            "id": uuid4(), "firm_id": firm_id, "matter_id": matter_id,
            "asset_id": asset_id, "task_type": task_type, "title": title,
            "status": "pending", "evidence_file_id": None, "due_date": None,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }
        self.rows.append(row)
        return row


_PLAYBOOKS = [
    {
        "playbook_id": "bank-tod-podb", "asset_types": ["bank"],
        "title": "Review transfer-on-death / payable-on-death designation",
        "priority": "high", "evidence_type": "beneficiary_confirmation",
        "attorney_review_recommended": False,
    },
    {
        "playbook_id": "general-beneficiary-alignment",
        "asset_types": ["bank", "brokerage", "retirement", "insurance", "529"],
        "title": "Align beneficiary designations with plan decisions",
        "priority": "medium", "evidence_type": "review_checklist",
        "attorney_review_recommended": False,
    },
]


def _wire_funding(monkeypatch, assets, *, stale_exists_check: bool = False):
    from app.domains.documents import repository as docs_repo
    from app.domains.documents import service

    store = _FundingStore()
    matter_id = uuid4()
    matter = {"id": matter_id, "firm_id": uuid4(), "household_id": uuid4()}

    class _Scope:
        pass

    scope = _Scope()
    scope.matter = matter
    monkeypatch.setattr(
        service.authz, "require_matter_firm_role", lambda conn, mid, aid, roles: scope
    )
    monkeypatch.setattr(service, "_actor_id", lambda sub: uuid4())

    class _Profile:
        funding_playbooks = _PLAYBOOKS

    monkeypatch.setattr(service, "_load_execution_profile", lambda conn, m: _Profile())
    monkeypatch.setattr(docs_repo, "list_assets_for_matter", lambda conn, mid: assets)
    if stale_exists_check:
        monkeypatch.setattr(docs_repo, "funding_task_exists", lambda conn, mid, aid, tt: False)
    else:
        monkeypatch.setattr(
            docs_repo, "funding_task_exists",
            lambda conn, mid, aid, tt: store.exists(mid, aid, tt),
        )
    monkeypatch.setattr(docs_repo, "insert_funding_task", store.insert)
    monkeypatch.setattr(service, "record_audit_event", lambda conn, **kw: None)
    return service, store, matter_id


def test_other_asset_yields_exactly_one_general_task(monkeypatch) -> None:
    asset_id = uuid4()
    assets = [{"id": asset_id, "asset_type": "other", "label": "Misc Account"}]
    service, store, matter_id = _wire_funding(monkeypatch, assets)

    created = service.generate_funding_tasks(object(), actor_sub=str(uuid4()), matter_id=matter_id)
    assert len(created) == 1
    assert created[0].task_type == "general-beneficiary-alignment"
    assert len(store.rows) == 1


def test_insert_funding_task_names_explicit_conflict_target() -> None:
    """FUNC-013 precision (Batch C2 follow-up): the insert must name
    the ``uq_funding_tasks_matter_asset_type`` columns as its conflict
    target — a bare ON CONFLICT DO NOTHING would silently swallow
    conflicts from ANY future unique constraint."""
    from app.domains.documents import repository as docs_repo

    conn = _FakeConn(next_row={"id": str(uuid4())})
    row = docs_repo.insert_funding_task(
        conn,
        firm_id=uuid4(),
        matter_id=uuid4(),
        asset_id=None,
        task_type="general-beneficiary-alignment",
        title="Align beneficiary designations",
    )
    assert row is not None
    sql = conn.statements[0][0]
    assert "ON CONFLICT (matter_id, asset_id, task_type) DO NOTHING" in sql


def test_double_generation_with_stale_exists_check_makes_no_duplicates(monkeypatch) -> None:
    asset_id = uuid4()
    assets = [{"id": asset_id, "asset_type": "bank_account", "label": "Checking"}]
    # The existence check always reads stale (the race window): the
    # insert-side conflict guard is what must prevent duplicates.
    service, store, matter_id = _wire_funding(monkeypatch, assets, stale_exists_check=True)

    first = service.generate_funding_tasks(object(), actor_sub=str(uuid4()), matter_id=matter_id)
    second = service.generate_funding_tasks(object(), actor_sub=str(uuid4()), matter_id=matter_id)
    assert len(first) == 2  # bank-tod-podb + general-beneficiary-alignment
    assert second == []
    assert len(store.rows) == 2
