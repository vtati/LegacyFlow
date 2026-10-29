"""Template truthfulness: conditional prose must follow the facts.

Defects (caught by the Attorney Reviewer on CLEAN packets, 2026-10-27):
(a) The Will template hardcoded ``I am married to {{spouse_name}}.`` and
    the children header as unconditional prose, while the renderer is a
    pure substitution engine. A SINGLE testator's Will therefore printed
    "I am married to ___." plus a blank children line — a false factual
    assertion in a legal document. The Article I recital is now composed
    by the context builder (``family_identification``), which is the
    layer that knows marital status, the resolved spouse, and the
    children facts.
(b) The AMD template printed the anatomical-gift agent appointment
    unconditionally, and the slot table aliased the healthcare agent
    into ``organ_donation_agent`` without consulting the donation
    answer — so a directive saying "I do not wish to donate organs"
    still appointed an agent to make the gift. The appointment is now
    composed (``anatomical_gift_appointment``) and gated on the
    donation answer; the raw slot is gated the same way.

The married-with-spouse rendering is byte-identical to before (the
2026-10-27 verified behavior): spouse resolution is NOT loosened —
it still comes only from a relationships row / member role.

All fixture data is synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.domains.documents import service as documents_service

CLIENT = "d4444444-4444-4444-4444-444444444444"
US_VA_JURISDICTION_ID = "11111111-1111-1111-1111-111111111111"
US_VA_PACK_ID = "22222222-2222-2222-2222-222222222222"

TRUNCATE_TABLES = (
    "document_templates, document_packages, document_versions,"
    " signing_checklists, funding_tasks, vault_shares, reminders,"
    " file_uploads, notification_outbox, audit_events, escalations,"
    " intake_answers, matter_status_history, matters, invitations,"
    " nominations, planning_decisions, relationships, persons,"
    " household_members, households, firm_memberships, firms, profiles,"
    " feature_flags, attorney_applications, review_packets, approval_gates,"
    " comments, clarification_requests,"
    " asset_flags, assets, liabilities, auth.users"
)

AUTH_STUB = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY, email text,
    raw_user_meta_data jsonb, raw_app_meta_data jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT NULL::uuid $$;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean
);
"""

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SEED_FILE = Path(__file__).resolve().parents[3] / "supabase" / "seeds" / "01_jurisdictions.sql"
TEMPLATES_DIR = (
    Path(__file__).resolve().parents[1]
    / "content-packs" / "templates" / "us-va" / "v0"
)

TESTATOR = "Maya Lee"
SPOUSE = "Daniel Lee"
AGENT = "Grace Liu"
CHILD = "Noah Lee"


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    try:
        import pgserver
    except ImportError:
        pytest.skip("pgserver is not installed")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata-template-truth")))
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.execute(SEED_FILE.read_text())
    yield uri
    server.cleanup()


@pytest.fixture()
def db(pg_uri: str):
    with psycopg.connect(pg_uri, row_factory=dict_row) as conn:
        conn.execute(f"TRUNCATE {TRUNCATE_TABLES} CASCADE")
        conn.execute(SEED_FILE.read_text())
        conn.commit()
        yield conn
        conn.rollback()


