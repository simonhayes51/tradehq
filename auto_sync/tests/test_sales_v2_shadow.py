"""Pure scheduler and capacity tests. No database or network needed."""
from datetime import datetime, timedelta, timezone

from sales_v2_shadow import capacity_summary, select_candidates


NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def card(card_id, price, *, first=None, status=None, checked=None,
         due=None, tradeable=None, failures=0):
    return dict(source_card_id=card_id, current_bin=price,
                sales_first_read_at=first, last_sales_status=status,
                last_sales_status_at=checked, next_sales_due_at=due,
                is_tradeable=tradeable, sales_consecutive_failures=failures)


def test_eligibility_excludes_cheap_and_confirmed_untradeable():
    result = select_candidates([
        card(1, 4999),
        card(2, 100000, tradeable=False),
        card(3, 5000),
    ], NOW)
    assert [r["source_card_id"] for r in result] == [3]


def test_respects_failed_attempt_backoff():
    result = select_candidates([
        card(1, 100000, due=NOW + timedelta(hours=2), failures=5),
        card(2, 20000, due=NOW - timedelta(minutes=1)),
    ], NOW)
    assert [r["source_card_id"] for r in result] == [2]


def test_old_sales_are_not_a_new_success_after_failure():
    result = select_candidates([
        card(1, 25000, first=NOW - timedelta(days=2),
             status="429", checked=NOW - timedelta(minutes=10)),
    ], NOW)
    assert result[0]["last_verifiable_success"] is None


def test_fairness_first_reads_before_rechecks():
    result = select_candidates([
        card(1, 150000, first=NOW - timedelta(days=1),
             status="success", checked=NOW - timedelta(hours=10)),
        card(2, 6000),
    ], NOW)
    assert result[0]["source_card_id"] == 2


def test_capacity_math_does_not_claim_hourly_is_possible():
    result = capacity_summary(1296, 480, 60)
    assert result["required_checks_per_day"] == 31104
    assert result["required_per_minute"] == 21.6
    assert result["optimistic_refresh_hours"] == 64.8
    assert result["hourly_feasible_even_if_all_attempts_succeed"] is False
