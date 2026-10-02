"""Stripe Connect interface for attorney payouts (Sprint 5).

Manual bank transfers are the v1 payout path (see ``service.pay_payout``).
This module defines the Stripe Connect integration surface so payouts can
move to automated transfers without changing call sites.

Fails safe: every function raises ``StripeNotConfiguredError`` until
``STRIPE_SECRET_KEY`` is present in the environment. No real Stripe call is
ever attempted without keys.
"""

import os


class StripeNotConfiguredError(RuntimeError):
    """Raised when Stripe is called without ``STRIPE_SECRET_KEY`` set."""


def _require_keys() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise StripeNotConfiguredError(
            "Stripe is not configured. Set STRIPE_SECRET_KEY (and "
            "STRIPE_WEBHOOK_SECRET for webhooks) — see STRIPE_SETUP.md."
        )
    return key


def create_connected_account(*, email: str, full_name: str) -> dict:
    """Create a Stripe Express connected account for an attorney.

    Returns ``{"account_id": ..., "onboarding_url": ...}`` once implemented.
    """
    _require_keys()
    raise NotImplementedError(
        "Stripe Connect account creation is not implemented yet. "
        "See STRIPE_SETUP.md for the rollout plan."
    )


def create_payout(*, account_id: str, amount_cents: int, description: str) -> dict:
    """Transfer funds to a connected attorney account.

    Returns ``{"payout_id": ..., "status": ...}`` once implemented.
    """
    _require_keys()
    raise NotImplementedError(
        "Stripe Connect payouts are not implemented yet. "
        "Use the manual payout flow (admin marks paid) until then."
    )


def get_account_status(*, account_id: str) -> dict:
    """Return onboarding/payout readiness for a connected account."""
    _require_keys()
    raise NotImplementedError(
        "Stripe Connect account status is not implemented yet."
    )


def is_configured() -> bool:
    """Whether Stripe keys are present (safe to call anywhere)."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))
