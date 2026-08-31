# tests/test_billing_plans_cache.py
#
# GET /api/billing/plans is read by the public pricing page, the signup
# page and every upgrade prompt in the product, and each cache miss is a
# synchronous call to Stripe.
#
# The failure this file exists to prevent: a transient Stripe error used
# to be written into the cache as `configured: False`, so every pricing
# surface said "pricing unavailable" for the next ten minutes, having
# known the real price a moment earlier.
from __future__ import annotations

import asyncio
import os
import time

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("STRIPE_MONTHLY_PRICE_ID", "price_monthly_test")
os.environ.setdefault("STRIPE_SEASON_PRICE_ID", "price_season_test")

import main  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setenv("STRIPE_MONTHLY_PRICE_ID", "price_monthly_test")
    monkeypatch.setenv("STRIPE_SEASON_PRICE_ID", "price_season_test")
    main._plan_cache.update({"at": 0.0, "value": None, "last_good": None, "last_good_at": 0.0})
    yield
    main._plan_cache.update({"at": 0.0, "value": None, "last_good": None, "last_good_at": 0.0})


class FakeStripePrice:
    """Stands in for stripe.Price.retrieve, and can be made to fail."""

    def __init__(self, amount=300, currency="gbp"):
        self.calls = 0
        self.fail = False
        self.amount = amount
        self.currency = currency

    def retrieve(self, price_id):
        self.calls += 1
        if self.fail:
            raise RuntimeError("stripe is having a moment")
        return {
            "unit_amount": self.amount,
            "currency": self.currency,
            "recurring": {"interval": "month", "interval_count": 1},
        }


@pytest.fixture
def stripe_price(monkeypatch):
    fake = FakeStripePrice()
    monkeypatch.setattr(main.stripe.Price, "retrieve", fake.retrieve)
    return fake


class TestItDoesNotCallStripePerPageView:
    def test_a_second_read_inside_the_ttl_is_served_from_cache(self, stripe_price):
        run(main._plan_catalogue())
        first = stripe_price.calls
        run(main._plan_catalogue())
        assert stripe_price.calls == first

    def test_the_ttl_is_configurable(self):
        assert main._PLAN_CACHE_TTL > 0

    def test_concurrent_misses_refresh_once_not_once_each(self, stripe_price):
        # Without the lock, the first request after expiry is joined by
        # every other in-flight request and all of them call Stripe.
        async def hammer():
            await asyncio.gather(*[main._plan_catalogue() for _ in range(20)])

        run(hammer())
        # Two prices configured, so one refresh is two retrieve calls.
        assert stripe_price.calls == 2

    def test_an_expired_cache_does_refresh(self, stripe_price):
        run(main._plan_catalogue())
        before = stripe_price.calls
        main._plan_cache["at"] = time.time() - main._PLAN_CACHE_TTL - 1
        run(main._plan_catalogue())
        assert stripe_price.calls > before


class TestStripeOutage:
    def test_the_last_known_price_is_served_rather_than_none(self, stripe_price):
        good = run(main._plan_catalogue())
        assert good["plans"]["monthly"]["price_display"] == "£3/month"

        stripe_price.fail = True
        main._plan_cache["at"] = 0.0
        during = run(main._plan_catalogue())

        # The price survives the outage.
        assert during["plans"]["monthly"]["price_display"] == "£3/month"
        assert during["stale"] is True
        assert during["stale_since"]

    def test_a_fresh_read_is_not_marked_stale(self, stripe_price):
        assert run(main._plan_catalogue())["stale"] is False

    def test_an_outage_with_no_previous_success_reports_unconfigured_not_a_guess(self, stripe_price):
        stripe_price.fail = True
        catalogue = run(main._plan_catalogue())
        assert catalogue["any_configured"] is False
        assert catalogue["plans"]["monthly"]["price_display"] is None
        # Never invented.
        assert catalogue["plans"]["monthly"]["amount_minor"] is None

    def test_a_failure_is_retried_sooner_than_a_success_is_refreshed(self, stripe_price):
        run(main._plan_catalogue())
        stripe_price.fail = True
        main._plan_cache["at"] = 0.0
        run(main._plan_catalogue())

        # Serving stale must not mean waiting out the full TTL: if Stripe
        # comes back in thirty seconds, so should the live price.
        assert main._plan_cache_ttl() == main._PLAN_CACHE_ERROR_TTL
        assert main._PLAN_CACHE_ERROR_TTL < main._PLAN_CACHE_TTL

    def test_recovery_replaces_the_stale_answer(self, stripe_price):
        run(main._plan_catalogue())
        stripe_price.fail = True
        main._plan_cache["at"] = 0.0
        assert run(main._plan_catalogue())["stale"] is True

        stripe_price.fail = False
        stripe_price.amount = 400
        main._plan_cache["at"] = 0.0
        recovered = run(main._plan_catalogue())
        assert recovered["stale"] is False
        assert recovered["plans"]["monthly"]["price_display"] == "£4/month"

    def test_a_failure_never_becomes_the_fallback(self, stripe_price):
        # Caching a failure as the thing to fall back to would make the
        # outage permanent.
        stripe_price.fail = True
        run(main._plan_catalogue())
        assert main._plan_cache["last_good"] is None


class TestUnconfigured:
    def test_no_price_id_is_a_clear_state_not_an_error(self, stripe_price, monkeypatch):
        monkeypatch.delenv("STRIPE_MONTHLY_PRICE_ID", raising=False)
        monkeypatch.delenv("STRIPE_SEASON_PRICE_ID", raising=False)
        catalogue = run(main._plan_catalogue())
        assert catalogue["any_configured"] is False
        for plan in catalogue["plans"].values():
            assert plan["configured"] is False
            assert plan["price_display"] is None
        # And Stripe was never called.
        assert stripe_price.calls == 0


class TestNothingCustomerSpecificIsCached:
    def test_the_catalogue_takes_no_user_input(self):
        import inspect

        assert list(inspect.signature(main._plan_catalogue).parameters) == []

    def test_the_endpoint_takes_no_user_input_either(self):
        import inspect

        # A shared cache holding per-customer pricing would be a data
        # leak, so there is deliberately nothing to key by.
        assert list(inspect.signature(main.get_billing_plans).parameters) == []

    def test_the_cached_value_contains_no_identity(self, stripe_price):
        blob = str(run(main._plan_catalogue()))
        for forbidden in ("customer", "cus_", "email", "user_id", "subscription"):
            assert forbidden not in blob.lower()


class TestTheChargedPriceIsStillAuthoritative:
    def test_checkout_does_not_read_the_cache(self):
        import inspect

        # The cache is for DISPLAY. What a customer is charged is decided
        # by the price id sent to Stripe at checkout, not by anything
        # cached here - so a stale display price can never become a stale
        # charge.
        source = inspect.getsource(main.create_checkout_session)
        assert "_plan_catalogue" not in source
        assert "_plan_cache" not in source
