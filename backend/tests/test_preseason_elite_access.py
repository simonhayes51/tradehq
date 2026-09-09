from datetime import datetime, timezone

from app.services import preseason_elite_access as promo


def test_preseason_access_is_active_before_cutoff(monkeypatch):
    monkeypatch.setenv("PRESEASON_ELITE_UNTIL", "2026-09-18T00:00:00+00:00")
    assert promo.preseason_elite_active(datetime(2026, 9, 17, 23, 59, tzinfo=timezone.utc)) is True


def test_preseason_access_expires_exactly_at_cutoff(monkeypatch):
    monkeypatch.setenv("PRESEASON_ELITE_UNTIL", "2026-09-18T00:00:00+00:00")
    assert promo.preseason_elite_active(datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)) is False


def test_preseason_access_can_be_disabled_without_code_change(monkeypatch):
    monkeypatch.setenv("PRESEASON_ELITE_UNTIL", "off")
    assert promo.preseason_elite_active(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)) is False
    payload = promo.preseason_payload()
    assert payload["active"] is False
    assert payload["until"] is None


def test_promo_payload_is_not_a_stripe_trial(monkeypatch):
    monkeypatch.setenv("PRESEASON_ELITE_UNTIL", "2026-09-18T00:00:00Z")
    payload = promo.preseason_payload()
    assert payload["requires_payment"] is False
    assert payload["requires_stripe_trial"] is False
    assert payload["until"] == "2026-09-18T00:00:00+00:00"
