from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

import aiohttp

log = logging.getLogger("discord_integration")

ACTIVE_STATUSES = frozenset({"active", "trialing", "trial", "past_due"})
MANUAL_PREMIUM_TIERS = frozenset({"pro", "premium", "elite"})
PUBLIC_OPPORTUNITY_FORBIDDEN_FIELDS = frozenset(
    {
        "source_card_id",
        "name",
        "player",
        "buy_target",
        "recommended_buy_max",
        "sell_target",
        "recommended_sell_target",
        "expected_profit",
        "expected_profit_after_tax",
        "expected_roi",
        "roi",
    }
)
PROOF_CADENCE_MINUTES = 30
PROOF_BATCH_MAX = 5
PROOF_STATUSES = frozenset(
    {"target_hit", "profitable_unrealised", "flat", "loss_unrealised", "downside_hit"}
)
PROOF_CLAIM_LOCK_ID = 731_904_057


def _env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return None


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: Optional[str]
    guild_id: Optional[str]
    member_role_id: Optional[str]
    opportunity_channel_id: Optional[str]
    proof_channel_id: Optional[str]
    invite_url: Optional[str]

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        return cls(
            bot_token=_env("DISCORD_BOT_TOKEN"),
            guild_id=_env("DISCORD_GUILD_ID", "DISCORD_SERVER_ID"),
            member_role_id=_env("DISCORD_MEMBER_ROLE_ID", "DISCORD_PREMIUM_ROLE_ID"),
            opportunity_channel_id=_env("DISCORD_OPPORTUNITY_CHANNEL_ID"),
            proof_channel_id=_env("DISCORD_PROOF_CHANNEL_ID"),
            invite_url=_env("DISCORD_INVITE_URL"),
        )

    @property
    def role_sync_ready(self) -> bool:
        return bool(self.bot_token and self.guild_id and self.member_role_id)


@dataclass(frozen=True)
class DiscordMemberState:
    state: str
    roles: frozenset[str] = frozenset()
    error: Optional[str] = None


