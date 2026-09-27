"""Settings configuration tests.

Covers the Slice-1 "required-but-empty" contract for
``SUPABASE_SERVICE_ROLE_KEY``: deployments must fail fast when the variable
is missing, while an explicitly empty value is accepted (the key is not
used yet in Slice 1).
"""

import pytest
from pydantic import ValidationError


def test_service_role_key_missing_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_service_role_key_may_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.SUPABASE_SERVICE_ROLE_KEY == ""
    finally:
        get_settings.cache_clear()
