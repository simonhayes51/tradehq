from __future__ import annotations

"""
Admin-only controls for the multi-server Discord trading feed
(discord_servers / discord_server_settings / discord_server_posts /
discord_server_audit_log — see migrations/069_discord_trading_feed.sql).

Gated by the exact same require_admin dependency as the rest of the admin
surface (app/routers/admin.py): ADMIN_DISCORD_IDS env or
users.account_type='admin'. There is no separate, weaker check here — a
Discord server's own "manage guild" permission is never sufficient to
register or reconfigure it.

Posting itself stays governed by DISCORD_TRADING_FEED_ENABLED (default
off) and each server's own status/expiry — these endpoints only change
configuration and can be used safely before that switch is flipped.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db
from app.routers.admin import require_admin
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
    for key in ("created_at", "updated_at", "enabled_at", "expires_at", "sent_at"):
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
    await conn.execute(
        "INSERT INTO discord_server_settings (server_id) VALUES ($1)", server_id
    )
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
    await conn.execute(
        "UPDATE discord_servers SET access_level=$2, updated_at=NOW() WHERE id=$1",
        server_id,
        body.access_level,
    )
    await _audit(conn, admin["user_id"], server_id, "set_access_level", body.model_dump())
    return {"ok": True}


@router.put("/servers/{server_id}/allowance")
async def set_allowance(server_id: int, body: SetAllowance, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.access_level == "paid_partner" and body.expires_at is None:
        existing_expiry = await conn.fetchval("SELECT expires_at FROM discord_servers WHERE id=$1", server_id)
        if existing_expiry is None:
            raise HTTPException(
                status_code=422,
                detail="Paid Partner servers require an expiry date — set expires_at",
            )
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
        await conn.execute(
            "UPDATE discord_servers SET expires_at=$2, updated_at=NOW() WHERE id=$1",
            server_id,
            body.expires_at,
        )
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


@router.get("/servers/{server_id}/posts")
async def recent_posts(
    server_id: int, limit: int = 25, admin=Depends(require_admin), conn=Depends(get_db)
) -> List[Dict[str, Any]]:
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
    failures = await conn.fetchval(
        "SELECT COUNT(*) FROM discord_server_posts WHERE server_id=$1 AND status='failed' AND created_at > NOW() - INTERVAL '24 hours'",
        server_id,
    )
    sent_today = await conn.fetchval(
        "SELECT COUNT(*) FROM discord_server_posts WHERE server_id=$1 AND status='sent' AND sent_at > NOW() - INTERVAL '24 hours'",
        server_id,
    )
    return {
        "server": _row_to_dict(vars(server) | {"expires_at": server.expires_at}),
        "is_postable": server.is_postable,
        "failures_last_24h": int(failures or 0),
        "sent_last_24h": int(sent_today or 0),
    }


@router.post("/servers/{server_id}/test-post")
async def test_post(server_id: int, admin=Depends(require_admin), conn=Depends(get_db)) -> Dict[str, Any]:
    """Sends a clearly-labelled fixture post, never a genuine live trade."""
    from app.db import get_core_pool  # local import: avoids a startup import cycle

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
    """Sends a clearly-labelled fixture Free daily overview - fixture
    numbers only, never real Proof Ledger data."""
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
async def test_opportunity_update(
    server_id: int, body: TestUpdate, admin=Depends(require_admin), conn=Depends(get_db)
) -> Dict[str, Any]:
    """Sends a clearly-labelled fixture opportunity-update post (target hit
    or closed/expired) - never a real graded outcome."""
    from app.db import get_core_pool

    server = await get_server(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    pool = await get_core_pool()
    ok, error = await send_test_opportunity_update(pool, server, body.status)
    await _audit(
        conn, admin["user_id"], server_id, "test_opportunity_update",
        {"ok": ok, "error": error, "status": body.status},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=error or "Discord post failed")
    return {"ok": True}


@router.get("/servers/{server_id}/audit-log")
async def audit_log(
    server_id: int, limit: int = 50, admin=Depends(require_admin), conn=Depends(get_db)
) -> List[Dict[str, Any]]:
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
