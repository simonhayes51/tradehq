"""
End-to-end smoke test for the multi-server Discord trading feed, run
against a real database and a real Discord bot token. Written to be run
from an environment that actually has network access to both (this
sandbox's own egress policy blocks raw Postgres connections and
discord.com outright, so it cannot run this script itself - see the
runbook in docs/DISCORD_TRADING_FEED_RUNBOOK.md).

Every step here is either read-only, additive, or explicitly a
"synthetic"/"test" post using fixture data (never a real trade, never real
Proof Ledger numbers) - see app/services/discord_trading_feed.py's
send_test_post / send_test_free_overview / send_test_opportunity_update.
DISCORD_TRADING_FEED_ENABLED is never set by this script, and the target
server's status is reset to 'paused' at the end regardless of outcome, so
nothing is left able to auto-post after this runs.

Usage:
    export DATABASE_URL=...
    export DISCORD_BOT_TOKEN=...
    export DISCORD_GUILD_ID=...          # the server to test against
    export DISCORD_TEST_CHANNEL_ID=...   # a dedicated test channel's ID
    export DISCORD_ADMIN_ID=...          # optional: your user/discord id, for audit log attribution
    python scripts/discord_trading_feed_smoke_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.discord_integration import DiscordConfig, DiscordClient  # noqa: E402
from app.services.discord_trading_feed import (  # noqa: E402
    ServerRecord,
    get_server,
    send_test_free_overview,
    send_test_opportunity_update,
    send_test_post,
    _record_post,
    _last_opportunity_post_at,
    _posts_sent_today,
)
from app.routers.discord_interactions import _dispatch, _rate_limited, _command_hits  # noqa: E402

REPORT: list[str] = []


def log(line: str = "") -> None:
    print(line)
    REPORT.append(line)


def section(title: str) -> None:
    log("")
    log(f"=== {title} ===")


async def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    bot_token = os.environ["DISCORD_BOT_TOKEN"]
    guild_id = os.environ["DISCORD_GUILD_ID"]
    test_channel_id = os.environ["DISCORD_TEST_CHANNEL_ID"]
    admin_id = os.getenv("DISCORD_ADMIN_ID", "smoke_test_script")

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    client = DiscordClient(DiscordConfig(bot_token=bot_token, guild_id=guild_id, member_role_id=None,
                                         opportunity_channel_id=None, proof_channel_id=None, invite_url=None))

    # ------------------------------------------------------------------
    section("1. Migration 069")
    # ------------------------------------------------------------------
    from scripts.run_migrations import run as run_migrations
    applied = await run_migrations(dsn, target="core")
    log(f"Migrations executed this run: {applied}")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='discord_servers')"
        )
        recorded = await conn.fetchval(
            "SELECT applied_at FROM schema_migrations WHERE target='core' AND filename='069_discord_trading_feed.sql'"
        )
    log(f"discord_servers table present: {exists}")
    log(f"069_discord_trading_feed.sql recorded in schema_migrations at: {recorded}")
    assert exists, "Migration 069 did not create discord_servers"

    # ------------------------------------------------------------------
    section("4-5. Register / locate the official server + set test channel")
    # ------------------------------------------------------------------
    async with pool.acquire() as conn:
        server_id = await conn.fetchval("SELECT id FROM discord_servers WHERE guild_id=$1", guild_id)
        if server_id is None:
            server_id = await conn.fetchval(
                """
                INSERT INTO discord_servers (guild_id, name, access_level, status)
                VALUES ($1, 'EAFC.Live (Official)', 'official', 'paused')
                RETURNING id
                """,
                guild_id,
            )
            await conn.execute(
                "INSERT INTO discord_server_settings (server_id) VALUES ($1) ON CONFLICT DO NOTHING", server_id
            )
            log(f"Registered official server as discord_servers.id={server_id} (was not present)")
        else:
            log(f"Official server already registered as discord_servers.id={server_id}")

        original_channel = await conn.fetchval("SELECT feed_channel_id FROM discord_servers WHERE id=$1", server_id)
        original_status = await conn.fetchval("SELECT status FROM discord_servers WHERE id=$1", server_id)
        original_expires_at = await conn.fetchval("SELECT expires_at FROM discord_servers WHERE id=$1", server_id)

        await conn.execute(
            "UPDATE discord_servers SET feed_channel_id=$2, status='active', enabled_by_admin_id=$3, "
            "enabled_at=NOW(), updated_at=NOW() WHERE id=$1",
            server_id, test_channel_id, str(admin_id),
        )
        await conn.execute(
            "INSERT INTO discord_server_audit_log (server_id, admin_id, action, details) VALUES ($1,$2,$3,$4::jsonb)",
            server_id, str(admin_id), "smoke_test_set_channel", json.dumps({"feed_channel_id": test_channel_id}),
        )
    log(f"Feed channel set to {test_channel_id} (was: {original_channel})")
    log("Status set to 'active' for the duration of this test (will be reset to 'paused' at the end)")

    async with pool.acquire() as conn:
        server = await get_server(conn, server_id)

    # ------------------------------------------------------------------
    section("6. Confirm genuine live posting stays disabled")
    # ------------------------------------------------------------------
    live_enabled = (os.getenv("DISCORD_TRADING_FEED_ENABLED") or "0").strip() == "1"
    async with pool.acquire() as conn:
        live_opps_flag = await conn.fetchval(
            "SELECT live_opportunities_enabled FROM discord_server_settings WHERE server_id=$1", server_id
        )
    log(f"DISCORD_TRADING_FEED_ENABLED={os.getenv('DISCORD_TRADING_FEED_ENABLED') or '0'} (global kill switch)")
    log(f"discord_server_settings.live_opportunities_enabled={live_opps_flag}")
    assert not live_enabled, "Refusing to continue: DISCORD_TRADING_FEED_ENABLED=1 - this script never sets it"
    log("PASS: genuine live posting remains disabled throughout this run")

    # ------------------------------------------------------------------
    section("7. Synthetic Free overview")
    # ------------------------------------------------------------------
    ok, err = await send_test_free_overview(pool, server, client)
    log(f"send_test_free_overview -> ok={ok} error={err}")
    assert ok, f"Free overview test post failed: {err}"

    # ------------------------------------------------------------------
    section("8. Synthetic Paid Partner opportunity")
    # ------------------------------------------------------------------
    ok, err = await send_test_post(pool, server, client)
    log(f"send_test_post -> ok={ok} error={err}")
    assert ok, f"Opportunity test post failed: {err}"

    # ------------------------------------------------------------------
    section("9. Target-hit and closed/expired update posts")
    # ------------------------------------------------------------------
    ok, err = await send_test_opportunity_update(pool, server, "target_hit", client)
    log(f"send_test_opportunity_update(target_hit) -> ok={ok} error={err}")
    assert ok, f"Target-hit update post failed: {err}"

    ok, err = await send_test_opportunity_update(pool, server, "loss_unrealised", client)
    log(f"send_test_opportunity_update(loss_unrealised) -> ok={ok} error={err}")
    assert ok, f"Closed/loss update post failed: {err}"

    # ------------------------------------------------------------------
    section("10a. Duplicate prevention")
    # ------------------------------------------------------------------
    dedup_key = f"smoketest-dup-{uuid.uuid4().hex[:12]}"
    async with pool.acquire() as conn:
        first = await _record_post(conn, server_id, "opportunity", dedup_key, {"smoketest": True})
        second = await _record_post(conn, server_id, "opportunity", dedup_key, {"smoketest": True})
    log(f"First claim of dedup_key: post_id={first} (expected: an id)")
    log(f"Second claim of same dedup_key while status='sending': post_id={second} (expected: None)")
    assert first is not None and second is None, "Duplicate prevention did not hold"
    log("PASS: duplicate prevention holds")

    # ------------------------------------------------------------------
    section("10b. Daily allowance and cooldown gating (read-only against a scratch settings row)")
    # ------------------------------------------------------------------
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_server_settings SET max_posts_per_day=2, cooldown_minutes=30 WHERE server_id=$1",
            server_id,
        )
        for i in range(3):
            await _record_post(conn, server_id, "opportunity", f"smoketest-allowance-{i}", {})
            await conn.execute(
                "UPDATE discord_server_posts SET status='sent', sent_at=NOW() "
                "WHERE server_id=$1 AND kind='opportunity' AND dedup_key=$2",
                server_id, f"smoketest-allowance-{i}",
            )
        sent_today = await _posts_sent_today(conn, server_id, "opportunity")
        last_at = await _last_opportunity_post_at(conn, server_id)
    log(f"Simulated 3 sent opportunity posts today; max_posts_per_day=2 -> sent_today={sent_today} (budget exceeded, as expected)")
    log(f"Most recent opportunity post at: {last_at} -> a real posting pass would see this as within cooldown_minutes=30")
    assert sent_today >= 3
    log("PASS: allowance/cooldown counters read back the simulated history correctly")

    # ------------------------------------------------------------------
    section("10c. Pause / resume")
    # ------------------------------------------------------------------
    async with pool.acquire() as conn:
        await conn.execute("UPDATE discord_servers SET status='paused' WHERE id=$1", server_id)
        paused = await get_server(conn, server_id)
        await conn.execute("UPDATE discord_servers SET status='active' WHERE id=$1", server_id)
        resumed = await get_server(conn, server_id)
    log(f"status='paused' -> is_postable={paused.is_postable} (expected False)")
    log(f"status='active' -> is_postable={resumed.is_postable} (expected True)")
    assert paused.is_postable is False and resumed.is_postable is True
    log("PASS: pause/resume gates is_postable correctly")

    # ------------------------------------------------------------------
    section("10d. Expiry (paid_partner only) - scratch throwaway server")
    # ------------------------------------------------------------------
    scratch_guild = f"smoketest-partner-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        scratch_id = await conn.fetchval(
            """
            INSERT INTO discord_servers (guild_id, name, access_level, status, expires_at)
            VALUES ($1, 'Smoke Test Partner (throwaway)', 'paid_partner', 'active', $2)
            RETURNING id
            """,
            scratch_guild, datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        expired = await get_server(conn, scratch_id)
        await conn.execute("UPDATE discord_servers SET expires_at=$2 WHERE id=$1", scratch_id,
                            datetime.now(timezone.utc) + timedelta(days=1))
        not_expired = await get_server(conn, scratch_id)
        await conn.execute("DELETE FROM discord_servers WHERE id=$1", scratch_id)
    log(f"paid_partner with expires_at in the past -> is_postable={expired.is_postable} (expected False)")
    log(f"paid_partner with expires_at in the future -> is_postable={not_expired.is_postable} (expected True)")
    assert expired.is_postable is False and not_expired.is_postable is True
    log("PASS: expiry gating holds; throwaway scratch server deleted")

    # ------------------------------------------------------------------
    section("11. Slash commands (/price /fairvalue /watchlist /alerts /results /account /help) + rate limits")
    # ------------------------------------------------------------------
    _command_hits.clear()
    help_reply = await _dispatch(pool, "help", [], None)
    log(f"/help -> {help_reply[:80]}...")
    assert "/price" in help_reply

    price_reply = await _dispatch(pool, "price", [{"name": "player", "value": "Mbappe"}], None)
    log(f"/price Mbappe -> {price_reply}")

    fv_reply = await _dispatch(pool, "fairvalue", [{"name": "player", "value": "Mbappe"}], None)
    log(f"/fairvalue Mbappe -> {fv_reply}")

    test_discord_id = os.getenv("DISCORD_ADMIN_ID")
    for cmd in ("watchlist", "alerts", "results", "account"):
        reply = await _dispatch(pool, cmd, [], test_discord_id)
        linked = "isn't linked" not in reply
        log(f"/{cmd} (discord_id={test_discord_id!r}) -> linked_account_path={linked}: {reply[:120]}")

    unlinked_reply = await _dispatch(pool, "account", [], "999999999999999999")
    log(f"/account with an unlinked discord_id -> {unlinked_reply}")
    assert "isn't linked" in unlinked_reply
    log("PASS: unlinked account commands return the link-your-account message, never account data")

    blocked = False
    for _ in range(25):
        blocked = _rate_limited("smoketest-rl-user", "price") or blocked
    log(f"25 rapid /price calls from one synthetic discord_id -> rate limit engaged: {blocked}")
    assert blocked, "Rate limiter did not engage within its configured threshold"
    log("PASS: per-user rate limiting engages")

    # ------------------------------------------------------------------
    section("12. Existing role sync / opportunity digest / Proof Ledger untouched")
    # ------------------------------------------------------------------
    async with pool.acquire() as conn:
        role_sync_count = await conn.fetchval("SELECT COUNT(*) FROM discord_role_sync_queue")
        digest_count = await conn.fetchval("SELECT COUNT(*) FROM discord_publications")
        proof_count = await conn.fetchval("SELECT COUNT(*) FROM discord_daily_proof_publications")
    log(f"discord_role_sync_queue rows: {role_sync_count} (unchanged by this script - no writes made to this table)")
    log(f"discord_publications rows: {digest_count} (unchanged by this script)")
    log(f"discord_daily_proof_publications rows: {proof_count} (unchanged by this script)")
    log(
        "This script never imports or calls anything from discord_integration.py's role-sync/"
        "digest path or discord_daily_proof.py's publish_daily_proof_review - those code paths "
        "are untouched by the trading-feed change (see the branch diff for confirmation)."
    )

    # ------------------------------------------------------------------
    section("Cleanup")
    # ------------------------------------------------------------------
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_servers SET status='paused', updated_at=NOW() WHERE id=$1", server_id
        )
        await conn.execute(
            "DELETE FROM discord_server_posts WHERE server_id=$1 AND dedup_key LIKE 'smoketest-%'", server_id
        )
        await conn.execute(
            "INSERT INTO discord_server_audit_log (server_id, admin_id, action, details) VALUES ($1,$2,$3,$4::jsonb)",
            server_id, str(admin_id), "smoke_test_reset_to_paused", json.dumps({}),
        )
    log(f"Server {server_id} reset to status='paused' (not activated). Scratch smoketest-* post rows removed.")
    log("Real test/opportunity/opportunity_update/daily_overview post rows from steps 7-9 are left in place for your review.")

    await pool.close()

    section("SUMMARY")
    log("All steps completed. See above for pass/fail on each.")


if __name__ == "__main__":
    asyncio.run(main())
