# app/services/free_picks.py
#
# Chooses the complete opportunities a Free member gets each FC content
# day, and makes that choice stable.
#
# Three properties matter, in this order:
#
#   1. EVERY Free member sees the SAME picks on a given content day. The
#      alternative - "whatever the first two rows of page 1 happen to be" -
#      means the free tier's value depends on when you loaded the page, and
#      two users comparing notes see different products.
#   2. A pick stays unlocked for the rest of the day once the day has been
#      decided, even if the live ranking moves. Hence persistence
#      (migration 073) rather than re-deriving on every request: the
#      candidate pool is live, so re-deriving would let a card disappear
#      from under a user who had already been shown it.
#   3. The picks are defensible on their own terms - not stale, not
#      low-confidence, not two versions of the same footballer, and
#      spread across budgets so the free tier isn't two 900k cards nobody
#      reading it can afford.
#
# The selection is deterministic given (content day, candidate pool): the
# ranking is a pure function and the tie-break is seeded by the content-day
# key, so a fresh database re-deriving a past day lands on the same answer
# the persisted row would have given, as long as the pool is the same.
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.auth.entitlements import FREE_DAILY_PICKS
from app.services.content_day import (
    content_day_key,
    next_content_day_start,
    seconds_until_next_content_day,
)

log = logging.getLogger("free_picks")

SELECTOR_VERSION = "free-picks-1"

# A free pick is a recommendation we are putting in front of someone who
# has never paid us anything. The bar is deliberately higher than the
# paid feed's, not lower.
MIN_CONFIDENCE = 0.55
MAX_PRICE_AGE_MINUTES = 90
MIN_RECENT_SALES = 5

# Budget bands, so the two picks aren't both unaffordable. Bands are in
# coins and describe where a card's current BIN sits.
BUDGET_BANDS: Sequence[Tuple[str, int]] = (
    ("budget", 25_000),
    ("mid", 150_000),
    ("premium", 10**12),
)


def budget_band(current_bin: Optional[float]) -> str:
    if current_bin is None:
        return "unknown"
    for name, ceiling in BUDGET_BANDS:
        if float(current_bin) <= ceiling:
            return name
    return "premium"


def _is_defensible(row: Dict[str, Any]) -> bool:
    """Would we stand behind showing this to a non-paying stranger?"""
    if str(row.get("signal") or "").lower() not in ("buy", "strong_buy"):
        return False
    confidence = row.get("confidence_score")
    if confidence is None or float(confidence) < MIN_CONFIDENCE:
        return False
    age = row.get("price_age_minutes")
    if age is None or float(age) > MAX_PRICE_AGE_MINUTES:
        return False
    if int(row.get("recent_sale_count") or 0) < MIN_RECENT_SALES:
        return False
    # A pick with no entry or no target is not a complete opportunity, and
    # "here is a card, work the rest out yourself" is not the free offer.
    if row.get("recommended_buy_max") is None or row.get("recommended_sell_target") is None:
        return False
    if row.get("expected_profit_after_tax") is None:
        return False
    return True


def _player_identity(row: Dict[str, Any]) -> str:
    """What counts as "the same player" for de-duplication.

    Name, not card id: the point is to avoid handing out two versions of
    one footballer as if they were two independent ideas.
    """
    name = str(row.get("display_name") or row.get("name") or "").strip().lower()
    return name or f"card:{row.get('source_card_id')}"


def _tie_break(day_key: date, row: Dict[str, Any]) -> str:
    """Stable, day-seeded ordering for candidates that score identically.

    Seeded by the content day so the arbitrary part of the order is at
    least different from day to day rather than always favouring the same
    corner of the catalogue, and hashed so it cannot be gamed by anyone
    choosing a card id.
    """
    return hashlib.sha256(f"{day_key.isoformat()}:{row.get('source_card_id')}".encode()).hexdigest()


def rank_candidates(rows: Sequence[Dict[str, Any]], day_key: date) -> List[Dict[str, Any]]:
    """Defensible candidates, best first. Pure - no I/O, no clock."""
    defensible = [row for row in rows if _is_defensible(row)]
    return sorted(
        defensible,
        key=lambda row: (
            # Confidence first: a free pick is a promise, and being right
            # matters more than being spectacular.
            -float(row.get("confidence_score") or 0),
            -float(row.get("expected_roi") or 0),
            -float(row.get("expected_profit_after_tax") or 0),
            _tie_break(day_key, row),
        ),
    )


