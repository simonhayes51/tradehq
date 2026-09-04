#!/usr/bin/env python
"""Shadow-mode measurement for the triggered detailed-sales queue - Part
6 of the FUT.GG price-detail infrastructure work.

WHY THIS EXISTS
---------------
Part 5 built futgg_sales_triggers.select_triggered() as a fully additive
selection, tested with no live database. This module is how it gets
observed against real production data WITHOUT changing what
futgg_sales_sync.py actually fetches: run select_triggered() alongside
the real select_due() every cycle, record what each one picked (and why)
into futgg_sales_shadow_log (migration 081), and report the aggregate
over a chosen interval afterwards. No scheduling switch happens here or
anywhere in this task - see build_shadow_report()'s own note on why
"estimated reduction" is not a number this phase can produce.

WIRING (not done by this module)
---------------------------------
futgg_sales_sync.py's main loop would call, once per cycle, right after
`due = await select_due(conn)`:

    cycle_started_at = datetime.now(timezone.utc)
    triggered = await select_triggered(
        conn, watchlist_conn=watchlist_conn,
        exclude_ids=[int(r["source_card_id"]) for r in due],
    )
    # ... existing due-only fetch loop runs unchanged ...
    metrics = build_shadow_cycle_metrics(
        due_rows=due, triggered_cards=triggered, stats=stats,
        cycle_started_at=cycle_started_at, cycle_ended_at=datetime.now(timezone.utc),
        throttled_batches=throttle.consecutive_fully_throttled_batches,
        successful_card_ids=[...ids from `updates` where succeeded...],
    )
    await record_shadow_cycle(conn, metrics)

That wiring is intentionally NOT included in this change: it requires a
live production database and a running sales container to observe
anything meaningful, both explicitly out of reach of this sandbox (see
the project's own DISCORD_TRADING_FEED_RUNBOOK.md for the same class of
restriction on discord.com/raw DB access). What ships here is everything
that does not require that: the pure metric-building/aggregation logic,
fully unit-tested, ready to be called from that one insertion point once
deployed.

WHAT THIS CANNOT MEASURE, BY DESIGN
--------------------------------------
"cards the old scheduler would fetch but the trigger model would skip"
and "estimated reduction" presuppose a REPLACEMENT scheduling model - one
where a card's trigger status changes its OWN cadence, so an untriggered
card gets read less often and a triggered one more. Part 5 was
deliberately not built that way (the task's own "do not make another
tier-floor or fixed-share scheduling tweak", and this Part's own "no
scheduling switch yet"): select_due()'s tier floors are completely
unchanged, and select_triggered() only ever ADDS a bounded number of
extra targeted reads on top of that baseline. There is therefore no
reduction to estimate yet - only additive overhead and targeting
quality - and ShadowIntervalReport reports triggered_new_requests (the
real added load) rather than a fabricated skip/reduction figure. A later
phase letting a card's own trigger history relax its tier is what would
turn this into an actual reduction; that phase does not exist yet.

"bulk manifest checks" / "dynamic blobs downloaded" were specified
against a manifest.json-polling design that Part 4's audit found this
codebase does not use (r2.fut.gg blocks direct requests to manifest.json
exactly as it blocks the blobs themselves - see futgg_bulk_price_sync.py's
own TRANSPORT section). Under the page-capture design this codebase
actually runs, one page load yields both blobs at once, so both figures
below are the SAME underlying count: distinct bulk-sync cycles observed
via futgg_bin_history.captured_at in the interval (see
bulk_cycles_in_interval()). They are reported as two fields, both equal,
so the output still maps 1:1 onto the requested list.

wall-clock throughput
----------------------
unique_successful_refreshes_per_wall_clock_hour is the throughput figure
the task called out explicitly by name: an in-cycle cards/min rate
silently excludes CYCLE_SLEEP, IDLE_SLEEP, and
CIRCUIT_BREAKER_BACKOFF_SECONDS between cycles, so a rate-limited hour
and a healthy hour can report the same optimistic number. This instead
dedupes real successful card ids (stored per cycle, see migration 081's
own comment on why that's cheap) against real wall-clock interval
length.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import asyncpg

from futgg_sales_triggers import TriggeredCard

log = logging.getLogger("futgg_sales_shadow")


# ---------------------------------------------------------------------
# Per-cycle metrics
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowCycleMetrics:
    cycle_started_at: datetime
    cycle_ended_at: datetime
    due_count: int
    due_count_by_tier: dict[str, int]
    triggered_count: int
    triggered_new_count: int
    triggered_card_ids: tuple[int, ...]
    reason_counts: dict[str, int]
    successful_reads: int
    failed_reads: int
    successful_card_ids: tuple[int, ...]
    throttled_batches: int
    rate_limited_count: int


def build_shadow_cycle_metrics(
    *,
    due_rows: Iterable[Any],
    triggered_cards: Iterable[TriggeredCard],
    stats: dict[str, Any],
    cycle_started_at: datetime,
    cycle_ended_at: datetime,
    throttled_batches: int = 0,
    successful_card_ids: Iterable[int] = (),
) -> ShadowCycleMetrics:
    """Pure - no DB. `due_rows` are select_due()'s own asyncpg.Record
    rows (need only ["source_card_id"] and ["effective_tier"]). `stats`
    is apply_batch_results()'s own per-cycle dict
    ({"ok": int, "failed": int, "errors": {marker: count}, ...})."""
    due_rows = list(due_rows)
    due_ids = {int(_get(r, "source_card_id")) for r in due_rows}
    tier_counts: Counter[str] = Counter()
    for r in due_rows:
        tier_counts[str(_get(r, "effective_tier", "NORMAL"))] += 1

    triggered = list(triggered_cards)
    triggered_ids = tuple(int(c.card_id) for c in triggered)
    reason_counts: Counter[str] = Counter()
    for card in triggered:
        for reason in card.reasons:
            reason_counts[reason] += 1

    errors = stats.get("errors", {}) or {}
    # Mirrors _THROTTLE_ERROR_MARKERS' own "429" substring convention -
    # see futgg_sales_sync.py's is_throttle_signal().
    rate_limited_count = sum(count for marker, count in errors.items() if "429" in marker)

    return ShadowCycleMetrics(
        cycle_started_at=cycle_started_at,
        cycle_ended_at=cycle_ended_at,
        due_count=len(due_ids),
        due_count_by_tier=dict(tier_counts),
        triggered_count=len(triggered),
        triggered_new_count=len({c.card_id for c in triggered} - due_ids),
        triggered_card_ids=triggered_ids,
        reason_counts=dict(reason_counts),
        successful_reads=int(stats.get("ok", 0)),
        failed_reads=int(stats.get("failed", 0)),
        successful_card_ids=tuple(int(x) for x in successful_card_ids),
        throttled_batches=int(throttled_batches),
        rate_limited_count=rate_limited_count,
    )


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


async def record_shadow_cycle(conn: asyncpg.Connection, metrics: ShadowCycleMetrics) -> None:
    await conn.execute(
        """
        INSERT INTO futgg_sales_shadow_log (
            cycle_started_at, cycle_ended_at, due_count, due_count_by_tier,
            triggered_count, triggered_new_count, triggered_card_ids,
            reason_counts, successful_reads, failed_reads,
            successful_card_ids, throttled_batches, rate_limited_count
        ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13)
        """,
        metrics.cycle_started_at, metrics.cycle_ended_at, metrics.due_count,
        json.dumps(metrics.due_count_by_tier), metrics.triggered_count,
        metrics.triggered_new_count, list(metrics.triggered_card_ids),
        json.dumps(metrics.reason_counts), metrics.successful_reads,
        metrics.failed_reads, list(metrics.successful_card_ids),
        metrics.throttled_batches, metrics.rate_limited_count,
    )


# ---------------------------------------------------------------------
# Interval aggregation
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowIntervalReport:
    interval_start: datetime
    interval_end: datetime
    cycles: int
    bulk_manifest_checks: int      # == bulk_cycles_dynamic_blobs_downloaded; see module docstring
    bulk_cycles_dynamic_blobs_downloaded: int
    cards_price_changed: int
    unique_cards_triggered: int
    trigger_selection_events: int  # sum of triggered_count across cycles - includes re-selection of the same still-unconsumed card
    trigger_reason_counts: dict[str, int]
    current_detailed_requests: int
    triggered_new_requests: int
    successful_reads_total: int
    failed_reads_total: int
    unique_successful_card_count: int
    rate_limited_total: int
    throttled_batches_total: int
    due_count_by_tier: dict[str, int]

    @property
    def hours(self) -> float:
        return max((self.interval_end - self.interval_start).total_seconds() / 3600.0, 1e-9)

    @property
    def current_detailed_requests_per_hour(self) -> float:
        return self.current_detailed_requests / self.hours

    @property
    def current_detailed_requests_per_day(self) -> float:
        return self.current_detailed_requests_per_hour * 24

    @property
    def projected_detailed_requests_per_hour(self) -> float:
        """The unchanged tier-floor baseline PLUS the triggered model's
        additive overhead if it were wired live. See this module's
        docstring for why there is no reduction figure: Part 5 is
        additive, not a replacement scheduler."""
        return (self.current_detailed_requests + self.triggered_new_requests) / self.hours

    @property
    def projected_detailed_requests_per_day(self) -> float:
        return self.projected_detailed_requests_per_hour * 24

    @property
    def unique_successful_refreshes_per_wall_clock_hour(self) -> float:
        return self.unique_successful_card_count / self.hours


def build_interval_report(
    rows: Iterable[dict[str, Any]],
    *,
    cards_price_changed: int,
    bulk_cycles: int,
) -> ShadowIntervalReport:
    """Pure aggregator over already-fetched futgg_sales_shadow_log rows
    (dict-like: cycle_started_at, cycle_ended_at, due_count,
    due_count_by_tier, triggered_count, triggered_new_count,
    triggered_card_ids, reason_counts, successful_reads, failed_reads,
    successful_card_ids, throttled_batches, rate_limited_count).
    `cards_price_changed` and `bulk_cycles` come from separate queries
    against futgg_price_change_events and futgg_bin_history - see
    compute_interval_report()."""
    rows = list(rows)
    if not rows:
        raise ValueError("no shadow-log rows in this interval - nothing to report")

    starts = [r["cycle_started_at"] for r in rows]
    ends = [r["cycle_ended_at"] for r in rows]

    reason_counts: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()
    unique_triggered: set[int] = set()
    unique_successful: set[int] = set()
    for r in rows:
        for reason, count in (r.get("reason_counts") or {}).items():
            reason_counts[reason] += int(count)
        for tier, count in (r.get("due_count_by_tier") or {}).items():
            tier_counts[tier] += int(count)
        unique_triggered.update(int(x) for x in (r.get("triggered_card_ids") or []))
        unique_successful.update(int(x) for x in (r.get("successful_card_ids") or []))

    return ShadowIntervalReport(
        interval_start=min(starts),
        interval_end=max(ends),
        cycles=len(rows),
        bulk_manifest_checks=bulk_cycles,
        bulk_cycles_dynamic_blobs_downloaded=bulk_cycles,
        cards_price_changed=cards_price_changed,
        unique_cards_triggered=len(unique_triggered),
        trigger_selection_events=sum(int(r.get("triggered_count", 0)) for r in rows),
        trigger_reason_counts=dict(reason_counts),
        current_detailed_requests=sum(int(r.get("due_count", 0)) for r in rows),
        triggered_new_requests=sum(int(r.get("triggered_new_count", 0)) for r in rows),
        successful_reads_total=sum(int(r.get("successful_reads", 0)) for r in rows),
        failed_reads_total=sum(int(r.get("failed_reads", 0)) for r in rows),
        unique_successful_card_count=len(unique_successful),
        rate_limited_total=sum(int(r.get("rate_limited_count", 0)) for r in rows),
        throttled_batches_total=sum(int(r.get("throttled_batches", 0)) for r in rows),
        due_count_by_tier=dict(tier_counts),
    )


async def compute_interval_report(
    core_conn: asyncpg.Connection, interval_start: datetime, interval_end: datetime,
) -> ShadowIntervalReport:
    """The one function an operator actually calls: reads
    futgg_sales_shadow_log plus the two supporting real signals
    (futgg_price_change_events for cards_price_changed,
    futgg_bin_history.captured_at for bulk_cycles) and builds the
    report."""
    shadow_rows = await core_conn.fetch(
        """
        SELECT cycle_started_at, cycle_ended_at, due_count, due_count_by_tier,
               triggered_count, triggered_new_count, triggered_card_ids,
               reason_counts, successful_reads, failed_reads,
               successful_card_ids, throttled_batches, rate_limited_count
        FROM futgg_sales_shadow_log
        WHERE cycle_started_at >= $1 AND cycle_started_at < $2
        ORDER BY cycle_started_at
        """,
        interval_start, interval_end,
    )
    cards_price_changed = await core_conn.fetchval(
        "SELECT COUNT(*) FROM futgg_price_change_events WHERE detected_at >= $1 AND detected_at < $2",
        interval_start, interval_end,
    )
    bulk_cycles = await core_conn.fetchval(
        "SELECT COUNT(DISTINCT captured_at) FROM futgg_bin_history WHERE captured_at >= $1 AND captured_at < $2",
        interval_start, interval_end,
    )
    return build_interval_report(
        [dict(r) for r in shadow_rows],
        cards_price_changed=int(cards_price_changed or 0),
        bulk_cycles=int(bulk_cycles or 0),
    )


# ---------------------------------------------------------------------
# Human-readable rendering
# ---------------------------------------------------------------------


def render_report(report: ShadowIntervalReport) -> str:
    lines = [
        f"Shadow-mode report: {report.interval_start.isoformat()} .. {report.interval_end.isoformat()} "
        f"({report.hours:.2f}h, {report.cycles} cycles)",
        "",
        f"  bulk manifest checks:              {report.bulk_manifest_checks} "
        "(== dynamic blobs downloaded; page-capture design, see module docstring)",
        f"  dynamic blobs downloaded:          {report.bulk_cycles_dynamic_blobs_downloaded}",
        f"  cards whose current price changed: {report.cards_price_changed}",
        f"  cards triggering detailed collection (unique): {report.unique_cards_triggered}",
        f"  trigger-selection events (incl. re-selection of unconsumed reasons): {report.trigger_selection_events}",
        f"  trigger reason counts:             {report.trigger_reason_counts}",
        f"  backlog by priority (due, this interval): {report.due_count_by_tier}",
        "",
        f"  current detailed requests:  {report.current_detailed_requests} "
        f"({report.current_detailed_requests_per_hour:.1f}/hour, {report.current_detailed_requests_per_day:.0f}/day)",
        f"  projected w/ triggers added: {report.current_detailed_requests + report.triggered_new_requests} "
        f"({report.projected_detailed_requests_per_hour:.1f}/hour, {report.projected_detailed_requests_per_day:.0f}/day)",
        "  estimated reduction: N/A - Part 5 is additive only (tier floors "
        "unchanged); see module docstring for why",
        "",
        f"  successful reads: {report.successful_reads_total}   failed reads: {report.failed_reads_total}",
        f"  429s: {report.rate_limited_total}   throttled batches: {report.throttled_batches_total}",
        f"  unique successful refreshes / wall-clock hour: "
        f"{report.unique_successful_refreshes_per_wall_clock_hour:.2f}",
    ]
    return "\n".join(lines)
