# app/services/trade_diagnosis.py
#
# Why is the feed empty?
#
# That question has now taken several rounds of inferring deployment state
# from screenshots - which is the exact complaint app/why_no_trades.py
# opens with, written the last time it happened. The script answers it
# completely, but it needs shell access to production, and the person who
# needs the answer is usually looking at a phone.
#
# So the same analysis is available over HTTP, admin-only. It runs the
# REAL engine over the REAL candidate pool through the REAL provider
# query, so its verdict is what production actually produces rather than
# an approximation - the same discipline why_no_trades.py insists on, and
# for the same reason: a previous fix was verified against hand-built
# dicts and was inert in production for days.
#
# Read-only. It evaluates and counts; it writes nothing.
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("trade_diagnosis")

# The dashboard's own pool and ordering, so the diagnosis describes the
# cards the product actually scores rather than a different sample.
DEFAULT_LIMIT = 500
ORDER_BY = "sales_count DESC NULLS LAST"

# What each blocking code means and what to do about it. Kept beside the
# codes rather than in a runbook: the moment someone needs this, they are
# looking at the output, not searching a wiki.
GUIDANCE = {
    "stale_market": (
        "The BIN price is judged too old. Check the bulk price sync is running."
    ),
    "stale_sales": (
        "Sales data is older than the engine will accept. This is the sales "
        "collector, not the price collector - the two run separately, and a "
        "card can have a fresh price and dead sales."
    ),
    "insufficient_sales": (
        "Not enough completed sales to value the card at all. Either the sales "
        "collector is behind, or these cards genuinely do not trade."
    ),
    "excessive_dispersion": (
        "Sale prices are too scattered to call a fair value. Usually a real "
        "market condition rather than a fault."
    ),
    "price_above_max_buy": (
        "Cards are being valued, but nothing is cheap enough to buy. This is a "
        "quiet market, not a broken pipeline - the engine is working."
    ),
    "low_confidence": (
        "Confidence is below the floor for any signal. Confidence is a "
        "geometric blend, so ONE zero component zeroes the whole score - most "
        "often a zero sales count or sales older than the freshness limit."
    ),
}


async def diagnose(conn, provider, *, limit: int = DEFAULT_LIMIT, examples: int = 3) -> Dict[str, Any]:
    """Replay the engine over the live pool and tally what blocks it."""
    from app.services.futgg_intelligence import evaluate_card
    from app.services.market_data_provider import resolve_snapshot_columns

    # Through the provider's own column resolver, so this degrades on a
    # partially-migrated database exactly as production does.
    columns = await resolve_snapshot_columns(conn)
    rows = [
        dict(r)
        for r in await conn.fetch(
            f"""
            SELECT {columns} FROM futgg_market_snapshot
            WHERE is_tradeable IS DISTINCT FROM FALSE
            ORDER BY {ORDER_BY} LIMIT $1
            """,
            limit,
        )
    ]
    if not rows:
        return {
            "pool_size": 0,
            "verdict": "empty_snapshot",
            "summary": (
                "futgg_market_snapshot returned no tradeable rows at all. Nothing "
                "downstream can produce a trade. Check the snapshot worker."
            ),
            "signals": {},
            "blocking": [],
        }

    try:
        sales_by_card = await provider.get_sales_by_ids([int(r["source_card_id"]) for r in rows])
    except Exception:
        # Said explicitly rather than silently degrading: if the batch
        # sales fetch is what is broken, that IS the answer, and a
        # diagnosis that quietly evaluated without sales would blame the
        # market for a fetch failure.
        log.warning("trade diagnosis: batch sales fetch failed", exc_info=True)
        sales_by_card = {}

    as_of = datetime.now(timezone.utc)
    blocked: Counter = Counter()
    signals: Counter = Counter()
    samples: Dict[str, List[str]] = {}
    # The two inputs confidence is a geometric blend of, counted directly:
    # when everything is insufficient_data, knowing WHICH input is missing
    # is the whole diagnosis.
    no_sales = 0
    stale_sales = 0
    no_price_age = 0

    for row in rows:
        card_id = int(row["source_card_id"])
        ci = evaluate_card(row, as_of=as_of, sales=sales_by_card.get(card_id, []))
        signals[ci.signal] += 1
        if not (row.get("sales_count") or 0):
            no_sales += 1
        if row.get("price_updated_at") is None and row.get("bin_captured_at") is None:
            no_price_age += 1
        latest_sale = row.get("latest_sale_at")
        if latest_sale is not None:
            age_h = (as_of - latest_sale).total_seconds() / 3600
            if age_h > 72:
                stale_sales += 1
        for code in (ci.blocking_codes or []):
            blocked[code] += 1
            samples.setdefault(code, [])
            if len(samples[code]) < examples:
                samples[code].append(f"{row.get('name')} ({card_id})")

    total = len(rows)
    valued = total - signals.get("insufficient_data", 0)
    top_code, top_n = (blocked.most_common(1)[0] if blocked else (None, 0))

    return {
        "pool_size": total,
        "valued": valued,
        "valued_pct": round(100 * valued / total, 1),
        "signals": dict(signals.most_common()),
        "blocking": [
            {
                "code": code,
                "cards": n,
                "pct": round(100 * n / total, 1),
                "examples": samples.get(code, []),
                "guidance": GUIDANCE.get(code),
            }
            for code, n in blocked.most_common()
        ],
        "dominant_cause": top_code,
        "dominant_cause_cards": top_n,
        "guidance": GUIDANCE.get(top_code) if top_code else None,
        # The inputs, so "the sales collector is down" and "the market is
        # quiet" can be told apart at a glance rather than inferred from
        # a blocking code.
        "inputs": {
            "cards_with_no_sales": no_sales,
            "cards_with_sales_older_than_72h": stale_sales,
            "cards_with_no_price_timestamp": no_price_age,
        },
        "verdict": _verdict(total, valued, no_sales, top_code),
        "summary": _summary(total, valued, no_sales, stale_sales, top_code),
    }


def _verdict(total: int, valued: int, no_sales: int, top_code: Optional[str]) -> str:
    if valued == 0 and no_sales > total * 0.5:
        return "sales_collector"
    if valued == 0:
        return "engine_blocked"
    if top_code == "price_above_max_buy":
        return "quiet_market"
    return "partial"


def _summary(total: int, valued: int, no_sales: int, stale_sales: int, top_code: Optional[str]) -> str:
    if valued == 0 and no_sales > total * 0.5:
        return (
            f"{no_sales} of {total} cards have NO completed sales recorded. Confidence "
            "is a geometric blend, so a zero sales count zeroes the whole score and the "
            "card becomes insufficient_data. This is the sales collector, not the price "
            "collector - they run separately, and prices being fine says nothing about sales."
        )
    if valued == 0 and stale_sales > total * 0.5:
        return (
            f"{stale_sales} of {total} cards have sales older than the 72h freshness "
            "limit, which zeroes their confidence. The sales collector is running behind "
            "rather than stopped."
        )
    if valued == 0:
        return (
            f"No card in a pool of {total} could be valued, and it is not a missing-sales "
            f"problem. Dominant blocking code: {top_code}."
        )
    if top_code == "price_above_max_buy":
        return (
            f"{valued} of {total} cards are valued fine - nothing is cheap enough to buy "
            "right now. The engine is working; the market is quiet."
        )
    return f"{valued} of {total} cards valued. Dominant blocking code: {top_code}."
