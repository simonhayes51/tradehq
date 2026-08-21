from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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

        status_matches_engine = {
            "profitable_unrealised": expected_roi > Decimal("0.005")
            and not row.get("downside_hit"),
            "loss_unrealised": expected_roi < Decimal("-0.005")
            and not row.get("downside_hit"),
            "flat": abs(expected_roi) <= Decimal("0.005") and not row.get("downside_hit"),
            "downside_hit": bool(row.get("downside_hit"))
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
        "entry": int(row["entry_price"]),
        "exit": int(row["outcome_price"]),
        "exit_type": str(row["outcome_price_type"]),
        "result": str(result),
        "roi": str(result / entry),
        "status": str(row["outcome_status"]),
    }


def build_daily_proof_report(
    rows: list[dict[str, Any]], period: ReportingPeriod
) -> Optional[dict[str, Any]]:
    eligible = [row for row in rows if proof_row_is_defensible(row, period)]
    if not eligible:
        return None

    wins = [row for row in eligible if row["outcome_status"] in WIN_STATUSES]
    losses = [row for row in eligible if row["outcome_status"] in LOSS_STATUSES]
    flats = [row for row in eligible if row["outcome_status"] == "flat"]
    results = [Decimal(str(row["net_profit_after_tax"])) for row in eligible]
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
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate": str(Decimal(len(wins)) / Decimal(total)),
        "combined_result": str(sum(results, Decimal("0"))),
        "average_result": str(sum(results, Decimal("0")) / Decimal(total)),
        "median_result": str(median(results)),
        "top_wins": [_report_item(row) for row in top_wins],
        "worst_loss": _report_item(worst_loss) if worst_loss else None,
    }


def _coins(value: Any, *, signed: bool = False) -> str:
    amount = Decimal(str(value))
    return f"{amount:+,.0f}" if signed else f"{amount:,.0f}"


def _result_line(item: dict[str, Any], rank: Optional[int] = None) -> str:
    prefix = f"{rank}. " if rank is not None else ""
    exit_label = "Target exit" if item["exit_type"] == "target_exit" else "24h mark"
    roi = Decimal(str(item["roi"])) * Decimal("100")
    return (
        f"**{prefix}{item['name']}**\n"
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
                f"{report['total']} opportunities completed\n"
                f"Wins: {report['wins']} | Losses: {report['losses']} | Flat: {report['flats']}\n"
                f"Win rate: {win_rate:.1f}%"
            ),
            "inline": False,
        },
        {
            "name": "COMBINED AFTER-TAX OPPORTUNITY RESULT",
            "value": f"**{_coins(result, signed=True)} coins**",
            "inline": False,
        },
        {
            "name": "TYPICAL RESULT",
            "value": (
                f"Average: {_coins(report['average_result'], signed=True)} coins\n"
                f"Median: {_coins(report['median_result'], signed=True)} coins"
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
                "Recorded 24-hour outcomes for independent EAFC Live opportunities. "
                "They are not an actual user portfolio and do not assume every opportunity "
                "was purchased. Past performance does not guarantee future results."
            ),
            "inline": False,
        }
    )
    color = 0x22C55E if result > 0 else 0xEF4444 if result < 0 else 0xEAB308
    return {
        "title": "EAFC LIVE - DAILY TRADING REVIEW",
        "url": app_url.rstrip("/") + "/#/v2/opportunities",
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
           o.target_hit, o.downside_hit, o.max_adverse_excursion,
           o.minutes_to_target,
           s.evaluated_at, s.recommended_sell_target,
           p.name,
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
      AND o.horizon='24h'
      AND o.graded_at >= $1 AND o.graded_at < $2
      AND o.outcome_status = ANY($3::text[])
      AND o.observation_count >= 2
      AND o.entry_achieved=TRUE
      AND o.entry_at IS NOT NULL AND o.entry_price > 0
      AND o.outcome_price IS NOT NULL
      AND o.outcome_observed_at IS NOT NULL
      AND o.net_profit_after_tax IS NOT NULL
      AND o.realised_roi IS NOT NULL
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