def _exec(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def set_answer(db, matter_id: str, key: str, value) -> None:
    _exec(
        db,
        "INSERT INTO intake_answers (matter_id, question_key, answer)"
        " VALUES (%s, %s, %s::jsonb)"
        " ON CONFLICT (matter_id, question_key) DO UPDATE SET answer = EXCLUDED.answer",
        (matter_id, key, json.dumps(value)),
    )


def build_world(db, *, with_spouse_row: bool = False, with_child: bool = False):
    """Firm + household + matter; testator is primary_client.

    Returns (household_id, matter_id, {name: person_id}). The spouse
    and agent persons always exist as household adults; the spouse is
    only LINKED (relationships row) when ``with_spouse_row`` — mirroring
    production, where a person existing is not a marriage signal.
    """
    _exec(db, "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
          (CLIENT, "client@example.test"))
    _exec(db, "INSERT INTO profiles (id, email, display_name) VALUES (%s, %s, %s)"
              " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
          (CLIENT, "client@example.test", "Synthetic Client"))
    firm_id = str(uuid4())
    _exec(db, "INSERT INTO firms (id, legal_name, display_name, status)"
              " VALUES (%s, %s, %s, 'active')", (firm_id, "Synthetic Firm", "Synthetic Firm"))
    household_id = str(uuid4())
    _exec(db, "INSERT INTO households (id, firm_id, display_name, created_by_user_id)"
              " VALUES (%s, %s, %s, %s)",
          (household_id, firm_id, "Lee Household", CLIENT))
    person_ids: dict[str, str] = {}
    roster = [(TESTATOR, "adult"), (SPOUSE, "adult"), (AGENT, "adult")]
    if with_child:
        roster.append((CHILD, "minor"))
    for idx, (name, kind) in enumerate(roster):
        pid = str(uuid4())
        person_ids[name] = pid
        _exec(db, "INSERT INTO persons (id, household_id, full_name, kind, created_at)"
                  " VALUES (%s, %s, %s, %s, %s)",
              (pid, household_id, name, kind, f"2026-01-01T00:00:{idx:02d}Z"))
    _exec(db, "INSERT INTO household_members"
              " (household_id, user_id, person_id, relationship_role, access_status)"
              " VALUES (%s, %s, %s, 'primary_client', 'active')",
          (household_id, CLIENT, person_ids[TESTATOR]))
    if with_spouse_row:
        _exec(db, "INSERT INTO relationships"
                  " (household_id, from_person_id, to_person_id, relationship_type)"
                  " VALUES (%s, %s, %s, 'spouse')",
              (household_id, person_ids[TESTATOR], person_ids[SPOUSE]))
    matter_id = str(uuid4())
    snapshot = json.dumps(
        {"pack_id": "us-va", "jurisdiction_code": "US-VA", "semantic_version": "2.0.0"}
    )
    _exec(db, "INSERT INTO matters (id, firm_id, household_id, primary_jurisdiction_id,"
              " jurisdiction_pack_id, plan_type, status, title, jurisdiction_snapshot,"
              " completeness_pct)"
              " VALUES (%s, %s, %s, %s, %s, 'will', 'INTAKE_IN_PROGRESS', %s, %s::jsonb, 0)",
          (matter_id, firm_id, household_id, US_VA_JURISDICTION_ID, US_VA_PACK_ID,
           "Lee Estate Plan", snapshot))
    set_answer(db, matter_id, "testator_full_name", TESTATOR)
    set_answer(db, matter_id, "testator_street_address", "9 Elm Street")
    set_answer(db, matter_id, "testator_city", "Richmond")
    return household_id, matter_id, person_ids


def render(db, matter_id: str, template_file: str) -> str:
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    body = (TEMPLATES_DIR / template_file).read_text(encoding="utf-8")
    return documents_service.render_template_content(body, ctx)


# ---------------------------------------------------------------------------
# (a) Will Article I — marital sentence follows marital facts
# ---------------------------------------------------------------------------


def test_married_with_captured_spouse_renders_sentence_with_name(db):
    _h, matter_id, _ids = build_world(db, with_spouse_row=True, with_child=True)
    set_answer(db, matter_id, "marital_status", "married")
    set_answer(db, matter_id, "has_minor_children", True)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["spouse_name"] == SPOUSE
    assert ctx["family_identification"] == (
        f"I am married to {SPOUSE}.\n\nI have the following children:\n{CHILD}"
    )
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert f"I am married to {SPOUSE}." in will
    assert f"I have the following children:\n{CHILD}" in will


def test_single_testator_no_marital_assertion_no_blank_children(db):
    """The Lee defect: single + childless printed 'I am married to ___.'
    and a blank children line."""
    _h, matter_id, _ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", False)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert "spouse_name" not in ctx
    assert ctx["family_identification"] == "I am not married."
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "married to" not in will
    assert "I have the following children" not in will
    assert "___" not in will.split("ARTICLE II")[0]


def test_widowed_testator_no_marital_assertion(db):
    _h, matter_id, _ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "widowed")
    set_answer(db, matter_id, "has_minor_children", False)
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "married to" not in will
    assert "I am widowed." in will


def test_no_marital_signal_at_all_no_assertion(db):
    """Legacy-shaped data: no marital_status answer, no spouse signal.
    The Will must not assert a marriage it has no evidence for."""
    _h, matter_id, _ids = build_world(db)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["family_identification"] == ""
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "married to" not in will


