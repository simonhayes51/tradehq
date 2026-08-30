# app/services/alerts_feed.py
#
# One place that turns rows of `alerts_log` (migration 016 + 072) into the
# customer-facing alert feed, so Home's bell/feed and the Alerts panel can
# never disagree about what an alert says or how many are unread.
#
# Before this module existed, app/routers/v2/dashboard.py built alert copy
# inline and nothing else could. Three separate defects came out of that:
#
#   * Raw ids leaked into user-facing text ("Card 117493814 is down 13%").
#     The name lookup guessed a card's catalogue from a LEFT JOIN onto
#     `watchlist`, which is a table the user can empty at any time - remove
#     the card and the join returns NULL, the lookup goes to the wrong
#     catalogue, and the id is printed instead of a name. alerts_log now
#     stores its own `source` (migration 072), so resolution no longer
#     depends on state the user can change after the fact.
#
#   * The bell badge counted "the 5 most recent rows", which is not a count
#     of anything: it never went down, and never matched the panel.
#
#   * Repeated firings on the same card flooded the feed. A rule with a
#     30-minute cooloff on a card that keeps drifting produces the same
#     alert all day; each one was a separate row in the feed.
#
# The formatting half (_describe / build_alert_item) is deliberately pure
# so it can be tested without a database.
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger("alerts_feed")

# Movement at or above this is called out as significant in the "why this
# matters" line. Below it the copy stays deliberately flat - a 6% drift on
# a card that routinely swings 15% is not news, and saying so is more
# useful than dressing every alert up as urgent.
SIGNIFICANT_MOVE_PCT = 15.0

# Repeat collapsing: alerts for the same card AND direction inside this
# window are shown as one entry carrying a repeat count, newest first.
# Wide enough to absorb a rule cycling on its 30-minute cooloff for a few
# hours; short enough that a genuinely new move the next day is its own
# entry.
REPEAT_WINDOW_HOURS = 12

DEFAULT_FEED_LIMIT = 20


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def age_label(sent_at: Optional[datetime], *, now: Optional[datetime] = None) -> str:
    """Human "when did this happen" - alerts are only useful if you can
    tell a price move from ten minutes ago apart from one from Tuesday."""
    moment = _aware(sent_at)
    if moment is None:
        return "Just now"
    reference = now or datetime.now(timezone.utc)
    seconds = (reference - moment).total_seconds()
    if seconds < 90:
        return "Just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} min ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = int(hours / 24)
    return "Yesterday" if days == 1 else f"{days} days ago"


def card_label(name: Optional[str], rating: Optional[int], version: Optional[str]) -> str:
    """"Mbappé (91 Team of the Week)" - never a bare id. `version` arrives
    as a raw rarity key from the snapshot ('team_of_the_week'), which is
    internal vocabulary, so it is humanised here rather than printed."""
    display = (name or "").strip()
    if not display:
        return "A watched card"
    bits = []
    if rating is not None:
        bits.append(str(int(rating)))
    if version:
        pretty = str(version).replace("_", " ").strip()
        if pretty and pretty.lower() not in ("card", "unknown"):
            bits.append(pretty.title())
    return f"{display} ({' '.join(bits)})" if bits else display


def _why_it_matters(direction: str, pct: float, metric: str) -> str:
    """The point of an alert is the decision it implies, not the number.
    Deliberately hedged: this is a price movement notification, not a
    recommendation - the engine's actual BUY/AVOID verdict lives on the
    card's own page, and saying more than the data supports here would be
    the same overclaiming the rest of this codebase exists to avoid."""
    size = abs(pct)
    if metric == "liquidity":
        if direction == "rise":
            return "More cards are selling than when you set this alert, so it should be easier to move at your price."
        return "Fewer cards are selling than when you set this alert, so it may take longer to sell."
    if direction == "rise":
        if size >= SIGNIFICANT_MOVE_PCT:
            return "A move this size is usually worth checking whether your exit is now on the table."
        return "It's drifted up since you started watching it."
    if size >= SIGNIFICANT_MOVE_PCT:
        return "A drop this size is either an entry or a card the market is repricing - check the sales before buying."
    return "It's drifted down since you started watching it."


