from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.game_cycle import active_game_year


PROOF_STATUSES = frozenset(
    {"target_hit", "profitable_unrealised", "flat", "loss_unrealised", "downside_hit"}
)
WIN_STATUSES = frozenset({"target_hit", "profitable_unrealised"})
LOSS_STATUSES = frozenset({"loss_unrealised", "downside_hit"})
DAILY_PROOF_CLAIM_LOCK_ID = 731_904_058


@dataclass(frozen=True)
class DailyProofSchedule:
    timezone_name: str = "Europe/London"
    hour: int = 21
    minute: int = 0

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown Proof Ledger timezone: {self.timezone_name}") from exc
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("Proof Ledger report time must be a valid hour and minute")

    @classmethod
    def from_env(cls) -> "DailyProofSchedule":
        return cls(
            timezone_name=(
                os.getenv("DISCORD_PROOF_REPORT_TIMEZONE") or "Europe/London"
            ).strip(),
            hour=int(os.getenv("DISCORD_PROOF_REPORT_HOUR") or "21"),
            minute=int(os.getenv("DISCORD_PROOF_REPORT_MINUTE") or "0"),
        )

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True)
class ReportingPeriod:
    report_date: date
    start: datetime
    end: datetime
    timezone_name: str


def latest_completed_reporting_period(
    now: Optional[datetime] = None,
    schedule: Optional[DailyProofSchedule] = None,
) -> ReportingPeriod:
    schedule = schedule or DailyProofSchedule.from_env()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(schedule.zone)
    boundary_date = local_now.date()
    boundary = datetime.combine(
        boundary_date, time(schedule.hour, schedule.minute), tzinfo=schedule.zone
    )
    if local_now < boundary:
        boundary_date -= timedelta(days=1)
        boundary = datetime.combine(
            boundary_date, time(schedule.hour, schedule.minute), tzinfo=schedule.zone
        )
    previous_date = boundary_date - timedelta(days=1)
    previous = datetime.combine(
        previous_date, time(schedule.hour, schedule.minute), tzinfo=schedule.zone
    )
    return ReportingPeriod(
        report_date=boundary_date,
        start=previous.astimezone(timezone.utc),
        end=boundary.astimezone(timezone.utc),
        timezone_name=schedule.timezone_name,
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def proof_row_is_defensible(row: dict[str, Any], period: ReportingPeriod) -> bool:
    try:
        evaluated_at = _aware(row["evaluated_at"])
        graded_at = _aware(row["graded_at"])
        entry_at = _aware(row["entry_at"])
        outcome_at = _aware(row["outcome_observed_at"])
        window_end = _aware(row["window_end"])
        status = str(row["outcome_status"])
        card_ids = {
            int(row["outcome_source_card_id"]),
            int(row["snapshot_source_card_id"]),
            int(row["player_source_card_id"]),
        }
        entry = Decimal(str(row["entry_price"]))
        outcome_price = Decimal(str(row["outcome_price"]))
        recorded_profit = Decimal(str(row["net_profit_after_tax"]))
        recorded_roi = Decimal(str(row["realised_roi"]))
        target = Decimal(str(row["recommended_sell_target"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False

    if len(card_ids) != 1 or status not in PROOF_STATUSES:
        return False
    if not (period.start <= graded_at < period.end):
        return False
    if row.get("currently_live") or not row.get("snapshot_closed"):
        return False
    if not row.get("entry_observation_verified") or not row.get("outcome_observation_verified"):
        return False
    if (
        entry <= 0
        or outcome_price <= 0
        or target <= 0
        or not (evaluated_at < entry_at < outcome_at <= window_end)
    ):
        return False
    expected_profit = outcome_price * Decimal("0.95") - entry
    expected_roi = expected_profit / entry
    if abs(recorded_profit - expected_profit) > Decimal("0.01"):
        return False
    if abs(recorded_roi - expected_roi) > Decimal("0.000001"):
        return False

    try:
        if status == "target_hit":
            if not row.get("target_hit") or not row.get("exit_achieved"):
                return False
            if row.get("outcome_price_type") != "target_exit" or outcome_price < target:
                return False
            if Decimal(str(row.get("realised_sell_price"))) != outcome_price:
                return False
            if row.get("exit_at") != row.get("outcome_observed_at"):
                return False
            elapsed = int((outcome_at - entry_at).total_seconds() // 60)
            return row.get("minutes_to_target") == elapsed

        # Mirrors futgg_outcome_grader.grade_recommendation()'s
        # classification exactly - this is the independent re-derivation
        # that proves a stored row was graded by the rules we publish.
        #
        # It has to move with that function. The grader used to let
        # downside_hit override the realised result outright, which
        # produced rows counted as a LOSS while carrying a positive
        # profit and ROI. Now the realised result decides win/loss/flat
        # and downside_hit only chooses WHICH loss label applies, so a
        # position that dipped past the threshold and recovered is
        # legitimately `profitable_unrealised` WITH downside_hit set.
        # Keeping the old "and not downside_hit" clauses here would
        # silently reject exactly those rows as indefensible and drop
        # them from the published record.
        deep_drawdown = bool(row.get("downside_hit"))
        status_matches_engine = {
            "profitable_unrealised": expected_roi > Decimal("0.005"),
            "loss_unrealised": expected_roi < Decimal("-0.005") and not deep_drawdown,
            "flat": abs(expected_roi) <= Decimal("0.005"),
            "downside_hit": deep_drawdown
            and expected_roi < Decimal("-0.005")
            and Decimal(str(row.get("max_adverse_excursion"))) <= Decimal("-0.10"),
        }
    except (TypeError, ValueError, ArithmeticError):
        return False
    return (
        not row.get("target_hit")
        and not row.get("exit_achieved")
        and row.get("outcome_price_type") == "window_mark"
        and row.get("minutes_to_target") is None
        and status_matches_engine.get(status, False)
    )


def _report_item(row: dict[str, Any]) -> dict[str, Any]:
    entry = Decimal(str(row["entry_price"]))
    result = Decimal(str(row["net_profit_after_tax"]))
    return {
        "snapshot_id": int(row["snapshot_id"]),
        "source_card_id": int(row["outcome_source_card_id"]),
        "name": str(row["name"]),
        "card_rating": (
            int(row["card_rating"]) if row.get("card_rating") is not None else None
        ),
        "rarity": str(row["rarity"]) if row.get("rarity") else None,
        "entry": int(row["entry_price"]),
        "exit": int(row["outcome_price"]),
        "exit_type": str(row["outcome_price_type"]),
        "result": str(result),
        "roi": str(result / entry),
        "status": str(row["outcome_status"]),
    }


def _unique_card_version_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the earliest eligible signal for each exact FUT card/version.

    Repeated recommendation snapshots for one source_card_id are not
    independent trades. Earliest-signal selection is deterministic and
    avoids choosing whichever duplicate later produced the best result.
    """
    selected: dict[int, dict[str, Any]] = {}
    for row in rows:
        source_card_id = int(row["outcome_source_card_id"])
        existing = selected.get(source_card_id)
        candidate_key = (_aware(row["evaluated_at"]), int(row["snapshot_id"]))
        if existing is None or candidate_key < (
            _aware(existing["evaluated_at"]),
            int(existing["snapshot_id"]),
        ):
            selected[source_card_id] = row
    return list(selected.values())


def build_daily_proof_report(
    rows: list[dict[str, Any]], period: ReportingPeriod
) -> Optional[dict[str, Any]]:
    eligible_snapshots = [row for row in rows if proof_row_is_defensible(row, period)]
    if not eligible_snapshots:
        return None
    eligible = _unique_card_version_rows(eligible_snapshots)

    wins = [row for row in eligible if row["outcome_status"] in WIN_STATUSES]
    losses = [row for row in eligible if row["outcome_status"] in LOSS_STATUSES]
    flats = [row for row in eligible if row["outcome_status"] == "flat"]
    results = [Decimal(str(row["net_profit_after_tax"])) for row in eligible]
    target_exits = [row for row in eligible if row["outcome_price_type"] == "target_exit"]
    window_marks = [row for row in eligible if row["outcome_price_type"] == "window_mark"]
    target_exit_result = sum(
        (Decimal(str(row["net_profit_after_tax"])) for row in target_exits), Decimal("0")
    )
    window_mark_result = sum(
        (Decimal(str(row["net_profit_after_tax"])) for row in window_marks), Decimal("0")
    )
    top_wins = sorted(
        wins,
        key=lambda row: (
            -Decimal(str(row["net_profit_after_tax"])),
            -Decimal(str(row["realised_roi"])),
            str(row["name"]),
            int(row["snapshot_id"]),
        ),
    )[:3]
    negative_losses = [
        row for row in losses if Decimal(str(row["net_profit_after_tax"])) < 0
    ]
    worst_loss = min(
        negative_losses,
        key=lambda row: (
            Decimal(str(row["net_profit_after_tax"])),
            Decimal(str(row["realised_roi"])),
            str(row["name"]),
            int(row["snapshot_id"]),
        ),
        default=None,
    )
    total = len(eligible)
    return {
        "report_date": period.report_date.isoformat(),
        "period_start": period.start.isoformat(),
        "period_end": period.end.isoformat(),
        "timezone": period.timezone_name,
        "total": total,
        "repeat_snapshots_consolidated": len(eligible_snapshots) - total,
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate": str(Decimal(len(target_exits)) / Decimal(total)),
        "target_exits": len(target_exits),
        "window_marks": len(window_marks),
        "target_exit_result": str(target_exit_result),
        "window_mark_result": str(window_mark_result),
        "combined_result": str(sum(results, Decimal("0"))),
        "average_result": str(sum(results, Decimal("0")) / Decimal(total)),
        "median_result": str(median(results)),
        "top_wins": [_report_item(row) for row in top_wins],
        "worst_loss": _report_item(worst_loss) if worst_loss else None,
    }


# =============================================================================
# Published opportunity track record
# =============================================================================
# HOTFIX: the customer-facing Track Record panel was presenting
# background-scanner signal validation (every buy/strong_buy call the
# engine evaluated, ~10-minute cadence, no dedup across passes) as "our
# calls" - language a user could reasonably read as recommendations they
# personally could have followed. That is not what that dataset is.
#
# discord_daily_proof_publications is the one place in this codebase
# that already tracks genuine publication to real users: a row with
# status='sent' means build_daily_proof_report()'s output was actually
# posted to the configured Discord channel (discord_message_id +
# published_at prove delivery - see publish_daily_proof_review() above).
# This is the real "Proof Ledger". published_track_record() re-derives
# the exact per-day eligible outcome set for every sent day in the
# window using the SAME query and the SAME row-by-row validation
# (_SELECT_DAILY_OUTCOMES, proof_row_is_defensible) the publish path
# itself uses - not a looser or different definition - so the numbers
# shown here are provably the same population that was actually sent to
# Discord, deduplicated globally across the whole window with
# _unique_card_version_rows (a card recommended on multiple sent days
# counts once, at its earliest eligible signal).
#
# The publish pipeline only ever reports on entered trades
# (_SELECT_DAILY_OUTCOMES's own AND o.entry_achieved=TRUE) - it has never
# tracked "recommended but never reached entry" as a published concept.
# has_published_data=False (or entry_reached_not_tracked=True) is the
# honest answer for that, not a fabricated count.
PROOF_MIN_SAMPLE = 20


async def _fetch_sent_publications(conn, window_days: int):
    interval = timedelta(days=int(window_days))
    return await conn.fetch(
        """
        SELECT report_date, period_start, period_end, timezone_name
        FROM discord_daily_proof_publications
        WHERE status = 'sent' AND report_date >= (now() - $1::interval)::date
        ORDER BY report_date
        """,
        interval,
    )



# Bounds how many of _SELECT_DAILY_OUTCOMES' per-period queries (each
# with 3 correlated EXISTS subqueries) run concurrently. The pool itself
# would already queue anything beyond its own max_size, but this keeps a
# wide track-record window (e.g. "All time" with 90+ sent days) from
# claiming every pool connection and starving unrelated requests while
# it runs.
_PERIOD_FETCH_CONCURRENCY = 5


async def _fetch_one_period_outcomes(pool, semaphore: asyncio.Semaphore, period: "ReportingPeriod"):
    async with semaphore, pool.acquire() as conn:
        rows = await conn.fetch(
            _SELECT_DAILY_OUTCOMES, period.start, period.end, sorted(PROOF_STATUSES),
            active_game_year(),
        )
    return [dict(row) for row in rows if proof_row_is_defensible(dict(row), period)]


async def _fetch_published_eligible_rows(pool, window_days: int):
    """Shared fetch for both published_track_record() (aggregates) and
    published_recent_wins_and_losses() (individual rows) - re-derives
    the exact per-day eligible outcome set for every sent day in the
    window using the SAME query and SAME row-by-row validation
    (_SELECT_DAILY_OUTCOMES, proof_row_is_defensible) the publish path
    itself uses. Returns (pubs, all_eligible) - pubs is empty if nothing
    has ever been sent.

    Each sent day's query used to run one at a time on a single held
    connection - a 90-day window with dozens of sent days meant dozens
    of sequential round trips of a query with 3 correlated EXISTS
    subqueries each, which is what made the Track Record tab take
    minutes to load. Every period's query is independent (bounded by
    its own graded_at range, no shared mutable state), so they're safe
    to run concurrently instead - see _PERIOD_FETCH_CONCURRENCY for the
    cap.
    """
    async with pool.acquire() as conn:
        pubs = await _fetch_sent_publications(conn, window_days)
        if not pubs:
            earliest = await conn.fetchval(
                "SELECT min(report_date) FROM discord_daily_proof_publications WHERE status = 'sent'"
            )
            return pubs, [], earliest

    periods = [
        ReportingPeriod(
            report_date=pub["report_date"],
            start=_aware(pub["period_start"]),
            end=_aware(pub["period_end"]),
            timezone_name=pub["timezone_name"],
        )
        for pub in pubs
    ]
    semaphore = asyncio.Semaphore(_PERIOD_FETCH_CONCURRENCY)
    per_period_rows = await asyncio.gather(
        *(_fetch_one_period_outcomes(pool, semaphore, period) for period in periods)
    )
    all_eligible: list[dict[str, Any]] = [row for rows in per_period_rows for row in rows]
    return pubs, all_eligible, None


async def published_track_record(
    pool, *, window_days: int = 90,
    eligible_rows: "tuple[list, list[dict[str, Any]], Any] | None" = None,
) -> dict[str, Any]:
    """`eligible_rows` lets a caller that also needs
    published_recent_wins_and_losses() (GET /api/v2/track-record does,
    on every request) pass in an already-fetched
    _fetch_published_eligible_rows() result instead of triggering a
    second full re-fetch - each call re-runs one _SELECT_DAILY_OUTCOMES
    query (3 correlated EXISTS subqueries) per sent day in the window,
    so calling this twice per request doubled that cost for no reason.
    """
    pubs, all_eligible, earliest_ever = (
        eligible_rows if eligible_rows is not None
        else await _fetch_published_eligible_rows(pool, window_days)
    )
    if not pubs:
        return {
            "has_published_data": False,
            "tracking_since": earliest_ever.isoformat() if earliest_ever else None,
            "window_days": window_days,
        }

    if not all_eligible:
        return {
            "has_published_data": False,
            "tracking_since": None,
            "window_days": window_days,
            "game_year": active_game_year(),
        }

    tracking_since = min(_aware(row["evaluated_at"]) for row in all_eligible).date().isoformat()
    return {
        "has_published_data": True,
        "tracking_since": tracking_since,
        "window_days": window_days,
        "game_year": active_game_year(),
        **aggregate_published_outcomes(all_eligible),
    }


async def published_recent_wins_and_losses(
    pool, *, window_days: int = 90, limit: int = 5,
    eligible_rows: "tuple[list, list[dict[str, Any]], Any] | None" = None,
) -> dict[str, Any]:
    """The 5 most recent published wins and 5 most recent published
    losses, kept in clearly separate lists so losses are always visible
    without a filter click. Deduplicated by real recommendation identity
    (snapshot_id) via the same _unique_card_version_rows rule the
    aggregate track record uses - the same card cannot appear twice
    unless it is genuinely two separate published recommendations
    (different snapshot_id/evaluated_at), which stays visible in the
    output rather than being silently collapsed.

    See published_track_record()'s `eligible_rows` docstring - same
    reasoning, same parameter, so GET /api/v2/track-record's two calls
    into this module share one fetch instead of two."""
    pubs, all_eligible, earliest_ever = (
        eligible_rows if eligible_rows is not None
        else await _fetch_published_eligible_rows(pool, window_days)
    )
    if not pubs or not all_eligible:
        return {
            "has_published_data": False,
            "tracking_since": None,
            "window_days": window_days,
            "game_year": active_game_year(),
            "recent_target_hits": [],
            "recent_target_misses": [],
        }

    eligible = _unique_card_version_rows(all_eligible)
    target_hits = sorted(
        (row for row in eligible if row.get("target_hit")),
        key=lambda row: (_aware(row["graded_at"]), int(row["snapshot_id"])),
        reverse=True,
    )
    target_misses = sorted(
        (row for row in eligible if not row.get("target_hit")),
        key=lambda row: (_aware(row["graded_at"]), int(row["snapshot_id"])),
        reverse=True,
    )
    tracking_since = min(_aware(row["evaluated_at"]) for row in eligible).date().isoformat()
    return {
        "has_published_data": True,
        "tracking_since": tracking_since,
        "window_days": window_days,
        "game_year": active_game_year(),
        "recent_target_hits": [_ledger_item(row) for row in target_hits[:limit]],
        "recent_target_misses": [_ledger_item(row) for row in target_misses[:limit]],
    }


def _ledger_item(row: dict[str, Any]) -> dict[str, Any]:
    """Full per-row detail for the recent-outcomes list - richer than
    _report_item() (which only carries what the Discord embed needs)."""
    entry = Decimal(str(row["entry_price"]))
    result = Decimal(str(row["net_profit_after_tax"]))
    mfe = row.get("max_favourable_excursion")
    peak_price = int(entry * (Decimal("1") + Decimal(str(mfe)))) if mfe is not None else None
    best_profit = (Decimal(str(peak_price)) * Decimal("0.95") - entry) if peak_price is not None else None
    return {
        "snapshot_id": int(row["snapshot_id"]),
        # _SELECT_DAILY_OUTCOMES hardcodes horizon='72h' in its WHERE
        # clause and doesn't select the column itself, so this is a
        # constant, not a read of row data.
        "outcome_id": f"{row['snapshot_id']}:72h",
        "source_card_id": int(row["outcome_source_card_id"]),
        "player_name": str(row["name"]),
        "card_rating": int(row["card_rating"]) if row.get("card_rating") is not None else None,
        "rarity": str(row["rarity"]) if row.get("rarity") else None,
        "detected_at": _aware(row["evaluated_at"]).isoformat(),
        "entry_price": int(entry),
        "exit_price": int(row["outcome_price"]),
        "net_profit_after_tax": float(result),
        "realised_roi_pct": round(float(result / entry) * 100, 2),
        "peak_price": peak_price,
        "best_available_profit_after_tax": float(best_profit) if best_profit is not None else None,
        "best_available_roi_pct": round(float(best_profit / entry) * 100, 2) if best_profit is not None else None,
        "minutes_to_target": int(row["minutes_to_target"]) if row.get("minutes_to_target") is not None else None,
        "outcome_status": str(row["outcome_status"]),
        "graded_at": _aware(row["graded_at"]).isoformat(),
    }


def aggregate_published_outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure aggregation over already-fetched, already-defensibility-
    filtered outcome rows (see published_track_record() above - this is
    its computation half, split out so it's testable without a database,
    the same way build_daily_proof_report() is).

    Applies the same global "one card, one call" dedup the single-day
    report already applies within a day - extended across the whole
    input so a card recommended on several different sent days still
    counts once, at its earliest eligible signal, not once per day it
    appeared.
    """
    eligible = _unique_card_version_rows(rows)

    target_hits = [row for row in eligible if row.get("target_hit")]
    target_misses = [row for row in eligible if not row.get("target_hit")]
    target_profits = [Decimal(str(row["net_profit_after_tax"])) for row in target_hits]
    target_rois = [Decimal(str(row["realised_roi"])) for row in target_hits]
    hold_times = [
        int(row["minutes_to_target"]) for row in eligible
        if row.get("target_hit") and row.get("minutes_to_target") is not None
    ]
    hits_within_24h = [
        row for row in target_hits
        if row.get("minutes_to_target") is not None and int(row["minutes_to_target"]) <= 24 * 60
    ]
    drawdowns = [
        abs(min(Decimal("0"), Decimal(str(row["max_adverse_excursion"])))) * Decimal("100")
        for row in eligible
        if row.get("max_adverse_excursion") is not None
    ]
    total = len(eligible)
    has_enough = total >= PROOF_MIN_SAMPLE

    return {
        "has_enough_data": has_enough,
        "min_sample": PROOF_MIN_SAMPLE,
        "published_opportunities": total,
        "entry_reached": total,  # every published row is, by definition, entered
        "entry_reached_not_tracked": True,  # the publish pipeline never records "recommended but never entered"
        "target_hits": len(target_hits),
        "target_not_reached": len(target_misses),
        "target_hit_rate_pct": round(100 * len(target_hits) / total, 1) if has_enough else None,
        "target_hits_within_24h": len(hits_within_24h),
        "target_hit_within_24h_rate_pct": round(100 * len(hits_within_24h) / total, 1) if has_enough else None,
        "target_hit_within_72h_rate_pct": round(100 * len(target_hits) / total, 1) if has_enough else None,
        "target_expiry_rate_pct": round(100 * len(target_misses) / total, 1) if has_enough else None,
        "median_max_drawdown_pct": round(float(median(drawdowns)), 1) if has_enough and drawdowns else None,
        "target_profit_after_tax": float(sum(target_profits, Decimal("0"))) if has_enough else None,
        "median_target_profit_after_tax": float(median(target_profits)) if has_enough and target_profits else None,
        "median_target_roi_pct": round(float(median(target_rois)) * 100, 2) if has_enough and target_rois else None,
        "median_time_to_target_minutes": int(median(hold_times)) if has_enough and hold_times else None,
    }


def _coins(value: Any, *, signed: bool = False) -> str:
    amount = Decimal(str(value))
    return f"{amount:+,.0f}" if signed else f"{amount:,.0f}"


def _result_line(item: dict[str, Any], rank: Optional[int] = None) -> str:
    prefix = f"{rank}. " if rank is not None else ""
    exit_label = "Target exit" if item["exit_type"] == "target_exit" else "72h mark"
    roi = Decimal(str(item["roi"])) * Decimal("100")
    version = " ".join(
        str(value) for value in (item.get("card_rating"), item.get("rarity")) if value
    )
    version_label = f" ({version})" if version else ""
    return (
        f"**{prefix}{item['name']}**{version_label}\n"
        f"Entry: {_coins(item['entry'])} | {exit_label}: {_coins(item['exit'])}\n"
        f"Result: {_coins(item['result'], signed=True)} coins ({roi:+.1f}% ROI)"
    )


def build_daily_proof_embed(report: dict[str, Any], app_url: str) -> dict[str, Any]:
    period_start = datetime.fromisoformat(report["period_start"])
    period_end = datetime.fromisoformat(report["period_end"])
    zone = ZoneInfo(report["timezone"])
    local_start = period_start.astimezone(zone)
    local_end = period_end.astimezone(zone)
    result = Decimal(report["combined_result"])
    win_rate = Decimal(report["win_rate"]) * Decimal("100")
    top_wins = report["top_wins"]
    fields = [
        {
            "name": "RESULTS",
            "value": (
                f"{report['total']} entered shadow positions (one card each)\n"
                + (
                    f"{report['repeat_snapshots_consolidated']} repeat same-card snapshots consolidated\n"
                    if report.get("repeat_snapshots_consolidated")
                    else ""
                )
                + f"Target exits: {report['target_exits']} | 72h marks: {report['window_marks']}\n"
                f"Target hit rate: {win_rate:.1f}%"
            ),
            "inline": False,
        },
        {
            "name": "TARGET EXIT RESULT (HYPOTHETICAL)",
            "value": f"**{_coins(report['target_exit_result'], signed=True)} coins**",
            "inline": False,
        },
        {
            "name": "72H OPEN MARK RESULT (UNREALISED)",
            "value": f"**{_coins(report['window_mark_result'], signed=True)} coins**",
            "inline": False,
        },
        {
            "name": "TOTAL SHADOW RESULT (ONE CARD EACH)",
            "value": f"**{_coins(result, signed=True)} coins**",
            "inline": False,
        },
        {
            "name": "TYPICAL RESULT",
            "value": (
                f"Average: {_coins(report['average_result'], signed=True)} coins\n"
                f"Median: {_coins(report['median_result'], signed=True)} coins\n"
                "Includes both target exits and unrealised 72h marks"
            ),
            "inline": False,
        },
    ]
    if top_wins:
        fields.append(
            {
                "name": "BEST RESULTS",
                "value": "\n\n".join(
                    _result_line(item, rank) for rank, item in enumerate(top_wins, 1)
                ),
                "inline": False,
            }
        )
    if report["worst_loss"]:
        fields.append(
            {
                "name": "LARGEST LOSS",
                "value": _result_line(report["worst_loss"]),
                "inline": False,
            }
        )
    fields.append(
        {
            "name": "ABOUT THESE RESULTS",
            "value": (
                "Shadow results assume one card of each entered opportunity; they are not "
                "an actual user portfolio and omit affordability and fill constraints. "
                "A 72h mark is unrealised and is not a sale. Only target exits are modeled "
                "exits. Past performance does not guarantee future results."
            ),
            "inline": False,
        }
    )
    color = 0x22C55E if result > 0 else 0xEF4444 if result < 0 else 0xEAB308
    return {
        "title": "EAFC.LIVE - DAILY TRADING REVIEW",
        "url": app_url.rstrip("/") + "/#/opportunities",
        "description": period_end.astimezone(zone).strftime("%d %B %Y"),
        "color": color,
        "fields": fields,
        "footer": {
            "text": (
                f"Reporting period: {local_start.strftime('%d %b %H:%M %Z')} to "
                f"{local_end.strftime('%d %b %H:%M %Z')}"
            )
        },
        "timestamp": period_end.isoformat(),
    }


_SELECT_DAILY_OUTCOMES = """
    SELECT o.snapshot_id,
           o.source_card_id AS outcome_source_card_id,
           s.source_card_id AS snapshot_source_card_id,
           p.source_card_id AS player_source_card_id,
           o.outcome_status, o.graded_at, o.window_end, o.observation_count,
           o.entry_achieved, o.entry_at, o.entry_price,
           o.exit_achieved, o.exit_at, o.realised_sell_price,
           o.outcome_price, o.outcome_observed_at, o.outcome_price_type,
           o.net_profit_after_tax, o.realised_roi,
           o.target_hit, o.downside_hit, o.max_favourable_excursion, o.max_adverse_excursion,
           o.minutes_to_target,
           s.evaluated_at, s.recommended_sell_target,
           p.name, p.rating AS card_rating, p.rarity,
           (s.invalidated_at IS NOT NULL OR s.expires_at <= $2) AS snapshot_closed,
           EXISTS (
               SELECT 1 FROM futgg_recommendation_snapshots live
               WHERE live.source_card_id=o.source_card_id
                 AND live.evaluated_at > s.evaluated_at
                 AND live.signal IN ('buy', 'strong_buy')
                 AND live.invalidated_at IS NULL
                 AND (live.expires_at IS NULL OR live.expires_at > NOW())
           ) AS currently_live,
           EXISTS (
               SELECT 1 FROM futgg_bin_history entry_observation
               WHERE entry_observation.source_card_id=o.source_card_id
                 AND entry_observation.lowest_bin=o.entry_price
                 AND entry_observation.captured_at=o.entry_at
           ) AS entry_observation_verified,
           EXISTS (
               SELECT 1 FROM futgg_bin_history outcome_observation
               WHERE outcome_observation.source_card_id=o.source_card_id
                 AND outcome_observation.lowest_bin=o.outcome_price
                 AND outcome_observation.captured_at=o.outcome_observed_at
           ) AS outcome_observation_verified
    FROM futgg_recommendation_outcomes o
    JOIN futgg_recommendation_snapshots s
      ON s.id=o.snapshot_id AND s.source_card_id=o.source_card_id
    JOIN futgg_players p ON p.source_card_id=o.source_card_id
    JOIN discord_integration_state st ON st.singleton=TRUE
    WHERE st.daily_proof_enabled_at IS NOT NULL
      AND s.evaluated_at >= st.daily_proof_enabled_at
      AND o.horizon='72h'
      AND o.graded_at >= $1 AND o.graded_at < $2
      AND o.outcome_status = ANY($3::text[])
      AND o.observation_count >= 2
      AND o.entry_achieved=TRUE
      AND o.entry_at IS NOT NULL AND o.entry_price > 0
      AND o.outcome_price IS NOT NULL
      AND o.outcome_observed_at IS NOT NULL
      AND o.net_profit_after_tax IS NOT NULL
      AND o.realised_roi IS NOT NULL
      AND p.game_year = $4
    ORDER BY o.graded_at, o.snapshot_id
"""


def _nonce(report_date: date) -> str:
    return hashlib.sha256(f"eafc-proof-{report_date.isoformat()}".encode("ascii")).hexdigest()[:25]


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


async def _claim_daily_report(pool, period: ReportingPeriod) -> Optional[dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock($1)", DAILY_PROOF_CLAIM_LOCK_ID
            )
            if not locked:
                return None
            existing = await conn.fetchrow(
                "SELECT * FROM discord_daily_proof_publications WHERE report_date=$1",
                period.report_date,
            )
            if existing:
                if existing["status"] != "retryable":
                    return None
                retryable = existing["last_error"] == "discord_http_429" or (
                    existing["retry_until"] is not None
                    and _aware(existing["retry_until"]) > datetime.now(timezone.utc)
                )
                if not retryable:
                    await conn.execute(
                        """
                        UPDATE discord_daily_proof_publications
                        SET status='abandoned', updated_at=NOW()
                        WHERE report_date=$1 AND status='retryable'
                        """,
                        period.report_date,
                    )
                    return None
                claimed = await conn.fetchrow(
                    """
                    UPDATE discord_daily_proof_publications
                    SET status='sending', attempts=attempts+1, last_error=NULL, updated_at=NOW()
                    WHERE report_date=$1 AND status='retryable'
                    RETURNING *
                    """,
                    period.report_date,
                )
                if not claimed:
                    return None
                return {
                    "report_date": claimed["report_date"],
                    "nonce": claimed["discord_nonce"],
                    "report": _decode_payload(claimed["payload"]),
                }

            enabled_at = await conn.fetchval(
                "SELECT daily_proof_enabled_at FROM discord_integration_state WHERE singleton=TRUE"
            )
            if enabled_at is None or period.start < _aware(enabled_at):
                return None
            rows = await conn.fetch(
                _SELECT_DAILY_OUTCOMES,
                period.start,
                period.end,
                sorted(PROOF_STATUSES),
                active_game_year(),
            )
            report = build_daily_proof_report([dict(row) for row in rows], period)
            if report is None:
                return None
            nonce = _nonce(period.report_date)
            inserted = await conn.fetchrow(
                """
                INSERT INTO discord_daily_proof_publications (
                    report_date, period_start, period_end, timezone_name,
                    status, discord_nonce, payload, attempts, retry_until
                ) VALUES ($1,$2,$3,$4,'sending',$5,$6::jsonb,1,NOW() + INTERVAL '2 minutes')
                ON CONFLICT (report_date) DO NOTHING
                RETURNING report_date
                """,
                period.report_date,
                period.start,
                period.end,
                period.timezone_name,
                nonce,
                json.dumps(report),
            )
            if not inserted:
                return None
            return {"report_date": period.report_date, "nonce": nonce, "report": report}


async def publish_daily_proof_review(
    pool,
    client,
    *,
    now: Optional[datetime] = None,
    schedule: Optional[DailyProofSchedule] = None,
) -> bool:
    channel_id = client.config.proof_channel_id
    if not channel_id:
        return False
    period = latest_completed_reporting_period(now=now, schedule=schedule)
    claim = await _claim_daily_report(pool, period)
    if not claim:
        return False
    embed = build_daily_proof_embed(
        claim["report"], os.getenv("FRONTEND_URL", "https://app.eafc.live")
    )
    status, body = await client.post_embed(
        channel_id, embed, nonce=claim["nonce"], enforce_nonce=True
    )
    message_id = body.get("id") if status in (200, 201) and isinstance(body, dict) else None
    if message_id is not None:
        publication_status, error = "sent", None
    elif status == 429:
        publication_status, error = "retryable", "discord_http_429"
    elif status >= 500:
        publication_status, error = "retryable", f"discord_http_{status}"
    else:
        publication_status = "abandoned"
        error = "invalid_discord_response" if status in (200, 201) else f"discord_http_{status or 'network'}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE discord_daily_proof_publications
            SET status=$2, discord_message_id=$3, published_at=CASE WHEN $2='sent' THEN NOW() ELSE NULL END,
                last_error=$4, updated_at=NOW()
            WHERE report_date=$1 AND status='sending'
            """,
            claim["report_date"],
            publication_status,
            str(message_id) if message_id is not None else None,
            error,
        )
    return publication_status == "sent"