def select_picks(
    rows: Sequence[Dict[str, Any]],
    day_key: date,
    limit: int = FREE_DAILY_PICKS,
) -> List[Dict[str, Any]]:
    """The day's picks, in display order. Pure.

    Diversity is a PREFERENCE, never a reason to return fewer picks than
    the pool can honestly support: the first pass takes the best candidate
    per (player, budget band), and a second pass backfills from what is
    left if that wasn't enough. What it will never do is invent a pick -
    if only one card is defensible today, one is what comes back, and the
    caller says so plainly.
    """
    ranked = rank_candidates(rows, day_key)
    picked: List[Dict[str, Any]] = []
    seen_players: set[str] = set()
    seen_bands: set[str] = set()

    for row in ranked:
        if len(picked) >= limit:
            break
        player = _player_identity(row)
        band = budget_band(row.get("current_bin"))
        if player in seen_players or band in seen_bands:
            continue
        picked.append(row)
        seen_players.add(player)
        seen_bands.add(band)

    if len(picked) < limit:
        # Backfill: relax the budget-band preference but never the
        # one-version-per-player rule, which exists to stop the free tier
        # being the same footballer twice.
        for row in ranked:
            if len(picked) >= limit:
                break
            player = _player_identity(row)
            if player in seen_players:
                continue
            picked.append(row)
            seen_players.add(player)

    return picked


def selection_reason(row: Dict[str, Any]) -> str:
    band = budget_band(row.get("current_bin"))
    return f"{band} budget band, confidence {float(row.get('confidence_score') or 0):.2f}"


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

_SELECT_DAY = """
    SELECT slot, source_card_id, selection_reason, selected_signal, selected_bin
    FROM free_daily_picks
    WHERE content_day = $1
    ORDER BY slot
"""

# ON CONFLICT DO NOTHING, not an upsert. Two requests racing on the first
# hit of a new content day must not produce two different answers: the
# first writer wins and the second reads what the first wrote. Overwriting
# would let the second request quietly change a pick a user had already
# been shown.
_INSERT_PICK = """
    INSERT INTO free_daily_picks
        (content_day, slot, source_card_id, selection_reason,
         selected_signal, selected_bin, selector_version)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (content_day, slot) DO NOTHING
"""


async def stored_pick_ids(pool, day_key: date) -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_DAY, day_key)
    return [int(r["source_card_id"]) for r in rows]


async def resolve_pick_ids(
    candidates: "Callable[[], Sequence[Dict[str, Any]]]",
    now: Optional[datetime] = None,
    pool=None,
) -> List[int]:
    """The card ids a Free member may see in full today.

    Reads the persisted choice if there is one; otherwise selects, writes,
    and re-reads so a race resolves to whatever actually landed in the
    table rather than to this caller's own local decision.

    `candidates` is a CALLABLE, not a list. On all but the first request of
    a content day the answer is already in the table, and building the full
    scored candidate pool into serialisable dicts just to throw it away
    would put that cost on every single request of the day. This way the
    pool is materialised only when a selection actually has to be made.

    A database failure must not take the free tier down - it degrades to
    an in-memory selection for this request, which is still deterministic
    for the day, just not pinned.
    """
    day_key = content_day_key(now)
    try:
        if pool is None:
            from app.db import get_core_pool

            pool = await get_core_pool()
        existing = await stored_pick_ids(pool, day_key)
        if existing:
            return existing
    except Exception:
        log.warning("free picks: could not read stored picks; selecting live", exc_info=True)
        return [int(r["source_card_id"]) for r in select_picks(candidates(), day_key)]

    rows = candidates()

    chosen = select_picks(rows, day_key)
    if not chosen:
        return []

    try:
        async with pool.acquire() as conn:
            for slot, row in enumerate(chosen):
                await conn.execute(
                    _INSERT_PICK, day_key, slot, int(row["source_card_id"]),
                    selection_reason(row),
                    str(row.get("signal") or ""),
                    int(row["current_bin"]) if row.get("current_bin") is not None else None,
                    SELECTOR_VERSION,
                )
        return await stored_pick_ids(pool, day_key)
    except Exception:
        log.warning("free picks: could not persist picks; using live selection", exc_info=True)
        return [int(r["source_card_id"]) for r in chosen]


def free_pick_meta(now: Optional[datetime] = None, unlocked: int = 0) -> Dict[str, Any]:
    """What the UI needs to explain the free allowance honestly."""
    return {
        "allowance": FREE_DAILY_PICKS,
        "unlocked": unlocked,
        "contentDay": content_day_key(now).isoformat(),
        "nextRefreshAt": next_content_day_start(now).isoformat(),
        "secondsUntilRefresh": seconds_until_next_content_day(now),
        # Said plainly so the frontend never has to guess whether a short
        # list means "we're hiding some" or "there weren't any more".
        "fewerThanAllowance": unlocked < FREE_DAILY_PICKS,
    }
