from fastapi import APIRouter, Depends, HTTPException, Request

from app.db import get_db
from app.services.discord_integration import (
    DiscordClient,
    DiscordConfig,
    desired_member_access,
    enqueue_role_sync,
    reconcile_user,
)

router = APIRouter(prefix="/api/discord", tags=["discord"])


def _user_id(request: Request) -> str:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(user_id)


@router.get("/status")
async def discord_status(request: Request, db=Depends(get_db)):
    user_id = _user_id(request)
    discord_user_id, desired = await desired_member_access(db, user_id)
    profile = await db.fetchrow(
        "SELECT username, global_name FROM user_profiles WHERE user_id=$1", user_id
    )
    queue = await db.fetchrow(
        "SELECT status, last_error, last_attempt_at, synced_at FROM discord_role_sync_queue WHERE user_id=$1",
        user_id,
    )
    config = DiscordConfig.from_env()
    guild_membership = "unknown"
    has_member_role = False
    if discord_user_id and config.role_sync_ready:
        member = await DiscordClient(config).get_member(discord_user_id)
        guild_membership = member.state
        has_member_role = member.state == "joined" and str(config.member_role_id) in member.roles
    elif not config.role_sync_ready:
        guild_membership = "unconfigured"
    return {
        "connected": bool(discord_user_id),
        "display_name": (profile["global_name"] or profile["username"]) if profile else None,
        "guild_membership": guild_membership,
        "membership_access": "EAFC.Live Member" if desired else "Free",
        "has_member_role": has_member_role,
        "role_sync": queue["status"] if queue else "not_queued",
        "invite_url": config.invite_url,
    }


@router.post("/reconcile")
async def reconcile_discord(request: Request, db=Depends(get_db)):
    user_id = _user_id(request)
    if not await enqueue_role_sync(db, user_id):
        raise HTTPException(status_code=409, detail="No Discord identity is associated with this account")
    result = await reconcile_user(db, user_id)
    return {"ok": result["role_sync"] == "synced", **result}
