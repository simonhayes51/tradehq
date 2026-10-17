"""Parse and persist FUT.GG FC27 manager/consumable bulk prices.

The misc feed is a small independent part of the bulk price response:
manager cards, manager leagues, and chemistry styles. It is collected by
the existing browser-based bulk worker, and stored in its own history table
so player price analytics and console/PC player tables remain untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any


ITEM_TYPES = ("manager", "manager_league", "chemistry_style")
HEARTBEAT_MINUTES = max(1, int(os.getenv("FUTGG_MISC_PRICE_HEARTBEAT_MINUTES", "15")))
RETENTION_DAYS = max(1, int(os.getenv("FUTGG_MISC_PRICE_RETENTION_DAYS", "30")))
PRUNE_EVERY_CYCLES = max(1, int(os.getenv("FUTGG_MISC_PRICE_PRUNE_EVERY_CYCLES", "12")))
PRUNE_BATCH_ROWS = max(1000, int(os.getenv("FUTGG_MISC_PRICE_PRUNE_BATCH_ROWS", "10000")))

# Keyed by (year, platform, item type, item id), value is the last persisted
# price/status and its observation time. A restart writes one baseline for
# each item again; that is a small, explicit cost which avoids a history scan
# at startup on the shared production database.
_last_written: dict[tuple[int, str, str, int], tuple[int | None, str, datetime]] = {}
_cycles = 0


@dataclass(frozen=True)
class MiscPrice:
    item_type: str
    item_id: int
    price: int | None
    status: str
    price_updated_at: datetime | None
    last_checked_at: datetime | None


def _parse_timestamp(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_misc_price_feed(
    payload: Any, *, game_year: int, platform: str,
) -> list[MiscPrice]:
    """Validate the feed envelope and normalize the supported item groups."""
    if not isinstance(payload, dict):
        raise ValueError("misc feed must be a JSON object")
    if payload.get("v") != 1:
        raise ValueError(f"unsupported misc feed version: {payload.get('v')!r}")
    if str(payload.get("game")) != str(game_year):
        raise ValueError(
            f"misc feed year mismatch: expected {game_year}, got {payload.get('game')!r}"
        )
    if str(payload.get("platform", "")).lower() != platform.lower():
        raise ValueError(
            f"misc feed platform mismatch: expected {platform}, got {payload.get('platform')!r}"
        )
    groups = payload.get("items")
    if not isinstance(groups, dict):
        raise ValueError("misc feed items must be an object")
    missing = [item_type for item_type in ITEM_TYPES if not isinstance(groups.get(item_type), dict)]
    if missing:
        raise ValueError(f"misc feed is missing item groups: {', '.join(missing)}")

    result: list[MiscPrice] = []
    for item_type in ITEM_TYPES:
        for raw_id, raw in groups[item_type].items():
            if not str(raw_id).isdigit():
                raise ValueError(f"non-numeric {item_type} item id: {raw_id!r}")
            if not isinstance(raw, dict):
                raise ValueError(f"{item_type} {raw_id} must be an object")
            price = raw.get("price")
            if price is not None and (
                isinstance(price, bool) or not isinstance(price, int) or price < 0
            ):
                raise ValueError(f"invalid price for {item_type} {raw_id}: {price!r}")
            status = raw.get("status")
            if not isinstance(status, str) or not status.strip():
                raise ValueError(f"missing status for {item_type} {raw_id}")
            result.append(MiscPrice(
                item_type=item_type,
                item_id=int(raw_id),
                price=price,
                status=status.strip(),
                price_updated_at=_parse_timestamp(
                    raw.get("priceUpdatedAt"), field="priceUpdatedAt"
                ),
                last_checked_at=_parse_timestamp(
                    raw.get("lastCheckedAt"), field="lastCheckedAt"
                ),
            ))
    if not result:
        raise ValueError("misc feed contains no supported items")
    return result


def _heartbeat_for(item: MiscPrice) -> int:
    # Spread heartbeat writes across the configured window rather than
    # making every unchanged item due on the same cycle.
    return HEARTBEAT_MINUTES + (
        (item.item_id + sum(ord(ch) for ch in item.item_type)) % HEARTBEAT_MINUTES
    )


def select_misc_rows_to_write(
    items: list[MiscPrice], *, now: datetime, game_year: int, platform: str,
    state: dict[tuple[int, str, str, int], tuple[int | None, str, datetime]] | None = None,
) -> tuple[list[tuple[Any, ...]], int, int]:
    """Return changed items plus staggered heartbeats for unchanged items."""
    cache = _last_written if state is None else state
    records: list[tuple[Any, ...]] = []
    changed = heartbeat = 0
    for item in items:
        key = (game_year, platform, item.item_type, item.item_id)
        previous = cache.get(key)
        is_changed = previous is None or previous[:2] != (item.price, item.status)
        due = previous is not None and previous[2] <= now - timedelta(
            minutes=_heartbeat_for(item)
        )
        if is_changed or due:
            records.append((
                game_year, platform, item.item_type, item.item_id, item.price,
                item.status, item.price_updated_at, item.last_checked_at, now,
            ))
            if is_changed:
                changed += 1
            else:
                heartbeat += 1
            cache[key] = (item.price, item.status, now)
    return records, changed, heartbeat


async def _prune_history(pool, *, game_year: int) -> int:
    deleted_total = 0
    async with pool.acquire() as conn:
        for _ in range(20):
            status = await conn.execute(
                """
                DELETE FROM futgg_misc_price_history
                WHERE id IN (
                    SELECT id FROM futgg_misc_price_history
                    WHERE game_year = $1
                      AND captured_at < now() - make_interval(days => $2)
                    ORDER BY id
                    LIMIT $3
                )
                """,
                game_year, RETENTION_DAYS, PRUNE_BATCH_ROWS,
            )
            try:
                deleted = int(status.split()[-1])
            except (ValueError, IndexError, AttributeError):
                break
            deleted_total += deleted
            if deleted < PRUNE_BATCH_ROWS:
                break
    return deleted_total


async def ingest_misc_price_feed(
    pool, payload: Any, *, game_year: int, platform: str, apply: bool = True,
) -> dict[str, Any]:
    """Validate, change-filter, and COPY one captured misc feed to Postgres."""
    global _cycles
    if payload is None:
        return {"ok": False, "reason": "feed_not_captured", "written": 0}
    items = parse_misc_price_feed(payload, game_year=game_year, platform=platform)
    now = datetime.now(timezone.utc)
    # Work against a temporary state. Commit that cache only after the
    # database write succeeds, otherwise a transient DB failure could cause
    # the next cycle to think an unwritten price was already persisted.
    state = dict(_last_written) if apply else {}
    records, changed, heartbeats = select_misc_rows_to_write(
        items, now=now, game_year=game_year, platform=platform, state=state,
    )
    if not apply:
        return {
            "ok": True, "applied": False, "items": len(items), "written": 0,
            "changed": changed, "heartbeats": heartbeats,
        }

    if records:
        async with pool.acquire() as conn:
            await conn.copy_records_to_table(
                "futgg_misc_price_history",
                records=records,
                columns=[
                    "game_year", "platform", "item_type", "item_id", "price",
                    "status", "price_updated_at", "last_checked_at", "captured_at",
                ],
            )
    _last_written.clear()
    _last_written.update(state)
    _cycles += 1
    pruned = 0
    if _cycles % PRUNE_EVERY_CYCLES == 0:
        pruned = await _prune_history(pool, game_year=game_year)
    return {
        "ok": True, "applied": True, "items": len(items), "written": len(records),
        "changed": changed, "heartbeats": heartbeats, "pruned": pruned,
    }
