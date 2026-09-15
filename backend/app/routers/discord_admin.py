from __future__ import annotations

"""
Admin-only controls for the multi-server Discord trading feed.
All endpoints use the same require_admin dependency as the rest of the
admin surface. Server owners cannot use these routes simply because they
have Manage Guild in Discord.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.routers.admin import require_admin
from app.services.discord_integration import DiscordClient
from app.services.discord_trading_feed import (
    get_server,
    send_test_free_overview,
    send_test_opportunity_update,
    send_test_post,
)

router = APIRouter(prefix="/api/admin/discord", tags=["admin", "discord"])


async def _audit(conn, admin_id: str, server_id: Optional[int], action: str, details: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO discord_server_audit_log (server_id, admin_id, action, details)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        server_id,
        str(admin_id),
        action,
        json.dumps(details, default=str),
    )


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("created_at", "updated_at", "enabled_at", "expires_at", "sent_at", "current_period_end", "bot_removed_at"):
        if isinstance(d.get(key), datetime):
            d[key] = d[key].isoformat()
    return d


class RegisterServer(BaseModel):
    guild_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    access_level: str = Field(pattern="^(free|paid_partner|official)$")


class SetChannel(BaseModel):
    feed_channel_id: Optional[str] = None
    proof_channel_id: Optional[str] = None
    sell_channel_id: Optional[str] = None


class SetAccessLevel(BaseModel):
    access_level: str = Field(pattern="^(free|paid_partner|official)$")


class SetAllowance(BaseModel):
    max_posts_per_day: Optional[int] = Field(None, ge=1, le=200)
    min_confidence: Optional[float] = Field(None, ge=0, le=1)
    min_expected_profit: Optional[float] = Field(None, ge=0)
    cooldown_minutes: Optional[int] = Field(None, ge=0, le=1440)
    platform: Optional[str] = Field(None, pattern="^(ps|xbox|pc)$")
    live_opportunities_enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None


class ManualAccess(BaseModel):
    level: str = Field(pattern="^(free|partner|partner_plus)$")
    days: Optional[int] = Field(None, ge=1, le=3650)


class ManualMessage(BaseModel):
    content: str = Field(min_length=1, max_length=1900)


@router.get("/servers")
async def list_servers(admin=Depends(require_admin), conn=Depends(get_db)) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT s.*, st.max_posts_per_day, st.min_confidence, st.min_expected_profit,
               st.cooldown_minutes, st.platform, st.live_opportunities_enabled,
               st.daily_overview_enabled
        FROM discord_servers s
        LEFT JOIN discord_server_settings st ON st.server_id = s.id
        ORDER BY s.created_at DESC
        """
    )
    return [_row_to_dict(r) for r in rows]


