# tests/test_billing_hardening.py
#
# The checkout session used to take three things from the request body
# that decide what the customer is charged and where they end up. All
# three are now server-side.
from __future__ import annotations

import inspect
import os
import re

import pytest

# These tests read the SOURCE of main.py's checkout handler, so they have
# to import main - and main validates its required environment at import
# time. Placeholders satisfy that check without reaching anything: nothing
# in this file opens a connection or signs a token. Set before the import
# rather than relying on some earlier test module in the run having
# happened to export them, which made this file pass or fail depending on
# collection order.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
# main.py hard-fails on the default JWT signing key when ENV looks like
# production, which it does by default. Marking the run as a test
# environment is the honest way past that guard - the alternative would be
# supplying a fake production key, which is the exact thing the guard
# exists to stop anyone getting used to doing.
os.environ.setdefault("ENV", "test")


def _code_of(func) -> str:
    """Executable lines only.

    These assertions are about what the code DOES, and the code now
    carries comments explaining the exact strings being asserted against
    ("trialDays came from the request body..."), so a naive substring
    search over the raw source matches the explanation and fails."""
    lines = []
    for line in inspect.getsource(func).split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  #")[0])
    return "\n".join(lines)


def _checkout_source() -> str:
    import main
    return _code_of(main.create_checkout_session)


class TestClientCannotControlTheCheckout:
    def test_trial_length_does_not_come_from_the_request_body(self):
        # `data.get('trialDays', 7)` meant {"trialDays": 100000} bought
        # roughly 273 years of Elite for nothing.
        source = _checkout_source()
        assert "trialDays" not in source
        assert "STRIPE_TRIAL_DAYS" in source

    def test_redirect_urls_do_not_come_from_the_request_body(self):
        # success_url/cancel_url from `data` let a caller point a real
        # Stripe Checkout session at any page they liked.
        source = _checkout_source()
        assert 'data.get("successUrl"' not in source
        assert 'data.get("cancelUrl"' not in source
        assert "FRONTEND_URL" in source

    def test_the_success_url_is_on_our_own_frontend(self):
        source = _checkout_source()
        match = re.search(r'success_url = f"\{FRONTEND_URL\}([^"]*)"', source)
        assert match, "success_url must be built from FRONTEND_URL"
        assert match.group(1).startswith("/#/")

    def test_trial_is_omitted_entirely_when_configured_to_zero(self):
        # Not "trial_period_days: 0", which Stripe rejects.
        source = _checkout_source()
        assert "if STRIPE_TRIAL_DAYS > 0:" in source

    def test_checkout_refuses_existing_active_subscription(self):
        source = _checkout_source()
        assert "existing_subscription" in source
        assert "active', 'trialing', 'trial', 'past_due" in source
        assert "status_code=409" in source

    def test_checkout_preserves_intentional_http_errors(self):
        source = _checkout_source()
        assert "except HTTPException:" in source
        assert "raise" in source


class TestAccountDeletionStopsBilling:
    def test_account_deletion_cancels_renewal_before_scrubbing_user(self):
        import main
        source = _code_of(main.delete_account)
        assert "stripe.Subscription.modify" in source
        assert "cancel_at_period_end=True" in source
        assert "Could not cancel active billing before deleting account" in source
        assert "UPDATE users" in source
        assert "email=NULL" in source
        assert "discord_id=NULL" in source

    def test_billing_mutations_preserve_intentional_http_errors(self):
        import main
        for func in (main.delete_account, main.cancel_subscription, main.update_payment_method):
            source = _code_of(func)
            assert "except HTTPException:" in source


class TestPriceHasOneSource:
    def test_no_hardcoded_price_strings_remain_in_the_subscription_response(self):
        # The contradiction being removed: this endpoint said
        # "£9.99/month" while the Billing page said "£3/month" and Stripe
        # charged a third thing.
        import main
        source = _code_of(main.get_subscription_status)
        assert "9.99" not in source
        assert "99.99" not in source
        assert "_plan_catalogue" in source

    def test_price_formatting_is_currency_aware(self):
        import main
        assert main._format_price(300, "gbp", "month") == "£3/month"
        assert main._format_price(2550, "gbp", "season") == "£25.50/season"
        assert main._format_price(300, "usd", "month") == "$3/month"

    def test_a_missing_price_renders_as_nothing_not_as_a_guess(self):
        import main
        assert main._format_price(None, "gbp", "month") is None


@pytest.mark.parametrize("env_var", ["STRIPE_MONTHLY_PRICE_ID", "STRIPE_SEASON_PRICE_ID"])
def test_plan_catalogue_reports_unconfigured_plans_honestly(monkeypatch, env_var):
    import asyncio

    import main
    monkeypatch.delenv("STRIPE_MONTHLY_PRICE_ID", raising=False)
    monkeypatch.delenv("STRIPE_SEASON_PRICE_ID", raising=False)
    main._plan_cache.update({"at": 0.0, "value": None})
    catalogue = asyncio.run(main._plan_catalogue())
    assert catalogue["any_configured"] is False
    for plan in catalogue["plans"].values():
        assert plan["configured"] is False
        assert plan["price_display"] is None