def test_partnered_with_captured_spouse_renders_as_before(db):
    """Partnered + relationships signal: the sentence renders with the
    spouse's name, byte-identical to the pre-fix married rendering
    (accepted L-4 — the fix targets assertion-without-facts, not the
    partnered wording, which is a separate product question)."""
    _h, matter_id, _ids = build_world(db, with_spouse_row=True)
    set_answer(db, matter_id, "marital_status", "partnered")
    set_answer(db, matter_id, "has_minor_children", False)
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert f"I am married to {SPOUSE}." in will


def test_household_less_matter_leaves_composed_slots_absent(monkeypatch):
    """No household: the composed slots are simply absent (the merge
    blanks them); the builder must not crash or invent recitals."""
    from contextlib import contextmanager

    from app.domains.intake import repository as intake_repo

    class _Cursor:
        def __init__(self, row):
            self._row = row

        def execute(self, *_a, **_k):
            pass

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self, row):
            self._row = row

        @contextmanager
        def cursor(self):
            yield _Cursor(self._row)

    matter_id = uuid4()
    conn = _Conn({"id": str(matter_id), "household_id": None})
    monkeypatch.setattr(documents_service, "_render_context", lambda c, m, answers=None: {})
    monkeypatch.setattr(intake_repo, "list_answers", lambda c, mid: [])
    ctx = documents_service.build_template_context(conn, matter_id)
    assert "family_identification" not in ctx
    assert "anatomical_gift_appointment" not in ctx


def test_married_but_spouse_name_uncaptured_keeps_attorney_blank(db):
    """Status says married; the name was never captured (no relationships
    row). The genuine gap stays visible as an attorney-fill blank —
    the sentence is NOT silently dropped for someone who IS married."""
    _h, matter_id, _ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "married")
    set_answer(db, matter_id, "has_minor_children", False)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert "spouse_name" not in ctx
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "I am married to ___." in will


def test_children_block_from_details_answer_when_no_minor_persons(db):
    _h, matter_id, _ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", True)
    set_answer(db, matter_id, "minor_children_details", "Noah Lee (7)")
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "I have the following children:\nNoah Lee (7)" in will


def test_children_block_blank_kept_when_children_stated_but_uncaptured(db):
    """has_minor_children is True but neither persons nor details were
    captured: the block stays with an attorney-fill blank rather than
    the children's existence being silently erased."""
    _h, matter_id, _ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", True)
    will = render(db, matter_id, "last-will-and-testament.txt")
    assert "I have the following children:\n___" in will


# ---------------------------------------------------------------------------
# (b) Advance directive — anatomical-gift appointment follows the wish
# ---------------------------------------------------------------------------


def _amd_answers(db, matter_id, ids, wishes, *, agent=AGENT):
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", False)
    set_answer(db, matter_id, "healthcare_agent_person", ids[agent])
    set_answer(db, matter_id, "organ_donation_wishes", wishes)


def test_amd_declined_donation_renders_no_gift_appointment(db):
    """The Lee defect: 'I do not wish to donate organs' paired with an
    anatomical-gift agent appointment."""
    _h, matter_id, ids = build_world(db)
    _amd_answers(db, matter_id, ids, "I do not wish to donate organs")
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    # The healthcare agent appointment itself is unaffected...
    assert ctx["healthcare_agent_name"] == AGENT
    # ...but the donation slot is NOT aliased from the healthcare agent.
    assert "organ_donation_agent" not in ctx
    assert ctx["anatomical_gift_appointment"] == ""
    amd = render(db, matter_id, "advance-medical-directive.txt")
    assert "I do not wish to donate organs" in amd
    assert "to make any anatomical gift" not in amd
    assert f"Agent: {AGENT}" in amd


def test_amd_wished_donation_renders_gift_appointment(db):
    _h, matter_id, ids = build_world(db)
    _amd_answers(db, matter_id, ids, "Donate any needed organs")
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["organ_donation_agent"] == AGENT
    amd = render(db, matter_id, "advance-medical-directive.txt")
    assert (
        f"I appoint {AGENT} to make any anatomical gift or organ, tissue, "
        "or eye donation following my death."
    ) in amd


def test_amd_wished_but_agent_uncaptured_keeps_attorney_blank(db):
    _h, matter_id, ids = build_world(db)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "organ_donation_wishes", "Donate any needed organs")
    amd = render(db, matter_id, "advance-medical-directive.txt")
    assert "I appoint ___ to make any anatomical gift" in amd


