"""Model round-trip validation (synthetic data only)."""

from datetime import UTC, date, datetime
from uuid import uuid4

from legacyflow_contracts import Capabilities, FirmMembership, JurisdictionPack, Matter


def _now() -> datetime:
    return datetime.now(UTC)


def test_firm_membership_round_trip() -> None:
    member = FirmMembership(
        id=uuid4(),
        firm_id=uuid4(),
        user_id=uuid4(),
        role="attorney",
        status="active",
        created_at=_now(),
    )
    assert FirmMembership.model_validate(member.model_dump()) == member
    assert FirmMembership.model_validate_json(member.model_dump_json()) == member


def test_matter_round_trip_with_opaque_status() -> None:
    matter = Matter(
        id=uuid4(),
        firm_id=uuid4(),
        household_id=uuid4(),
        primary_jurisdiction_id=uuid4(),
        jurisdiction_pack_id=uuid4(),
        plan_type="standard_couple_with_minor_children",
        status="intake_in_progress",
        created_at=_now(),
        updated_at=_now(),
    )
    assert Matter.model_validate_json(matter.model_dump_json()) == matter


def test_jurisdiction_pack_manifest_and_capabilities() -> None:
    pack = JurisdictionPack(
        id=uuid4(),
        jurisdiction_id=uuid4(),
        version="0.1.0",
        status="internal_testing",
        effective_from=date(2026, 9, 26),
        manifest={
            "pack": {"id": "us-va", "display_name": "Virginia"},
            "capabilities": {
                "intake": True,
                "execution_guidance": "attorney_pending",
            },
        },
        source_bundle_hash="synthetic-hash",
        created_at=_now(),
    )
    capabilities = Capabilities(**pack.manifest["capabilities"])
    assert capabilities.intake is True
    assert capabilities.execution_guidance == "attorney_pending"
    assert capabilities.document_assembly is False
    assert JurisdictionPack.model_validate(pack.model_dump()) == pack
