# app/services/futgg_candidate_segments.py
"""
Segmented candidate selection for the FUT.GG evaluation pass.

THE PROBLEM THIS REPLACES
-------------------------
Every list endpoint scanned candidates with a single query:

    ORDER BY sales_count DESC LIMIT 500

which means the engine only ever considered the 500 most-traded cards in
the game. Those are precisely the most efficiently priced and most
heavily botted markets there are - the places where an edge is least
likely to survive long enough for a human to act on it. Meanwhile the
long tail (obscure cards, thin markets, players nobody is watching) -
where genuine, persistent mispricing actually lives - was structurally
invisible. Not under-weighted: never scanned at all, with no symptom
other than "why does this card never show up".

It also starved itself over time: the same top-500 cards won the ordering
every single pass, so nothing outside that set was ever re-evaluated.

THE APPROACH
------------
Split the scan budget across named segments with independent limits and
refresh intervals, so high-volume cards cannot consume the whole
allocation. Each segment answers a different question:

  liquid          - the fast, efficient markets (still worth watching)
  medium_liquidity- the middle of the distribution
  low_liquidity   - the long tail, where mispricing persists
  recently_active - cards that have just traded
  recently_moved  - cards whose BIN has moved materially
  near_threshold  - cards sitting just above a buy trigger, so a small
                    move makes them actionable and we want to catch it
  never_evaluated - cards with no evaluation on record, or the stalest

Segments overlap by design; the caller de-duplicates by card id. Overlap
is a feature - a card qualifying under several segments is genuinely more
interesting than one qualifying under a single segment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


@dataclass(frozen=True)
class Segment:
    name: str
    #: Extra SQL predicate, ANDed onto the base filter. Static strings
    #: only - never built from user input.
    where: str
    order_by: str
    limit: int
    #: How often a card in this segment should be re-evaluated. Used by
    #: the scanner to skip cards evaluated more recently than this.
    refresh_minutes: int
    description: str


# Liquidity boundaries, expressed in sales within the snapshot's bounded
# recent window. Deliberately generous at the bottom: a card with three
# recent sales is exactly the kind of thin market the old scan ignored,
# and while it will usually fail the engine's own min_sales gate, it must
# at least be *looked at* so that gate is what rejects it rather than the
# candidate query silently never surfacing it.
LIQUID_MIN_SALES = _env_int("FUTGG_SEG_LIQUID_MIN_SALES", 25)
MEDIUM_MIN_SALES = _env_int("FUTGG_SEG_MEDIUM_MIN_SALES", 8)
# "recently_moved" ranks purely by deviation magnitude with no liquidity
# floor at all, so a card with one or two noisy/outlier sales (a trivially
# huge, meaningless deviation ratio) can rank ahead of - and crowd out -a
# card with real, well-sampled evidence behind a smaller but genuine move.
# Confirmed in production: a 89-rated card with 50 sales and a real 22.8%
# BIN-vs-median gap (a live strong_buy on its own /players/{id} lookup)
# never appeared in this segment's top LIMIT rows at all. Aligned with
# futgg_config.py's min_sales_for_signal (5): below that, evaluate_card()
# cannot produce a completed-sales signal for the card anyway, so this
# floor only removes rows this segment could never usefully rank high -
# thin cards below it are still reachable via low_liquidity/never_evaluated.
MOVED_MIN_SALES = _env_int("FUTGG_SEG_MOVED_MIN_SALES", 5)

SEGMENTS: List[Segment] = [
    Segment(
        name="special_price",
        where="price_tier = 'special' AND current_bin IS NOT NULL AND bin_captured_at IS NOT NULL",
        order_by="bin_captured_at DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_SPECIAL_LIMIT", 300),
        refresh_minutes=_env_int("FUTGG_SEG_SPECIAL_REFRESH_MIN", 10),
        description="Fresh BIN history for special cards.",
    ),
    Segment(
        name="gold_rare_price",
        where="price_tier = 'gold_rare' AND current_bin IS NOT NULL AND bin_captured_at IS NOT NULL",
        order_by="bin_captured_at DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_GOLD_RARE_LIMIT", 500),
        refresh_minutes=_env_int("FUTGG_SEG_GOLD_RARE_REFRESH_MIN", 15),
        description="Fresh BIN history for gold rare cards.",
    ),
    Segment(
        name="other_price",
        where="price_tier IS DISTINCT FROM 'special' AND price_tier IS DISTINCT FROM 'gold_rare' "
              "AND current_bin IS NOT NULL AND bin_captured_at IS NOT NULL",
        order_by="bin_captured_at DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_OTHER_PRICE_LIMIT", 500),
        refresh_minutes=_env_int("FUTGG_SEG_OTHER_PRICE_REFRESH_MIN", 45),
        description="Fresh BIN history for all other tradeable cards.",
    ),
    Segment(
        name="price_history_rotation",
        where="current_bin IS NOT NULL AND bin_captured_at IS NOT NULL",
        order_by="price_updated_at ASC NULLS FIRST",
        limit=_env_int("FUTGG_SEG_PRICE_ROTATION_LIMIT", 200),
        refresh_minutes=_env_int("FUTGG_SEG_PRICE_ROTATION_REFRESH_MIN", 360),
        description="Rotation pass across cards with price observations.",
    ),
]

SEGMENTS_BY_NAME: Dict[str, Segment] = {s.name: s for s in SEGMENTS}

# Hard ceiling on a single pass regardless of per-segment limits, so a
# misconfigured environment cannot turn one scan into a full-table walk.
# Lowered back down alongside the liquid/recently_moved limit reductions
# above (default per-segment limits now sum to 1400, the original total
# before any of this) - see those segments' own comments for why
# evaluation volume, not just candidate pool composition, needed to come
# back down once the BIN-history fallback made each evaluation heavier.
MAX_TOTAL_CANDIDATES = _env_int("FUTGG_SEG_MAX_TOTAL", 1500)


def segment_summary() -> List[Dict[str, Any]]:
    """Configuration echo, for the metrics endpoint."""
    return [
        {
            "name": s.name,
            "limit": s.limit,
            "refresh_minutes": s.refresh_minutes,
            "description": s.description,
        }
        for s in SEGMENTS
    ]
