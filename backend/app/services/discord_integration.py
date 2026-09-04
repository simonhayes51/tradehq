from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import aiohttp

from app.auth.entitlements import user_is_entitled
from app.services.discord_daily_proof import publish_daily_proof_review

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

    async def get_channel(self, channel_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/channels/{channel_id}")

    async def get_guild_roles(self, guild_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/guilds/{guild_id}/roles")

    async def get_guild_channels(self, guild_id: str) -> tuple[int, Any]:
        """Used by the web Server Feed settings page to populate a channel
        picker - not needed by the Discord command flow, which already has
        Discord's own native channel-select autocomplete for free."""
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def get_guild_member_in(self, guild_id: str, discord_user_id: str) -> tuple[int, Any]:
        """Guild-parameterized twin of get_member(), which is hardcoded to
        self.config.guild_id (the single Official server). Used for
        arbitrary partner/self-service guilds - see
        discord_feed_billing.py's channel permission check."""
        return await self._request("GET", f"/guilds/{guild_id}/members/{discord_user_id}")

    async def get_bot_user(self) -> tuple[int, Any]:
        return await self._request("GET", "/users/@me")

    async def get_guild(self, guild_id: str) -> tuple[int, Any]:
        """Used to read owner_id - a guild's owner has full permissions
        regardless of roles, and Discord doesn't reflect that in the
        member/role permission bits."""
        return await self._request("GET", f"/guilds/{guild_id}")

    async def post_embed(
        self,
        channel_id: str,
        embed: dict[str, Any],
        *,
        nonce: str,
        enforce_nonce: bool,
        components: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[int, Any]:
        if not channel_id or not embed:
            return 0, {"message": "Missing channel or embed"}
        payload: dict[str, Any] = {
            "embeds": [embed],
            "nonce": nonce,
            "enforce_nonce": enforce_nonce,
            "allowed_mentions": {"parse": []},
        }
        if components:
            payload["components"] = components
        return await self._request(
            "POST",
            f"/channels/{channel_id}/messages",
            payload=payload,
        )


async def desired_member_access(conn, user_id: str) -> tuple[Optional[str], bool]:
    """(discord_user_id, should_have_the_paid-member role).

    The entitlement half is delegated to app/auth/entitlements.py so
    Discord cannot disagree with the rest of the product about whether
    someone is paid. It used to compute its own answer here, which
    diverged in three ways - no ADMIN_DISCORD_IDS, no
    user_profiles.is_premium, and an unconditional "not paid" for anyone
    without a linked Discord id - and produced the "Current plan: ELITE /
    Membership access: Free" contradiction on the Account page.

    The discord_id half is unchanged and still returns None when there is
    no linked identity: without one there is no member to grant a role
    to, whatever the user is entitled to. The two are now independent,
    which is the point: "you are entitled" and "we can act on it in
    Discord" are different facts and the UI can say so.
    """
    user = await conn.fetchrow("SELECT discord_id FROM users WHERE id = $1", user_id)
    if not user:
        return None, False
    discord_user_id = str(user["discord_id"]) if user["discord_id"] else None
    desired = await user_is_entitled(conn, user_id, discord_id=discord_user_id)
    return discord_user_id, desired


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


def build_opportunity_digest(
    rows: Iterable[dict[str, Any]], app_url: str
) -> dict[str, Any]:
    opportunities = list(rows)
    count = len(opportunities)
    signal_counts = {
        "strong_buy": sum(row.get("signal") == "strong_buy" for row in opportunities),
        "buy": sum(row.get("signal") == "buy" for row in opportunities),
    }
    confidences = [
        float(row["confidence_score"])
        for row in opportunities
        if row.get("confidence_score") is not None
    ]
    liquidity = {"high": 0, "moderate": 0, "limited": 0}
    for row in opportunities:
        score = row.get("liquidity_score")
        if score is not None and float(score) >= 0.7:
            liquidity["high"] += 1
        elif score is not None and float(score) >= 0.4:
            liquidity["moderate"] += 1
        else:
            liquidity["limited"] += 1
    risk_counts: dict[str, int] = {}
    for row in opportunities:
        risk = str(row.get("risk_level") or "unknown").strip().lower()
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
    budgets = {"under_25k": 0, "25k_100k": 0, "over_100k": 0}
    for row in opportunities:
        price = row.get("current_executable_buy")
        if price is None or float(price) < 25_000:
            budgets["under_25k"] += 1
        elif float(price) < 100_000:
            budgets["25k_100k"] += 1
        else:
            budgets["over_100k"] += 1

    risk_text = " | ".join(
        f"{risk.title()}: {risk_counts[risk]}" for risk in sorted(risk_counts)
    )
    confidence_text = (
        f"Average confidence: {sum(confidences) / len(confidences):.0%}"
        if confidences
        else "Average confidence: unavailable"
    )
    opportunity_url = app_url.rstrip("/") + "/#/opportunities"
    return {
        "title": "EAFC.LIVE - OPPORTUNITY UPDATE",
        "url": opportunity_url,
        "description": (
            f"{count} genuinely new opportunities have qualified since the previous update."
        ),
        "color": 0x22C55E,
        "fields": [
            {
                "name": "SIGNAL QUALITY",
                "value": (
                    f"Strong buy: {signal_counts['strong_buy']} | Buy: {signal_counts['buy']}\n"
                    f"{confidence_text}"
                ),
                "inline": False,
            },
            {
                "name": "LIQUIDITY",
                "value": (
                    f"High: {liquidity['high']} | Moderate: {liquidity['moderate']} | "
                    f"Limited: {liquidity['limited']}"
                ),
                "inline": False,
            },
            {"name": "RISK MIX", "value": risk_text, "inline": False},
            {
                "name": "BUDGET MIX",
                "value": (
                    f"Under 25k: {budgets['under_25k']} | 25k-100k: {budgets['25k_100k']} | "
                    f"100k+: {budgets['over_100k']}"
                ),
                "inline": False,
            },
            {
                "name": "VIEW LIVE OPPORTUNITIES",
                "value": f"[Open EAFC.Live]({opportunity_url})",
                "inline": False,
            },
        ],
        "footer": {
            "text": "Live conditions change. Open EAFC.Live and check current status before buying."
        },
    }


def assert_public_opportunity_payload(payload: dict[str, Any]) -> None:
    leaked = PUBLIC_OPPORTUNITY_FORBIDDEN_FIELDS.intersection(payload)
    if leaked:
        raise ValueError(f"Public Discord opportunity payload contains forbidden fields: {sorted(leaked)}")


def publication_fingerprint(kind: str, values: Iterable[Any]) -> str:
    raw = json.dumps([kind, *list(values)], sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def publish_opportunity_digest(pool, client: Optional[DiscordClient] = None) -> bool:
    client = client or DiscordClient()
    channel_id = client.config.opportunity_channel_id
    if not channel_id:
        return False
    cooldown_minutes = int(os.getenv("DISCORD_OPPORTUNITY_COOLDOWN_MINUTES") or "180")
    dedup_hours = int(os.getenv("DISCORD_OPPORTUNITY_DEDUP_HOURS") or "24")
    async with pool.acquire() as conn:
        cooldown = await conn.fetchval(
            """SELECT EXISTS (
                SELECT 1 FROM discord_publications
                WHERE kind='opportunity_digest' AND status='sent'
                  AND sent_at > NOW() - ($1::integer * INTERVAL '1 minute')
            )""",
            cooldown_minutes,
        )
        if cooldown:
            return False
        rows = await conn.fetch(
            """
            WITH last_digest AS (
                SELECT MAX(sent_at) AS sent_at
                FROM discord_publications
                WHERE kind='opportunity_digest' AND status='sent'
            ), first_qualifying AS (
                SELECT DISTINCT ON (candidate.source_card_id)
                       candidate.id, candidate.source_card_id,
                       candidate.current_executable_buy, candidate.risk_level,
                       candidate.signal, candidate.confidence_score, candidate.liquidity_score
                FROM futgg_recommendation_snapshots candidate
                CROSS JOIN last_digest
                WHERE candidate.signal IN ('buy', 'strong_buy')
                  AND candidate.evaluated_at > GREATEST(
                      COALESCE(
                          last_digest.sent_at,
                          NOW() - ($1::integer * INTERVAL '1 minute')
                      ),
                      NOW() - INTERVAL '12 hours'
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
                    AND published.sent_at > NOW() - ($2::integer * INTERVAL '1 hour')
              )
            """,
            cooldown_minutes,
            dedup_hours,
        )
        if len(rows) < int(os.getenv("DISCORD_OPPORTUNITY_BATCH_MIN") or "5"):
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
    embed = build_opportunity_digest(
        [dict(row) for row in rows], os.getenv("FRONTEND_URL", "https://app.eafc.live")
    )
    status, _ = await client.post_embed(
        channel_id, embed, nonce=source_key[:25], enforce_nonce=True
    )
    ok = status in (200, 201)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_publications SET status=$2, sent_at=CASE WHEN $2='sent' THEN NOW() ELSE NULL END, last_error=$3 WHERE id=$1",
            inserted,
            "sent" if ok else "failed",
            None if ok else "discord_post_failed",
        )
    return ok


async def discord_worker_loop(pool, poll_seconds: int = 60) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            client = DiscordClient()
            await process_role_sync_queue(pool, client)
            await publish_opportunity_digest(pool, client)
            await publish_daily_proof_review(pool, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Discord integration worker pass failed", exc_info=True)
        await asyncio.sleep(poll_seconds)