@router.post("/servers")
async def register_server(body: RegisterServer, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    existing = await conn.fetchval("SELECT id FROM discord_servers WHERE guild_id=$1", body.guild_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="This guild is already registered")
    row = await conn.fetchrow(
        """
        INSERT INTO discord_servers (guild_id, name, access_level, status)
        VALUES ($1, $2, $3, 'paused')
        RETURNING id
        """,
        body.guild_id,
        body.name,
        body.access_level,
    )
    server_id = row["id"]
    await conn.execute("INSERT INTO discord_server_settings (server_id) VALUES ($1)", server_id)
    await _audit(conn, admin["user_id"], server_id, "register", body.model_dump())
    return {"id": server_id, "status": "paused"}


@router.put("/servers/{server_id}/channel")
async def set_channel(server_id: int, body: SetChannel, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    await conn.execute(
        """
        UPDATE discord_servers
        SET feed_channel_id = COALESCE($2, feed_channel_id),
            proof_channel_id = COALESCE($3, proof_channel_id),
            sell_channel_id = COALESCE($4, sell_channel_id),
            updated_at = NOW()
        WHERE id=$1
        """,
        server_id,
        body.feed_channel_id,
        body.proof_channel_id,
        body.sell_channel_id,
    )
    await _audit(conn, admin["user_id"], server_id, "set_channel", body.model_dump())
    return {"ok": True}


@router.put("/servers/{server_id}/access-level")
async def set_access_level(server_id: int, body: SetAccessLevel, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    await conn.execute("UPDATE discord_servers SET access_level=$2, updated_at=NOW() WHERE id=$1", server_id, body.access_level)
    await _audit(conn, admin["user_id"], server_id, "set_access_level", body.model_dump())
    return {"ok": True}


@router.put("/servers/{server_id}/manual-access")
async def set_manual_access(server_id: int, body: ManualAccess, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    """Admin grant independent of Stripe. Free=1/day, Partner=6/day,
    Partner+=15/day. Paid manual grants may optionally expire."""
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    if body.level == "free":
        access_level, plan, max_posts, live_enabled = "free", None, 1, False
        expiry = None
    elif body.level == "partner_plus":
        access_level, plan, max_posts, live_enabled = "paid_partner", "server_feed_plus", 15, True
        expiry = datetime.now(timezone.utc) + timedelta(days=body.days) if body.days else None
    else:
        access_level, plan, max_posts, live_enabled = "paid_partner", "server_feed", 6, True
        expiry = datetime.now(timezone.utc) + timedelta(days=body.days) if body.days else None

    status = "active" if server.feed_channel_id else "paused"
    await conn.execute(
        """
        UPDATE discord_servers
        SET access_level=$2, plan=$3, subscription_status='manual',
            current_period_end=$4, expires_at=$4, status=$5,
            enabled_by_admin_id=$6,
            enabled_at=CASE WHEN $5='active' THEN NOW() ELSE enabled_at END,
            updated_at=NOW()
        WHERE id=$1
        """,
        server_id, access_level, plan, expiry, status, str(admin["user_id"]),
    )
    await conn.execute(
        """
        INSERT INTO discord_server_settings (server_id, max_posts_per_day, live_opportunities_enabled, daily_overview_enabled, updated_at)
        VALUES ($1,$2,$3,$4,NOW())
        ON CONFLICT (server_id) DO UPDATE SET
          max_posts_per_day=EXCLUDED.max_posts_per_day,
          live_opportunities_enabled=EXCLUDED.live_opportunities_enabled,
          daily_overview_enabled=EXCLUDED.daily_overview_enabled,
          updated_at=NOW()
        """,
        server_id, max_posts, live_enabled, body.level == "free",
    )
    detail = {"level": body.level, "max_posts_per_day": max_posts, "days": body.days, "expires_at": expiry.isoformat() if expiry else None}
    await _audit(conn, admin["user_id"], server_id, "manual_access", detail)
    return {"ok": True, "status": status, **detail}


@router.put("/servers/{server_id}/allowance")
async def set_allowance(server_id: int, body: SetAllowance, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.access_level == "paid_partner" and body.expires_at is None:
        existing_expiry = await conn.fetchval("SELECT expires_at FROM discord_servers WHERE id=$1", server_id)
        if existing_expiry is None:
            raise HTTPException(status_code=422, detail="Paid Partner servers require an expiry date — set expires_at")
    await conn.execute(
        """
        UPDATE discord_server_settings SET
            max_posts_per_day = COALESCE($2, max_posts_per_day),
            min_confidence = COALESCE($3, min_confidence),
            min_expected_profit = COALESCE($4, min_expected_profit),
            cooldown_minutes = COALESCE($5, cooldown_minutes),
            platform = COALESCE($6, platform),
            live_opportunities_enabled = COALESCE($7, live_opportunities_enabled),
            updated_at = NOW()
        WHERE server_id=$1
        """,
        server_id,
        body.max_posts_per_day,
        body.min_confidence,
        body.min_expected_profit,
        body.cooldown_minutes,
        body.platform,
        body.live_opportunities_enabled,
    )
    if body.expires_at is not None:
        await conn.execute("UPDATE discord_servers SET expires_at=$2, updated_at=NOW() WHERE id=$1", server_id, body.expires_at)
    await _audit(conn, admin["user_id"], server_id, "set_allowance", body.model_dump(mode="json"))
    return {"ok": True}


@router.post("/servers/{server_id}/pause")
async def pause_server(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    await conn.execute("UPDATE discord_servers SET status='paused', updated_at=NOW() WHERE id=$1", server_id)
    await _audit(conn, admin["user_id"], server_id, "pause", {})
    return {"ok": True, "status": "paused"}


@router.post("/servers/{server_id}/resume")
async def resume_server(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not server.feed_channel_id and server.access_level != "free":
        raise HTTPException(status_code=422, detail="Set a feed channel before resuming this server")
    await conn.execute(
        "UPDATE discord_servers SET status='active', enabled_by_admin_id=$2, enabled_at=NOW(), updated_at=NOW() WHERE id=$1",
        server_id,
        str(admin["user_id"]),
    )
    await _audit(conn, admin["user_id"], server_id, "resume", {})
    return {"ok": True, "status": "active"}


@router.post("/servers/{server_id}/message")
async def send_manual_message(server_id: int, body: ManualMessage, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    row = await conn.fetchrow("SELECT id, name, feed_channel_id FROM discord_servers WHERE id=$1", server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not row["feed_channel_id"]:
        raise HTTPException(status_code=422, detail="Set a feed channel before sending a message")
    ok = await DiscordClient().post_message(str(row["feed_channel_id"]), body.content.strip())
    await _audit(conn, admin["user_id"], server_id, "manual_message", {"ok": ok, "chars": len(body.content)})
    if not ok:
        raise HTTPException(status_code=502, detail="Discord message failed")
    return {"ok": True}


@router.post("/broadcast")
async def broadcast_message(body: ManualMessage, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    """Send to every active Partner/Official feed. Manual admin broadcasts
    are deliberately not inserted into discord_server_posts, so they do
    not consume the server's trade-post allowance."""
    rows = await conn.fetch(
        """
        SELECT id, name, feed_channel_id
        FROM discord_servers
        WHERE status='active'
          AND access_level IN ('paid_partner','official')
          AND feed_channel_id IS NOT NULL
          AND bot_removed_at IS NULL
        ORDER BY id
        """
    )
    client = DiscordClient()
    sent = 0
    failures: list[dict[str, Any]] = []
    for row in rows:
        ok = await client.post_message(str(row["feed_channel_id"]), body.content.strip())
        if ok:
            sent += 1
        else:
            failures.append({"server_id": row["id"], "name": row["name"]})
        await _audit(conn, admin["user_id"], row["id"], "broadcast_message", {"ok": ok, "chars": len(body.content)})
    return {"ok": len(failures) == 0, "attempted": len(rows), "sent": sent, "failed": len(failures), "failures": failures}


@router.get("/servers/{server_id}/posts")
async def recent_posts(server_id: int, limit: int = 25, admin=Depends(require_admin), conn=Depends(get_db)) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 100))
    rows = await conn.fetch(
        """
        SELECT id, kind, dedup_key, status, last_error, attempts, created_at, sent_at
        FROM discord_server_posts
        WHERE server_id=$1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        server_id,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


@router.get("/servers/{server_id}/status")
async def server_status(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    failures = await conn.fetchval("SELECT COUNT(*) FROM discord_server_posts WHERE server_id=$1 AND status='failed' AND created_at > NOW() - INTERVAL '24 hours'", server_id)
    sent_today = await conn.fetchval("SELECT COUNT(*) FROM discord_server_posts WHERE server_id=$1 AND status='sent' AND sent_at > NOW() - INTERVAL '24 hours'", server_id)
    return {
        "server": _row_to_dict(vars(server) | {"expires_at": server.expires_at}),
        "is_postable": server.is_postable,
        "failures_last_24h": int(failures or 0),
        "sent_last_24h": int(sent_today or 0),
    }


@router.post("/servers/{server_id}/test-post")
async def test_post(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    from app.db import get_core_pool
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    pool = await get_core_pool()
    ok, error = await send_test_post(pool, server)
    await _audit(conn, admin["user_id"], server_id, "test_post", {"ok": ok, "error": error})
    if not ok:
        raise HTTPException(status_code=502, detail=error or "Discord post failed")
    return {"ok": True}


@router.post("/servers/{server_id}/test-free-overview")
async def test_free_overview(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    from app.db import get_core_pool
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    pool = await get_core_pool()
    ok, error = await send_test_free_overview(pool, server)
    await _audit(conn, admin["user_id"], server_id, "test_free_overview", {"ok": ok, "error": error})
    if not ok:
        raise HTTPException(status_code=502, detail=error or "Discord post failed")
    return {"ok": True}


class TestUpdate(BaseModel):
    status: str = Field(pattern="^(target_hit|loss_unrealised)$")


@router.post("/servers/{server_id}/test-opportunity-update")
async def test_opportunity_update(server_id: int, body: TestUpdate, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    from app.db import get_core_pool
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    pool = await get_core_pool()
    ok, error = await send_test_opportunity_update(pool, server, body.status)
    await _audit(conn, admin["user_id"], server_id, "test_opportunity_update", {"ok": ok, "error": error, "status": body.status})
    if not ok:
        raise HTTPException(status_code=502, detail=error or "Discord post failed")
    return {"ok": True}


@router.get("/servers/{server_id}/audit-log")
async def audit_log(server_id: int, limit: int = 50, admin=Depends(require_admin), conn=Depends(get_db)) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 200))
    rows = await conn.fetch(
        """
        SELECT admin_id, action, details, created_at
        FROM discord_server_audit_log
        WHERE server_id=$1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        server_id,
        limit,
    )
    return [_row_to_dict(r) for r in rows]
