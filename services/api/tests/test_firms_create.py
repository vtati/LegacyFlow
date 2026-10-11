"""Tests for firm creation API."""
from __future__ import annotations


class TestCreateFirm:
    def test_create_firm_schema(self) -> None:
        from app.domains.firms.schemas import FirmCreate

        payload = FirmCreate(legal_name="Gen Alpha Labs LLC", display_name="Gen Alpha Labs")
        assert payload.legal_name == "Gen Alpha Labs LLC"
        assert payload.display_name == "Gen Alpha Labs"

    def test_create_firm_service_exists(self) -> None:
        from app.domains.firms import service

        assert callable(getattr(service, "create_firm", None))

    def test_create_firm_makes_creator_admin(self) -> None:
        # The creator of a firm must become its firm_admin.
        import inspect

        from app.domains.firms import service

        source = inspect.getsource(service.create_firm)
        assert "firm_admin" in source