def test_amd_no_donation_answer_keeps_legacy_appointment(db):
    """No donation answer at all (legacy/partial data): behavior is as
    before — only an affirmative decline suppresses the appointment."""
    _h, matter_id, ids = build_world(db)
    set_answer(db, matter_id, "healthcare_agent_person", ids[AGENT])
    amd = render(db, matter_id, "advance-medical-directive.txt")
    assert f"I appoint {AGENT} to make any anatomical gift" in amd


# ---------------------------------------------------------------------------
# Donation-decline interpretation (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "I do not wish to donate organs",
    "No",
    "No.",
    "no donation",
    "I don't want to donate anything",
    "Not interested in donating",
    "Against organ donation",
    "Do not donate",
    "I do not wish to make an anatomical gift",
    "None",
    "Declined",
    "I do not wish to donate organs, tissue, or eyes",
    # Reviewer-found misses (M-2): donor/harvest/object/oppose/forbid.
    "I decline to be an organ donor",
    "Not an organ donor",
    "I am not a donor",
    "Do not harvest my organs",
    "No harvesting",
    "Please do not use my organs",
    "I object to organ donation",
    "I oppose donation",
    "Opposed to donation",
    "I forbid organ donation",
    "I refuse organ donation",
    "My organs are not to be donated",
    "Not only do I refuse, I forbid organ donation",
    "Not only do I not wish to donate, I forbid it",
    # Delta-review N-2: a donor-card/registry mention must not rescue
    # an explicit decline in another clause.
    "I do not wish to donate. Please destroy my donor card",
    "Not an organ donor. Ignore my old donor card",
    "I forbid organ donation, donor registry entry was a mistake",
    # Conjunction-split safety: declines joined by "and" still decline.
    "I do not wish to donate organs and tissue",
    "Do not donate organs and do not harvest tissue",
    # Delta-review R-1: ONE negation scoping over coordinated donation
    # keywords — the post-"and" fragment is part of the same decline,
    # not a new wish.
    "I refuse organ and tissue donation",
    "I oppose organ and tissue donation",
    "I decline organ and tissue donation",
    "No organ and tissue donation",
    "I am against organ and tissue donation",
    "Not for organ and tissue donation",
    "Decline organs and tissue donation",
    "Do not harvest and donate my organs",
    "Do not donate and harvest my organs",
    "I do not want my organs harvested and donated",
    "No harvesting and donation of my organs",
    "I forbid harvesting and donation",
    "I refuse organ and tissue and eye donation",
    # Saturation still absorbs a bare coordinated NP fragment.
    "I decline eye donation and organ donation",
])
def test_donation_declined_phrasings(text):
    assert documents_service._organ_donation_declined(text) is True


@pytest.mark.parametrize("text", [
    "Donate any needed organs",
    "Yes, donate everything",
    "I wish to donate my organs, tissue, or eyes",
    "Donate organs but not eyes",
    "I do not wish to donate my eyes, but donate my organs",
    "Make an anatomical gift of any organs",
    "Undecided — ask my family",
    "",
    "Donate everything, no exceptions",
    # Reviewer-found false declines (H-1): consent idioms phrase the
    # wish AS a negation of objection — they must keep the appointment.
    "I have no objection to donating my organs",
    "I have no problem donating organs",
    "No problem with organ donation",
    "Not opposed to organ donation",
    "I'm not against donating organs",
    "I don't mind donating organs",
    "Do not hesitate to donate my organs",
    "No hesitation - donate my organs",
    "No restrictions on organ donation",
    "Not only donate organs, but tissue too",
    "I want to donate, no question",
    "Donate — not for resale",
    "My wish: not cremation, donate organs",
    "NoVA resident; donate any organs",
    # Delta-review N-1: an objection aimed at something else in the
    # same sentence must not scope onto the donation wish.
    "I oppose cremation and want to donate my organs",
    "I object to cremation and wish to donate my organs",
    "Forbid cremation and donate my organs",
    "I am opposed to cremation and want to be an organ donor",
    "I object to cremation, donate my organs",
    "Opposed to cremation; donate everything",
    "My donor card is signed",
    "I am a registered organ donor",
    "Harvest my organs for transplant",
    "Forbid cremation, donate organs",
    # Delta-review R-1 counterweight: a coordinated fragment carrying
    # its own explicit wish breaks a preceding decline's coordination
    # (a partial wish — the appointment renders).
    "I refuse brain donation and want to donate my organs",
    # Delta-review R-2: once a refusal is saturated by its own
    # donation object, a following VERB-bearing fragment is a new,
    # affirmative clause (partial wish), not part of the decline.
    "I decline eye donation and donate my organs",
    "I refuse brain donation and give my organs to science",
    "I refuse organ donation and authorize tissue donation",
    "I oppose organ donation and support tissue donation",
    "I refuse brain donation and my organs are available for transplant",
    "I refuse organ donation and then donate my organs later",
    "I refuse organ donation and make my organs available for transplant",
    "I decline eye donation and offer my organs to others",
    "I refuse organ donation and pledge my organs to research",
])
def test_donation_not_declined_phrasings(text):
    assert documents_service._organ_donation_declined(text) is False