def build_alert_item(
    row: Dict[str, Any],
    *,
    card: Optional[Dict[str, Any]] = None,
    repeat_count: int = 1,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pure row -> feed item. `card` is whatever the catalogue lookup found
    (name/rating/version), or None when it found nothing - in which case
    the copy degrades to "A watched card", never to the raw id."""
    card = card or {}
    pct = float(row["pct"] or 0)
    direction = "rise" if str(row.get("direction")) == "rise" else "fall"
    metric = "liquidity" if str(row.get("metric") or "price") == "liquidity" else "price"
    label = card_label(card.get("name"), card.get("rating"), card.get("version"))
    direction_word = "up" if direction == "rise" else "down"
    noun = "sales activity" if metric == "liquidity" else "price"

    price = row.get("price")
    ref_price = row.get("ref_price")
    if metric == "price" and price is not None:
        if ref_price:
            movement = f"{int(float(ref_price)):,} → {int(float(price)):,} coins"
        else:
            movement = f"Now {int(float(price)):,} coins"
    elif price is not None:
        movement = f"{float(price):.1f} sales/hour now"
    else:
        movement = ""

    return {
        "id": int(row["id"]),
        "cardId": int(row["card_id"]),
        "source": row.get("source") or "futbin",
        "platform": (row.get("platform") or "ps"),
        "player": {
            "name": card.get("name"),
            "rating": card.get("rating"),
            "version": card.get("version"),
            "imageUrl": card.get("image_url"),
        },
        # Resolved, human title. This is the line that used to read
        # "Card 117493814 is down 13%".
        "title": f"{label} is {direction_word} {abs(pct):.1f}% ({noun})",
        "movement": movement,
        "whyItMatters": _why_it_matters(direction, pct, metric),
        "direction": direction,
        "metric": metric,
        "changePct": round(pct, 1),
        "severity": "high" if abs(pct) >= SIGNIFICANT_MOVE_PCT else "medium",
        "sentAt": _aware(row.get("sent_at")).isoformat() if row.get("sent_at") else None,
        "age": age_label(row.get("sent_at"), now=now),
        "unread": row.get("read_at") is None,
        # >1 means "this rule fired N times on this card recently"; the UI
        # shows one row and says so, instead of N near-identical rows.
        "repeatCount": repeat_count,
        "href": f"/v2/players/{int(row['card_id'])}",
    }


def collapse_repeats(rows: Sequence[Dict[str, Any]], *, window_hours: int = REPEAT_WINDOW_HOURS) -> List[tuple]:
    """(newest_row, repeat_count) per (card_id, direction, metric) group,
    input order preserved. Rows are expected newest-first."""
    out: List[tuple] = []
    index: Dict[tuple, int] = {}
    for row in rows:
        key = (int(row["card_id"]), str(row.get("direction")), str(row.get("metric") or "price"))
        position = index.get(key)
        if position is None:
            index[key] = len(out)
            out.append((row, 1))
            continue
        head, count = out[position]
        head_at = _aware(head.get("sent_at"))
        row_at = _aware(row.get("sent_at"))
        if head_at and row_at and (head_at - row_at).total_seconds() > window_hours * 3600:
            # Old enough to be a genuinely separate episode - start a new
            # group rather than inflating the count of the current one.
            index[key] = len(out)
            out.append((row, 1))
            continue
        out[position] = (head, count + 1)
    return out


_SELECT_LIVE_ALERTS = """
    SELECT al.id, al.card_id, al.platform, al.direction, al.pct, al.price,
           al.ref_price, al.sent_at, al.read_at, al.source,
           COALESCE(wa.metric, 'price') AS metric
    FROM alerts_log al
    LEFT JOIN watchlist_alerts wa
           ON wa.user_id = al.user_id AND wa.card_id = al.card_id
          AND wa.platform = al.platform
    WHERE al.user_id = $1 AND al.dismissed_at IS NULL
    ORDER BY al.sent_at DESC
    LIMIT $2
"""


async def _resolve_cards(core_pool, player_pool, rows: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """One batched lookup per catalogue, not one per alert - the previous
    inline version issued a query per row inside the request handler."""
    futgg_ids = sorted({int(r["card_id"]) for r in rows if (r.get("source") or "futbin") == "futgg"})
    futbin_ids = sorted({int(r["card_id"]) for r in rows if (r.get("source") or "futbin") != "futgg"})
    resolved: Dict[int, Dict[str, Any]] = {}

    if futgg_ids and core_pool is not None:
        try:
            async with core_pool.acquire() as conn:
                found = await conn.fetch(
                    """SELECT source_card_id,
                              COALESCE(display_name, name) AS name,
                              rating, rarity,
                              COALESCE(card_image_stored_url, card_image_url, player_image_url) AS image_url
                       FROM futgg_market_snapshot WHERE source_card_id = ANY($1::bigint[])""",
                    futgg_ids,
                )
            for r in found:
                resolved[int(r["source_card_id"])] = {
                    "name": r["name"], "rating": r["rating"],
                    "version": r["rarity"], "image_url": r["image_url"],
                }
        except Exception:
            log.warning("alerts feed: FUT.GG card resolution failed", exc_info=True)

    if futbin_ids and player_pool is not None:
        try:
            async with player_pool.acquire() as conn:
                found = await conn.fetch(
                    """SELECT card_id, name, rating, version, image_url
                       FROM fut_players WHERE card_id = ANY($1::text[])""",
                    [str(i) for i in futbin_ids],
                )
            for r in found:
                resolved[int(r["card_id"])] = {
                    "name": r["name"], "rating": r["rating"],
                    "version": r["version"], "image_url": r["image_url"],
                }
        except Exception:
            log.warning("alerts feed: legacy card resolution failed", exc_info=True)

    return resolved


async def fetch_alert_feed(
    *, watchlist_pool, core_pool, player_pool, user_id: str,
    limit: int = DEFAULT_FEED_LIMIT,
) -> Dict[str, Any]:
    """The single read every alert surface uses.

    `unreadCount` is a real count of undismissed, unread rows - not
    `len(items)`, which is capped by `limit` and collapsed by repeat, and
    so could never be a truthful badge number.
    """
    async with watchlist_pool.acquire() as conn:
        # Fetch more than `limit` so repeat-collapsing has something to
        # collapse and can still return a full page afterwards.
        rows = [dict(r) for r in await conn.fetch(_SELECT_LIVE_ALERTS, str(user_id), limit * 4)]
        unread_count = int(await conn.fetchval(
            "SELECT COUNT(*) FROM alerts_log WHERE user_id = $1 AND dismissed_at IS NULL AND read_at IS NULL",
            str(user_id),
        ) or 0)
        watched_count = int(await conn.fetchval(
            "SELECT COUNT(*) FROM watchlist WHERE user_id = $1", str(user_id),
        ) or 0)

    cards = await _resolve_cards(core_pool, player_pool, rows)
    grouped = collapse_repeats(rows)[:limit]
    items = [
        build_alert_item(row, card=cards.get(int(row["card_id"])), repeat_count=count)
        for row, count in grouped
    ]
    return {"items": items, "unreadCount": unread_count, "watchedCount": watched_count}