def entitlement_is_active(
    *,
    status: Optional[str],
    current_period_end: Optional[datetime],
    plan: Optional[str] = None,
    tier: Optional[str] = None,
    account_type: Optional[str] = None,
    premium_until: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> bool:
    """The website/account billing state is authoritative; Discord is not."""
    if (account_type or "").lower() == "admin":
        return True
    if (plan or "").lower() in MANUAL_PREMIUM_TIERS:
        return True
    if (tier or "").lower() in MANUAL_PREMIUM_TIERS:
        return True
    if premium_until is not None:
        if premium_until.tzinfo is None:
            premium_until = premium_until.replace(tzinfo=timezone.utc)
        if premium_until > (now or datetime.now(timezone.utc)):
            return True
    if (status or "").lower() not in ACTIVE_STATUSES:
        return False
    if current_period_end is None:
        return True
    if current_period_end.tzinfo is None:
        current_period_end = current_period_end.replace(tzinfo=timezone.utc)
    return current_period_end > (now or datetime.now(timezone.utc))


class DiscordClient:
    def __init__(self, config: Optional[DiscordConfig] = None):
        self.config = config or DiscordConfig.from_env()

    async def _request(self, method: str, path: str, *, payload: Optional[dict] = None) -> tuple[int, Any]:
        if not self.config.bot_token:
            return 0, {"message": "Discord bot is not configured"}
        headers = {
            "Authorization": f"Bot {self.config.bot_token}",
            "Content-Type": "application/json",
        }
        url = f"https://discord.com/api/v10{path}"
        timeout = aiohttp.ClientTimeout(total=8)
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                    async with session.request(method, url, json=payload) as response:
                        body: Any = None
                        if response.content_type == "application/json":
                            body = await response.json(content_type=None)
                        else:
                            body = await response.text()
                        if response.status == 429 and attempt < 2:
                            retry_after = body.get("retry_after", 1) if isinstance(body, dict) else 1
                            await asyncio.sleep(min(float(retry_after), 3.0))
                            continue
                        if response.status >= 500 and attempt < 2:
                            await asyncio.sleep(0.25 * (2**attempt))
                            continue
                        return response.status, body
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    return 0, {"message": type(exc).__name__}
                await asyncio.sleep(0.25 * (2**attempt))
        return 0, {"message": "Discord request failed"}

    async def get_member(self, discord_user_id: str) -> DiscordMemberState:
        if not self.config.role_sync_ready:
            return DiscordMemberState("unconfigured")
        status, body = await self._request(
            "GET", f"/guilds/{self.config.guild_id}/members/{discord_user_id}"
        )
        if status == 404:
            return DiscordMemberState("not_joined")
        if status != 200 or not isinstance(body, dict):
            return DiscordMemberState("unavailable", error=f"discord_http_{status or 'network'}")
        roles = body.get("roles")
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            return DiscordMemberState("unavailable", error="invalid_member_response")
        return DiscordMemberState("joined", frozenset(roles))

    async def reconcile_member_role(self, discord_user_id: str, desired: bool) -> DiscordMemberState:
        member = await self.get_member(discord_user_id)
        if member.state != "joined":
            return member
        role_id = str(self.config.member_role_id)
        has_role = role_id in member.roles
        if has_role == desired:
            return member
        method = "PUT" if desired else "DELETE"
        status, _ = await self._request(
            method,
            f"/guilds/{self.config.guild_id}/members/{discord_user_id}/roles/{role_id}",
        )
        if status not in (200, 204):
            return DiscordMemberState("unavailable", member.roles, f"role_http_{status or 'network'}")
        roles = set(member.roles)
        roles.add(role_id) if desired else roles.discard(role_id)
        return DiscordMemberState("joined", frozenset(roles))

    async def post_message(self, channel_id: str, content: str) -> bool:
        if not channel_id or not content:
            return False
        status, body = await self._request(
            "POST", f"/channels/{channel_id}/messages", payload={"content": content}
        )
        if status not in (200, 201):
            log.warning("Discord channel post failed: channel=%s status=%s body=%s", channel_id, status, str(body)[:200])
            return False
        return True

    async def post_embeds(self, channel_id: str, embeds: list[dict[str, Any]]) -> tuple[int, Any]:
        if not channel_id or not embeds:
            return 0, {"message": "Missing channel or embeds"}
        return await self._request(
            "POST", f"/channels/{channel_id}/messages", payload={"embeds": embeds}
        )


async def desired_member_access(conn, user_id: str) -> tuple[Optional[str], bool]:
    user = await conn.fetchrow(
        "SELECT discord_id, plan, tier, account_type, premium_until FROM users WHERE id = $1", user_id
    )
    if not user or not user["discord_id"]:
        return None, False
    subscription = await conn.fetchrow(
        """
        SELECT status, current_period_end
        FROM subscriptions
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id,
    )
    desired = entitlement_is_active(
        status=subscription["status"] if subscription else None,
        current_period_end=subscription["current_period_end"] if subscription else None,
        plan=user["plan"],
        tier=user["tier"],
        account_type=user["account_type"],
        premium_until=user["premium_until"],
    )
    return str(user["discord_id"]), desired


async def enqueue_role_sync(
    conn,
    user_id: str,
    *,
    discord_user_id: Optional[str] = None,
    desired: Optional[bool] = None,
) -> bool:
    if discord_user_id is None or desired is None:
        resolved_id, resolved_desired = await desired_member_access(conn, user_id)
        discord_user_id = discord_user_id or resolved_id
        desired = resolved_desired if desired is None else desired
    if not discord_user_id:
        return False
    await conn.execute(
        """
        INSERT INTO discord_role_sync_queue (
            user_id, discord_user_id, desired_member_role, status,
            attempts, next_attempt_at, updated_at
        ) VALUES ($1, $2, $3, 'pending', 0, NOW(), NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            discord_user_id = EXCLUDED.discord_user_id,
            desired_member_role = EXCLUDED.desired_member_role,
            status = 'pending', attempts = 0, next_attempt_at = NOW(),
            last_error = NULL, updated_at = NOW()
        """,
        user_id,
        str(discord_user_id),
        bool(desired),
    )
    return True


async def claim_stripe_event(conn, event_id: str, event_type: str) -> str:
    """Atomically claim a new or stale Stripe event for processing."""
    claimed = await conn.fetchval(
        """
        INSERT INTO stripe_events (event_id, event_type, processing_started_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        event_id,
        event_type,
    )
    if claimed is not None:
        return "claimed"

    claimed = await conn.fetchval(
        """
        UPDATE stripe_events
        SET processing_started_at=NOW(), processing_error=NULL
        WHERE event_id=$1
          AND processed_at IS NULL
          AND (
            processing_started_at IS NULL
            OR processing_started_at <= NOW() - INTERVAL '5 minutes'
          )
        RETURNING event_id
        """,
        event_id,
    )
    if claimed is not None:
        return "claimed"

    processed_at = await conn.fetchval(
        "SELECT processed_at FROM stripe_events WHERE event_id=$1",
        event_id,
    )
    return "processed" if processed_at else "processing"


async def _finish_role_sync(conn, user_id: str, member: DiscordMemberState) -> None:
    if member.state == "joined":
        await conn.execute(
            """
            UPDATE discord_role_sync_queue
            SET status='synced', attempts=attempts+1, last_attempt_at=NOW(),
                synced_at=NOW(), next_attempt_at=NULL, last_error=NULL, updated_at=NOW()
            WHERE user_id=$1
            """,
            user_id,
        )
        return
    delay_minutes = 60 if member.state == "not_joined" else 5
    await conn.execute(
        """
        UPDATE discord_role_sync_queue
        SET status=$2, attempts=attempts+1, last_attempt_at=NOW(),
            next_attempt_at=NOW() + ($3::integer * INTERVAL '1 minute'),
            last_error=$4, updated_at=NOW()
        WHERE user_id=$1
        """,
        user_id,
        member.state,
        delay_minutes,
        member.error,
    )


async def reconcile_user(conn, user_id: str, client: Optional[DiscordClient] = None) -> dict[str, Any]:
    client = client or DiscordClient()
    queued = await conn.fetchrow(
        "SELECT discord_user_id, desired_member_role FROM discord_role_sync_queue WHERE user_id=$1",
        user_id,
    )
    if not queued:
        if not await enqueue_role_sync(conn, user_id):
            return {"guild_membership": "unavailable", "role_sync": "no_discord_identity"}
        queued = await conn.fetchrow(
            "SELECT discord_user_id, desired_member_role FROM discord_role_sync_queue WHERE user_id=$1",
            user_id,
        )
    member = await client.reconcile_member_role(
        str(queued["discord_user_id"]), bool(queued["desired_member_role"])
    )
    await _finish_role_sync(conn, user_id, member)
    has_member_role = (
        member.state == "joined"
        and str(client.config.member_role_id) in member.roles
    )
    return {
        "guild_membership": member.state,
        "role_sync": "synced" if member.state == "joined" else member.state,
        "has_member_role": has_member_role,
        "desired_member_role": bool(queued["desired_member_role"]),
    }


async def process_role_sync_queue(pool, client: Optional[DiscordClient] = None, limit: int = 25) -> int:
    client = client or DiscordClient()
    if not client.config.role_sync_ready:
        return 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id FROM discord_role_sync_queue
            WHERE status <> 'synced'
              AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
            ORDER BY next_attempt_at NULLS FIRST, updated_at
            LIMIT $1
            """,
            limit,
        )
    processed = 0
    for row in rows:
        try:
            async with pool.acquire() as conn:
                await reconcile_user(conn, row["user_id"], client)
            processed += 1
        except Exception:
            log.warning("Discord role reconciliation failed for user=%s", row["user_id"], exc_info=True)
    return processed


def _coin_band(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return max(1000, int(round(float(value) / 10000.0) * 10000))


def build_opportunity_digest(
    *, count: int, min_coin: Optional[float], max_coin: Optional[float], risks: Iterable[str], app_url: str
) -> str:
    lines = [f"{count} new EAFC Live opportunities detected"]
    low, high = _coin_band(min_coin), _coin_band(max_coin)
    if low is not None and high is not None:
        lines.append(f"Coin range: {low:,}-{high:,}")
    clean_risks = sorted({str(r).strip().title() for r in risks if r})
    if clean_risks:
        lines.append(f"Risk: {'-'.join(clean_risks)}")
    lines.extend(["", f"View live opportunities: {app_url.rstrip('/')}/#/v2/opportunities"])
    return "\n".join(lines)


def assert_public_opportunity_payload(payload: dict[str, Any]) -> None:
    leaked = PUBLIC_OPPORTUNITY_FORBIDDEN_FIELDS.intersection(payload)
    if leaked:
        raise ValueError(f"Public Discord opportunity payload contains forbidden fields: {sorted(leaked)}")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def proof_row_is_defensible(row: dict[str, Any]) -> bool:
    try:
        enabled_at = _aware(row["proof_enabled_at"])
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
        target = Decimal(str(row["recommended_sell_target"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False

    if len(card_ids) != 1 or status not in PROOF_STATUSES:
        return False
    if evaluated_at < enabled_at or graded_at < enabled_at:
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
    if abs(recorded_profit - expected_profit) > Decimal("0.01"):
        return False

    if status == "target_hit":
        if not row.get("target_hit") or not row.get("exit_achieved"):
            return False
        if row.get("outcome_price_type") != "target_exit" or outcome_price < target:
            return False
        if row.get("realised_sell_price") != row.get("outcome_price"):
            return False
        if row.get("exit_at") != row.get("outcome_observed_at"):
            return False
        elapsed = int((outcome_at - entry_at).total_seconds() // 60)
        return row.get("minutes_to_target") == elapsed

    realised_roi = recorded_profit / entry
    status_matches_math = {
        "profitable_unrealised": realised_roi > Decimal("0.005") and not row.get("downside_hit"),
        "loss_unrealised": realised_roi < Decimal("-0.005") and not row.get("downside_hit"),
        "flat": abs(realised_roi) <= Decimal("0.005") and not row.get("downside_hit"),
        "downside_hit": bool(row.get("downside_hit"))
        and Decimal(str(row.get("max_adverse_excursion"))) <= Decimal("-0.10"),
    }
    return (
        not row.get("target_hit")
        and not row.get("exit_achieved")
        and row.get("outcome_price_type") == "window_mark"
        and row.get("minutes_to_target") is None
        and status_matches_math.get(status, False)
    )


def build_proof_embed(row: dict[str, Any], app_url: str) -> dict[str, Any]:
    if not proof_row_is_defensible(row):
        raise ValueError("Outcome does not have defensible proof provenance")
    status = str(row["outcome_status"]).replace("_", " ").title()
    profit = Decimal(str(row["net_profit_after_tax"]))
    minutes = int(
        (_aware(row["outcome_observed_at"]) - _aware(row["entry_at"])).total_seconds() // 60
    )
    colors = {
        "target_hit": 0x22C55E,
        "profitable_unrealised": 0x84CC16,
        "flat": 0xEAB308,
        "loss_unrealised": 0xEF4444,
        "downside_hit": 0xB91C1C,
    }
    exit_label = "Target exit" if row["outcome_price_type"] == "target_exit" else "24h mark"
    return {
        "title": str(row["name"]),
        "url": app_url.rstrip("/") + "/#/v2/opportunities",
        "description": "Expired outcome. This is historical proof, not a live recommendation.",
        "color": colors[str(row["outcome_status"])],
        "fields": [
            {"name": "Result", "value": status, "inline": True},
            {"name": "Entry", "value": f"{int(row['entry_price']):,}", "inline": True},
            {"name": exit_label, "value": f"{int(row['outcome_price']):,}", "inline": True},
            {"name": "After-tax outcome", "value": f"{profit:+,.0f}", "inline": True},
            {"name": "Time active", "value": f"{minutes} minutes", "inline": True},
        ],
        "footer": {
            "text": f"EAFC Live • Card {row['outcome_source_card_id']} • Detected {row['evaluated_at'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        },
        "timestamp": _aware(row["outcome_observed_at"]).isoformat(),
    }


def publication_fingerprint(kind: str, values: Iterable[Any]) -> str:
    raw = json.dumps([kind, *list(values)], sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def publish_opportunity_digest(pool, client: Optional[DiscordClient] = None) -> bool:
    client = client or DiscordClient()
    channel_id = client.config.opportunity_channel_id
    if not channel_id:
        return False
    async with pool.acquire() as conn:
        cooldown = await conn.fetchval(
            """SELECT EXISTS (
                SELECT 1 FROM discord_publications
                WHERE kind='opportunity_digest' AND status='sent'
                  AND sent_at > NOW() - INTERVAL '30 minutes'
            )"""
        )
        if cooldown:
            return False
        rows = await conn.fetch(
            """
            WITH first_qualifying AS (
                SELECT DISTINCT ON (candidate.source_card_id)
                       candidate.id, candidate.source_card_id,
                       candidate.current_executable_buy, candidate.risk_level
                FROM futgg_recommendation_snapshots candidate
                WHERE candidate.signal IN ('buy', 'strong_buy')
                  AND candidate.evaluated_at > NOW() - INTERVAL '30 minutes'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM futgg_recommendation_snapshots earlier
                    WHERE earlier.source_card_id=candidate.source_card_id
                      AND earlier.signal IN ('buy', 'strong_buy')
                      AND earlier.evaluated_at < candidate.evaluated_at
                      AND earlier.evaluated_at > candidate.evaluated_at - INTERVAL '6 hours'
                  )
                ORDER BY candidate.source_card_id, candidate.evaluated_at
            ), latest AS (
                SELECT DISTINCT ON (source_card_id)
                       source_card_id, signal, expires_at, invalidated_at
                FROM futgg_recommendation_snapshots
                ORDER BY source_card_id, evaluated_at DESC
            )
            SELECT first_qualifying.*
            FROM first_qualifying
            JOIN latest USING (source_card_id)
            WHERE latest.signal IN ('buy', 'strong_buy')
              AND latest.invalidated_at IS NULL
              AND (latest.expires_at IS NULL OR latest.expires_at > NOW())
              AND NOT EXISTS (
                  SELECT 1
                  FROM discord_publications published
                  WHERE published.kind='opportunity_digest'
                    AND published.payload @> jsonb_build_object(
                        'source_card_ids', jsonb_build_array(first_qualifying.source_card_id::text)
                    )
                    AND published.sent_at > NOW() - INTERVAL '6 hours'
              )
            """
        )
        if len(rows) < int(os.getenv("DISCORD_OPPORTUNITY_BATCH_MIN", "3")):
            return False
        source_key = publication_fingerprint("opportunity_digest", sorted(r["id"] for r in rows))
        inserted = await conn.fetchval(
            """
            INSERT INTO discord_publications (kind, source_key, status, payload)
            VALUES ('opportunity_digest', $1, 'sending', $2::jsonb)
            ON CONFLICT (kind, source_key) DO UPDATE
            SET status='sending', last_error=NULL
            WHERE discord_publications.status='failed'
            RETURNING id
            """,
            source_key,
            json.dumps({
                "count": len(rows),
                "source_card_ids": [str(row["source_card_id"]) for row in rows],
            }),
        )
        if inserted is None:
            return False
    prices = [r["current_executable_buy"] for r in rows if r["current_executable_buy"] is not None]
    message = build_opportunity_digest(
        count=len(rows),
        min_coin=min(prices) if prices else None,
        max_coin=max(prices) if prices else None,
        risks=[r["risk_level"] for r in rows],
        app_url=os.getenv("FRONTEND_URL", "https://app.eafc.live"),
    )
    ok = await client.post_message(channel_id, message)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_publications SET status=$2, sent_at=CASE WHEN $2='sent' THEN NOW() ELSE NULL END, last_error=$3 WHERE id=$1",
            inserted,
            "sent" if ok else "failed",
            None if ok else "discord_post_failed",
        )
    return ok


async def _claim_proof_batch(pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", PROOF_CLAIM_LOCK_ID)
            if not locked:
                return []
            cooling_down = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM discord_publications
                    WHERE kind='proof_outcome'
                      AND status IN ('sending', 'sent')
                      AND COALESCE(sent_at, created_at) >
                          NOW() - ($1::integer * INTERVAL '1 minute')
                )
                """,
                PROOF_CADENCE_MINUTES,
            )
            if cooling_down:
                return []
            rows = await conn.fetch(
                """
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
                       s.rating, s.rarity, p.name, st.proof_enabled_at,
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
                WHERE st.proof_enabled_at IS NOT NULL
                  AND s.evaluated_at >= st.proof_enabled_at
                  AND o.graded_at >= st.proof_enabled_at
                  AND o.horizon='24h'
                  AND o.outcome_status = ANY($1::text[])
                  AND o.observation_count >= 2
                  AND o.entry_achieved=TRUE
                  AND o.entry_at IS NOT NULL AND o.entry_price > 0
                  AND o.outcome_price IS NOT NULL
                  AND o.outcome_observed_at IS NOT NULL
                  AND o.net_profit_after_tax IS NOT NULL
                  AND (s.invalidated_at IS NOT NULL OR s.expires_at <= NOW())
                  AND NOT EXISTS (
                      SELECT 1 FROM futgg_recommendation_snapshots live
                      WHERE live.source_card_id=o.source_card_id
                        AND live.evaluated_at > s.evaluated_at
                        AND live.signal IN ('buy', 'strong_buy')
                        AND live.invalidated_at IS NULL
                        AND (live.expires_at IS NULL OR live.expires_at > NOW())
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM discord_publications dp
                      WHERE dp.kind='proof_outcome'
                        AND dp.source_key=o.snapshot_id::text
                        AND NOT (dp.status='failed' AND dp.last_error='discord_http_429')
                  )
                ORDER BY o.graded_at, o.snapshot_id
                LIMIT 25
                """,
                list(PROOF_STATUSES),
            )
            defensible = [dict(row) for row in rows if proof_row_is_defensible(dict(row))][
                :PROOF_BATCH_MAX
            ]
            if not defensible:
                return []
            batch_id = str(uuid.uuid4())
            claimed: list[dict[str, Any]] = []
            for row in defensible:
                publication_id = await conn.fetchval(
                    """
                    INSERT INTO discord_publications (kind, source_key, status, payload)
                    VALUES ('proof_outcome', $1, 'sending', $2::jsonb)
                    ON CONFLICT (kind, source_key) DO UPDATE
                    SET status='sending', last_error=NULL, created_at=NOW()
                    WHERE discord_publications.status='failed'
                      AND discord_publications.last_error='discord_http_429'
                    RETURNING id
                    """,
                    str(row["snapshot_id"]),
                    json.dumps(
                        {
                            "snapshot_id": row["snapshot_id"],
                            "outcome_status": row["outcome_status"],
                            "batch_id": batch_id,
                        }
                    ),
                )
                if publication_id is not None:
                    row["publication_id"] = publication_id
                    claimed.append(row)
            return claimed


async def publish_proof_digest(pool, client: Optional[DiscordClient] = None) -> bool:
    client = client or DiscordClient()
    channel_id = client.config.proof_channel_id
    if not channel_id:
        return False
    rows = await _claim_proof_batch(pool)
    if not rows:
        return False
    app_url = os.getenv("FRONTEND_URL", "https://app.eafc.live")
    embeds = [build_proof_embed(row, app_url) for row in rows]
    status, _ = await client.post_embeds(channel_id, embeds)
    publication_ids = [row["publication_id"] for row in rows]
    if status in (200, 201):
        publication_status, error = "sent", None
    elif status == 429:
        publication_status, error = "failed", "discord_http_429"
    else:
        # At-most-once: an uncertain network/5xx result or a terminal 4xx
        # must never replay after a restart and risk duplicate public proof.
        publication_status, error = "abandoned", f"discord_http_{status or 'network'}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE discord_publications
            SET status=$2,
                sent_at=CASE WHEN $2='sent' THEN NOW() ELSE NULL END,
                last_error=$3
            WHERE id=ANY($1::bigint[])
            """,
            publication_ids,
            publication_status,
            error,
        )
    return publication_status == "sent"


async def discord_worker_loop(pool, poll_seconds: int = 60) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            client = DiscordClient()
            await process_role_sync_queue(pool, client)
            await publish_opportunity_digest(pool, client)
            await publish_proof_digest(pool, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Discord integration worker pass failed", exc_info=True)
        await asyncio.sleep(poll_seconds)
