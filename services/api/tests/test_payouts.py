"""Unit tests for payout tiering and Stripe fail-safe (no DB required)."""

import os

import pytest

from app.domains.payouts import service, stripe_connect


def test_tier_for_matter_standard():
    assert service.tier_for_matter("will") == "standard"
    assert service.tier_for_matter("poa") == "standard"
    assert service.tier_for_matter(None) == "standard"
    assert service.tier_for_matter("") == "standard"


def test_tier_for_matter_complex():
    assert service.tier_for_matter("trust") == "complex"
    assert service.tier_for_matter("bundle") == "complex"
    assert service.tier_for_matter("TRUST") == "complex"


def test_payout_amount_defaults():
    os.environ.pop("LF_PAYOUT_STANDARD_CENTS", None)
    os.environ.pop("LF_PAYOUT_COMPLEX_CENTS", None)
    assert service.payout_amount_for_tier("standard") == 15000
    assert service.payout_amount_for_tier("complex") == 30000


def test_payout_amount_configurable(monkeypatch):
    monkeypatch.setenv("LF_PAYOUT_STANDARD_CENTS", "20000")
    monkeypatch.setenv("LF_PAYOUT_COMPLEX_CENTS", "40000")
    assert service.payout_amount_for_tier("standard") == 20000
    assert service.payout_amount_for_tier("complex") == 40000


def test_stripe_not_configured_by_default(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert stripe_connect.is_configured() is False
    with pytest.raises(stripe_connect.StripeNotConfiguredError):
        stripe_connect.create_connected_account(email="a@b.c", full_name="Test")
    with pytest.raises(stripe_connect.StripeNotConfiguredError):
        stripe_connect.create_payout(account_id="x", amount_cents=100, description="d")
    with pytest.raises(stripe_connect.StripeNotConfiguredError):
        stripe_connect.get_account_status(account_id="x")
