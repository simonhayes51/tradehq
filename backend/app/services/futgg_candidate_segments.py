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
        name="liquid",
        where=f"sales_count >= {LIQUID_MIN_SALES}",
        order_by="sales_count DESC NULLS LAST",
        # 200 (the original default) is what let Donnarumma/Courtois-style
        # cards rank outside every segment - fixed by raising this to 400
        # and, separately, by the sample-size-weighted ordering on
        # "recently_moved" below. But evaluating ~1700 candidates per pass
        # costs real time (evaluate_card() is plain synchronous CPU work,
        # ~15-20s for that many cards - confirmed in production), and
        # excluding silver/bronze (see market_data_provider.py's
        # EXCLUDED_PRICE_TIERS) barely reduced the actual evaluated count,
        # because segments fill to their LIMIT from whatever pool remains
        # regardless of how large that pool is. 250 is the compromise: still
        # meaningfully above the value that missed real signals, while the
        # ranking fix (not the limit) is what mainly keeps the best
        # candidates from falling out now.
        limit=_env_int("FUTGG_SEG_LIQUID_LIMIT", 250),
        refresh_minutes=_env_int("FUTGG_SEG_LIQUID_REFRESH_MIN", 15),
        description="Highly liquid cards - fast, efficient markets.",
    ),
    Segment(
        name="medium_liquidity",
        where=f"sales_count >= {MEDIUM_MIN_SALES} AND sales_count < {LIQUID_MIN_SALES}",
        order_by="sales_count DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_MEDIUM_LIMIT", 250),
        refresh_minutes=_env_int("FUTGG_SEG_MEDIUM_REFRESH_MIN", 45),
        description="Mid-liquidity cards.",
    ),
    Segment(
        name="low_liquidity",
        where=f"sales_count > 0 AND sales_count < {MEDIUM_MIN_SALES}",
        # Ordered by recency of the last sale rather than by count: within
        # the tail, "traded recently" is far more informative than
        # "traded slightly more often".
        order_by="latest_sale_at DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_LOW_LIMIT", 250),
        refresh_minutes=_env_int("FUTGG_SEG_LOW_REFRESH_MIN", 180),
        description="Long-tail cards - thin markets where mispricing persists.",
    ),
    Segment(
        name="recently_active",
        where="latest_sale_at IS NOT NULL AND latest_sale_at >= now() - interval '2 hours'",
        order_by="latest_sale_at DESC NULLS LAST",
        limit=_env_int("FUTGG_SEG_ACTIVE_LIMIT", 200),
        refresh_minutes=_env_int("FUTGG_SEG_ACTIVE_REFRESH_MIN", 20),
        description="Cards that have traded in the last two hours.",
    ),
    Segment(
        name="recently_moved",
        # A BIN materially away from the recent-sales median is either an
        # opportunity or a trend - both are worth an evaluation. The
        # sales_count floor keeps single-sale noise from crowding out
        # well-sampled moves in the ranking (see MOVED_MIN_SALES above).
        where=(
            f"current_bin IS NOT NULL AND sales_median IS NOT NULL AND sales_median > 0 "
            f"AND sales_count >= {MOVED_MIN_SALES} "
            f"AND abs(current_bin - sales_median) / sales_median >= 0.08"
        ),
        # Raw deviation % alone still crowds out well-evidenced moves: a
        # thin card with 6 sales and a wild, noisy 60% swing outranks a
        # card with 50 real sales and a genuine 19% swing, even after the
        # sales_count floor above (that floor removes single-sale noise,
        # it does not stop a 6-24-sale card from producing a larger, less
        # reliable ratio than a 50-sale one). Confirmed in production
        # twice - card_ids 50562269 (50 sales, 22.8% gap) and 192119 (50
        # sales, 18.9% gap) both evaluated to a live buy/strong_buy signal
        # on their own /players/{id} lookup and both still failed to rank
        # into this segment's top LIMIT rows after the sales_count floor
        # alone. Weighting the ratio by sample size (capped at 50 - the
        # same order of magnitude as LIQUID_MIN_SALES's idea of "enough
        # evidence to trust") ranks well-sampled real moves above thin,
        # noisy ones without touching evaluate_card()'s own gates at all.
        order_by=(
            "(abs(current_bin - sales_median) / NULLIF(sales_median, 0)) "
            "* least(sales_count, 50) DESC"
        ),
        # See "liquid" segment's own limit comment above - same trade-off,
        # same reasoning: the ordering fix above is what actually protects
        # Donnarumma/Courtois-style cards now, not a large limit.
        limit=_env_int("FUTGG_SEG_MOVED_LIMIT", 250),
        refresh_minutes=_env_int("FUTGG_SEG_MOVED_REFRESH_MIN", 20),
        description="Cards whose live price has moved away from recent sales.",
    ),
    Segment(
        name="near_threshold",
        # Sitting just above where a buy would trigger. These are the
        # cards where a small move creates an opportunity, so they deserve
        # frequent re-checking even though they are not actionable yet.
        where=(
            "current_bin IS NOT NULL AND sales_median IS NOT NULL AND sales_median > 0 "
            "AND current_bin > sales_median * 0.88 AND current_bin <= sales_median * 1.02"
        ),
        order_by="current_bin / NULLIF(sales_median, 0) ASC",
        limit=_env_int("FUTGG_SEG_NEAR_LIMIT", 150),
        refresh_minutes=_env_int("FUTGG_SEG_NEAR_REFRESH_MIN", 25),
        description="Cards close to becoming a buy - a small move makes them actionable.",
    ),
    Segment(
        name="never_evaluated",
        where="TRUE",
        # The scanner supplies last-evaluated times; ordering by price
        # staleness here approximates "least recently looked at" without
        # needing a join against the snapshot table in the candidate
        # query itself.
        order_by="price_updated_at ASC NULLS FIRST",
        limit=_env_int("FUTGG_SEG_NEVER_LIMIT", 150),
        refresh_minutes=_env_int("FUTGG_SEG_NEVER_REFRESH_MIN", 360),
        description="Cards with no recent evaluation on record.",
    ),
]

SEGMENTS_BY_NAME: Dict[str, Segment] = {s.name: s for s in SEGMENTS}

# Hard ceiling on a single pass regardless of per-segment limits, so a
# misconfigured environment cannot turn one scan into a full-table walk.
# Lowered back down alongside the liquid/recently_moved limit reductions
# above (default per-segment limits now sum to 1500) - see those
# segments' own comments for why evaluation volume, not just candidate
# pool composition, needed to come back down.
MAX_TOTAL_CANDIDATES = _env_int("FUTGG_SEG_MAX_TOTAL", 1800)


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