def test_donation_declined_non_string_answers():
    assert documents_service._organ_donation_declined(None) is False
    assert documents_service._organ_donation_declined(True) is False


# ---------------------------------------------------------------------------
# (c) Revocable living trust Article V — distribution clauses follow the
# facts (FUNC-026: the Will's truthfulness fix, applied to the trust)
# ---------------------------------------------------------------------------


def test_trust_single_childless_grantor_has_no_marital_or_children_clauses(db):
    _h, matter_id, _ids = build_world(db, with_spouse_row=False, with_child=False)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", False)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert ctx["trust_distribution_provisions"] == ""
    trust = render(db, matter_id, "revocable-living-trust.txt")
    assert "Marital share" not in trust
    assert "Shares for children" not in trust
    assert "divided into equal shares for my children" not in trust


def test_trust_married_with_children_keeps_both_clauses_verbatim(db):
    _h, matter_id, _ids = build_world(db, with_spouse_row=True, with_child=True)
    set_answer(db, matter_id, "marital_status", "married")
    set_answer(db, matter_id, "has_minor_children", True)
    set_answer(db, matter_id, "trust_marital_share", "one-half of the trust estate")
    trust = render(db, matter_id, "revocable-living-trust.txt")
    assert "5.2 Marital share. one-half of the trust estate" in trust
    assert (
        "5.3 Shares for children. The remaining trust property shall be "
        f"divided into equal shares for my children:\n{CHILD}"
    ) in trust


def test_trust_married_no_children_omits_children_clause(db):
    _h, matter_id, _ids = build_world(db, with_spouse_row=True, with_child=False)
    set_answer(db, matter_id, "marital_status", "married")
    set_answer(db, matter_id, "has_minor_children", False)
    set_answer(db, matter_id, "trust_marital_share", "the marital share as elected")
    trust = render(db, matter_id, "revocable-living-trust.txt")
    assert "5.2 Marital share. the marital share as elected" in trust
    assert "Shares for children" not in trust


def test_trust_single_with_children_omits_marital_clause(db):
    _h, matter_id, _ids = build_world(db, with_spouse_row=False, with_child=True)
    set_answer(db, matter_id, "marital_status", "single")
    set_answer(db, matter_id, "has_minor_children", True)
    trust = render(db, matter_id, "revocable-living-trust.txt")
    assert "Marital share" not in trust
    assert "Shares for children" in trust
    assert CHILD in trust


# ---------------------------------------------------------------------------
# PERF-008: build_template_context fetches intake answers exactly once
# ---------------------------------------------------------------------------


def test_build_template_context_fetches_answers_once(db, monkeypatch):
    from app.domains.intake import repository as intake_repo

    _h, matter_id, _ids = build_world(db, with_spouse_row=True, with_child=True)
    set_answer(db, matter_id, "marital_status", "married")
    original = intake_repo.list_answers
    calls = {"n": 0}

    def counting(conn, mid):
        calls["n"] += 1
        return original(conn, mid)

    monkeypatch.setattr(intake_repo, "list_answers", counting)
    ctx = documents_service.build_template_context(db, UUID(matter_id))
    assert calls["n"] == 1
    # Behavior is unchanged: composed slots are all present.
    assert ctx["spouse_name"] == SPOUSE
    assert ctx["family_identification"].startswith(f"I am married to {SPOUSE}.")
    assert CHILD in ctx["children_list"]
