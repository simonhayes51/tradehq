"""Compact BIN-history loader for recommendation valuation.

The recommendation engine's BIN fallback (`futgg_bin_valuation.py`) does not
reason over five-minute observations directly. Its first step is to collapse
those observations into one median per hour. Loading every raw observation for
every candidate therefore transfers and materialises roughly 12x more rows
than the fallback can use at the normal five-minute bulk-price cadence.

This loader performs that exact hourly median reduction in PostgreSQL. It is
intentionally separate from `bin_market_signal.load_bin_observations`: market
state detection needs the raw sub-hour observations for its 30m/1h windows and
must not be silently degraded.

For small card sets, per-card LATERAL index probes are cheap and selective. For
scanner-sized batches, repeating the aggregate hundreds of times becomes the
dominant cost, so the loader switches to one set-based aggregate over the same
rows. Both paths produce identical evidence: same seven-day window, same hourly
bucket, same median and same bucket-centre timestamp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Sequence, Tuple

OBSERVATION_WINDOW_DAYS = 7
SET_BASED_BATCH_THRESHOLD = 128

# Small/fresh-detail path: one bounded index probe per card.
_HOURLY_VALUATION_LATERAL_QUERY = """
    SELECT h.source_card_id, h.lowest_bin, h.captured_at
    FROM unnest($1::bigint[]) AS ids(card_id)
    CROSS JOIN LATERAL (
        SELECT
            ids.card_id AS source_card_id,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY bh.lowest_bin) AS lowest_bin,
            date_trunc('hour', bh.captured_at) + interval '30 minutes' AS captured_at
        FROM futgg_bin_history bh
        WHERE bh.source_card_id = ids.card_id
          AND bh.captured_at >= now() - make_interval(days => $2)
          AND bh.lowest_bin > 0
        GROUP BY date_trunc('hour', bh.captured_at)
        ORDER BY date_trunc('hour', bh.captured_at) DESC
    ) h
"""

# Bulk/scanner path: aggregate the same qualifying observations once rather than
# executing hundreds of separate aggregate nodes. The composite covering index
# installed by migration 089 supports the card/time filter and supplies
# lowest_bin without changing the logical result.
_HOURLY_VALUATION_SET_QUERY = """
    SELECT
        bh.source_card_id,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY bh.lowest_bin) AS lowest_bin,
        date_trunc('hour', bh.captured_at) + interval '30 minutes' AS captured_at
    FROM futgg_bin_history bh
    WHERE bh.source_card_id = ANY($1::bigint[])
      AND bh.captured_at >= now() - make_interval(days => $2)
      AND bh.lowest_bin > 0
    GROUP BY bh.source_card_id, date_trunc('hour', bh.captured_at)
    ORDER BY bh.source_card_id, date_trunc('hour', bh.captured_at) DESC
"""


async def load_hourly_bin_valuation_observations(
    pool,
    card_ids: Sequence[int],
    *,
    days: int = OBSERVATION_WINDOW_DAYS,
) -> Dict[int, List[Tuple[int, datetime]]]:
    """Return hourly median BIN observations keyed by card id.

    The timestamp is the centre of the hour bucket. The valuation layer only
    uses these timestamps for bucket spacing/span and trend ordering, so both SQL
    plans preserve the exact same evidence model.
    """
    ids = sorted({int(card_id) for card_id in card_ids})
    if not ids:
        return {}

    query = (
        _HOURLY_VALUATION_SET_QUERY
        if len(ids) >= SET_BASED_BATCH_THRESHOLD
        else _HOURLY_VALUATION_LATERAL_QUERY
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, ids, max(1, int(days)))

    out: Dict[int, List[Tuple[int, datetime]]] = {}
    for row in rows:
        value = row["lowest_bin"]
        if value is None:
            continue
        out.setdefault(int(row["source_card_id"]), []).append(
            (int(round(float(value))), row["captured_at"])
        )
    return out
