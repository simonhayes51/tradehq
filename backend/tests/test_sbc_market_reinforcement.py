from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services import sbc_market_reinforcement as reinforcement


def run(coro):
    return asyncio.run(coro)


def test_resource_filter_uses_real_futgg_rarity_vocabulary():
    assert reinforcement._resource_filter("TOTW", "totw") == (
        "lower(COALESCE(m.rarity, '')) LIKE $1", ["%team of the week%"]
    )
    assert reinforcement._resource_filter("TOTS", "tots") == (
        "lower(COALESCE(m.rarity, '')) LIKE $1", ["%team of the season%"]
    )
    assert reinforcement._resource_filter("MIN_TEAM_RATING", "89") is None
    assert reinforcement._resource_filter("PROMO_TYPE", "fut_birthday") is None


def test_confirmation_needs_price_and_sales_activity():
    base = {"matched_cards": 40, "baseline_cards": 35, "current_cards": 38, "pending": False}
    status, _ = reinforcement.classify_shadow_reinforcement({**base, "price_change_pct": 4.2, "sales_activity_ratio": 1.35})
    assert status == "MARKET_CONFIRMED_CANDIDATE"

    status, _ = reinforcement.classify_shadow_reinforcement({**base, "price_change_pct": 4.2, "sales_activity_ratio": 1.05})
    assert status == "PRICE_ONLY"

    status, _ = reinforcement.classify_shadow_reinforcement({**base, "price_change_pct": -4.0, "sales_activity_ratio": 1.5})
    assert status == "CONFLICTING"


def test_new_event_is_pending_without_hitting_raw_history_queries():
    class Conn:
        def __init__(self): self.calls = 0
        async def fetchrow(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("raw history should not be queried while event is immature")

    conn = Conn()
    evidence = run(reinforcement._segment_evidence(
        conn,
        list(range(20)),
        datetime.now(timezone.utc) - timedelta(minutes=30),
        window_hours=2,
    ))
    assert evidence["pending"] is True
    assert conn.calls == 0
    assert reinforcement.classify_shadow_reinforcement(evidence)[0] == "PENDING"


def test_history_queries_are_bounded_to_baseline_or_current_windows():
    class Conn:
        def __init__(self): self.queries = []
        async def fetchrow(self, query, *args):
            self.queries.append(query)
            if "futgg_bin_history" in query:
                return {"baseline_cards": 20, "current_cards": 20, "baseline_bin": 1000, "current_bin": 1040}
            return {"baseline_sales": 20, "current_sales": 30}

    conn = Conn()
    run(reinforcement._segment_evidence(
        conn,
        list(range(20)),
        datetime.now(timezone.utc) - timedelta(hours=12),
    ))
    assert len(conn.queries) == 2
    for query in conn.queries:
        assert " OR " in query
        assert "LEAST(" not in query


def test_module_is_read_only_by_structure():
    from pathlib import Path
    source = Path("app/services/sbc_market_reinforcement.py").read_text().lower()
    assert "update sbc_resource_demand" not in source
    assert "insert into sbc_resource_demand" not in source
    assert '"writes_market_status":false' in source.replace(" ", "")
    assert '"changes_priority_weight":false' in source.replace(" ", "")
