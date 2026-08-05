# tests/test_priority_recompute.py
#
# Pure-function tests for app/services/priority_recompute.py's
# is_eligible_for_conservative_demotion() - the gate for Milestone 1.5's
# next_sales_due_at extension. No DB needed. The single most important
# invariant tested here: a never-scraped card is NEVER eligible, under
# any combination of other inputs - this is what preserves
# auto_sync/futgg_sales_sync.py's select_due() "never-scraped cards
# first" backfill rule.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.priority_config import PriorityEngineConfig
from app.services.priority_recompute import is_eligible_for_conservative_demotion

AS_OF = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
CONFIG = PriorityEngineConfig()

_ELIGIBLE_BASE = dict(
    card_type_component=0.05,  # plain bronze
    sales_count=0,
    watcher_count=0,
    has_active_trigger=False,
    first_seen_at=AS_OF - timedelta(days=365),
    next_sales_due_at=AS_OF - timedelta(hours=1),
    last_sales_status_at=AS_OF - timedelta(hours=2),
    as_of=AS_OF,
    config=CONFIG,
)


def test_baseline_eligible_case_passes():
    assert is_eligible_for_conservative_demotion(**_ELIGIBLE_BASE) is True


def test_never_scraped_card_is_never_eligible_regardless_of_other_inputs():
    # The one invariant that must never break: next_sales_due_at IS NULL
    # means "never read" per select_due()'s own definition.
    args = {**_ELIGIBLE_BASE, "next_sales_due_at": None}
    assert is_eligible_for_conservative_demotion(**args) is False

    args2 = {**_ELIGIBLE_BASE, "last_sales_status_at": None}
    assert is_eligible_for_conservative_demotion(**args2) is False


def test_active_trigger_blocks_eligibility():
    args = {**_ELIGIBLE_BASE, "has_active_trigger": True}
    assert is_eligible_for_conservative_demotion(**args) is False


def test_premium_card_type_blocks_eligibility():
    args = {**_ELIGIBLE_BASE, "card_type_component": 0.95}  # icon-level
    assert is_eligible_for_conservative_demotion(**args) is False


def test_any_recent_sales_blocks_eligibility():
    args = {**_ELIGIBLE_BASE, "sales_count": 1}
    assert is_eligible_for_conservative_demotion(**args) is False


def test_watcher_count_above_max_blocks_eligibility():
    args = {**_ELIGIBLE_BASE, "watcher_count": CONFIG.conservative_demotion_watcher_max + 1}
    assert is_eligible_for_conservative_demotion(**args) is False


def test_watcher_count_at_max_is_still_eligible():
    args = {**_ELIGIBLE_BASE, "watcher_count": CONFIG.conservative_demotion_watcher_max}
    assert is_eligible_for_conservative_demotion(**args) is True


def test_newly_released_card_is_not_eligible():
    args = {**_ELIGIBLE_BASE, "first_seen_at": AS_OF - timedelta(days=1)}
    assert is_eligible_for_conservative_demotion(**args) is False


def test_unknown_first_seen_at_is_not_eligible():
    args = {**_ELIGIBLE_BASE, "first_seen_at": None}
    assert is_eligible_for_conservative_demotion(**args) is False


def test_card_exactly_at_the_minimum_age_boundary_is_eligible():
    args = {**_ELIGIBLE_BASE, "first_seen_at": AS_OF - timedelta(days=CONFIG.conservative_demotion_min_card_age_days)}
    assert is_eligible_for_conservative_demotion(**args) is True
