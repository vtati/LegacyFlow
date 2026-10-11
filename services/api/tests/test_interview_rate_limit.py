"""Tests for AI interview rate limiting."""
from __future__ import annotations


class TestInterviewRateLimit:
    def test_rate_limit_constants(self) -> None:
        from app.domains.interview import service

        assert service.INTERVIEW_TURNS_PER_MINUTE == 10
        assert service.INTERVIEW_TURNS_PER_DAY == 200

    def test_rate_limit_helper_exists(self) -> None:
        from app.domains.interview import service

        assert callable(getattr(service, "_check_turn_rate_limit", None))

    def test_rate_limit_error_type(self) -> None:
        from app.core.errors import RateLimitError

        err = RateLimitError("Too many interview turns.")
        assert err.status == 429
