from __future__ import annotations

"""
Discord permission computation for the /feed command group. Two things
live here:

  1. check_manage_guild(interaction_payload) - reads the invoking member's
     permissions directly off the SIGNED, VERIFIED interaction payload
     Discord sent us. Discord computes `member.permissions` itself
     (base role perms + channel overwrites do NOT apply to this field -
     it's the member's GUILD-level permissions, exactly what "Manage
     Server" gating needs) - this code never trusts anything the client
     claims about itself.

  2. compute_channel_permissions() - the standard Discord permission
     algorithm (base role perms, then guild-wide @everyone/role/member
     channel overwrites, in that precedence order), used to verify the
     BOT itself (not the invoking member) can view/post/embed in a
     channel before /feed setup, /feed test, /feed activate, or
     /feed channel ever claim a channel is configured.

This app runs REST-only (no gateway connection - see
discord_integration.py's own module docstring), so there is no cached
member/role state to read locally; compute_channel_permissions() is
always fed data freshly fetched over the REST API by the caller.
"""

import logging

from typing import Any, Iterable, Optional

ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14

GUILD_TEXT = 0
GUILD_ANNOUNCEMENT = 5
ALLOWED_CHANNEL_TYPES = frozenset({GUILD_TEXT, GUILD_ANNOUNCEMENT})

REQUIRED_BOT_CHANNEL_PERMISSIONS = VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS

log = logging.getLogger("discord_permissions")


def _perm_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def has_manage_guild(interaction_payload: dict[str, Any]) -> bool:
    """True only if Discord's own signed interaction payload says the
    invoking member has MANAGE_GUILD (or ADMINISTRATOR, which implies
    it) in the guild the command was invoked in. Returns False for a
    DM (no `member` block at all - only `user` - since DMs have no
    guild-level permissions to check)."""
    member = interaction_payload.get("member")
    if not isinstance(member, dict):
        return False
    perms = _perm_int(member.get("permissions"))
    return bool(perms & (MANAGE_GUILD | ADMINISTRATOR))


def is_dm_interaction(interaction_payload: dict[str, Any]) -> bool:
    return not interaction_payload.get("guild_id")


def resolve_effective_permissions(
    *,
    guild_id: str,
    everyone_role_permissions: int,
    member_role_ids: Iterable[str],
    member_roles_permissions: dict[str, int],
    member_user_id: str,
    channel_overwrites: Iterable[dict[str, Any]],
) -> int:
    """Full computation including the @everyone-role channel overwrite
    (keyed by the guild_id itself, since @everyone's role id == guild id)."""
    member_role_ids = set(member_role_ids)
    base = everyone_role_permissions
    for role_id in member_role_ids:
        base |= member_roles_permissions.get(role_id, 0)

    if base & ADMINISTRATOR:
        return 0x7FFFFFFFFFFFFFFF

    overwrites = list(channel_overwrites)

    everyone_ow = next((o for o in overwrites if str(o.get("id")) == str(guild_id)), None)
    if everyone_ow is not None:
        base = (base & ~_perm_int(everyone_ow.get("deny"))) | _perm_int(everyone_ow.get("allow"))

    role_allow = 0
    role_deny = 0
    for role_id in member_role_ids:
        ow = next((o for o in overwrites if o.get("type") == 0 and str(o.get("id")) == str(role_id)), None)
        if ow is not None:
            role_allow |= _perm_int(ow.get("allow"))
            role_deny |= _perm_int(ow.get("deny"))
    base = (base & ~role_deny) | role_allow

    member_ow = next((o for o in overwrites if o.get("type") == 1 and str(o.get("id")) == str(member_user_id)), None)
    if member_ow is not None:
        base = (base & ~_perm_int(member_ow.get("deny"))) | _perm_int(member_ow.get("allow"))

    return base


def bot_can_post_embeds(effective_permissions: int) -> bool:
    return (effective_permissions & REQUIRED_BOT_CHANNEL_PERMISSIONS) == REQUIRED_BOT_CHANNEL_PERMISSIONS


async def user_has_manage_guild_live(client, guild_id: str, user_id: str) -> bool:
    """Live, guild-level (no channel overwrites needed) MANAGE_GUILD check
    for a web-authenticated user, used to gate the /v2/discord-feed
    settings page's access to a given guild's feed.

    Deliberately re-checked on every call rather than trusting a stored
    owner_discord_id from /feed setup: Discord roles/ownership can change
    after setup, and per-request spec this must reflect the CURRENT state
    of the guild, not who happened to run /feed setup originally.

    Uses the bot's own token (already required to be in the guild for the
    feed to function at all) rather than the user's OAuth token, so no
    extra 'guilds' OAuth scope or stored user access token is needed -
    same REST calls check_bot_channel_access already relies on, just
    resolved for an arbitrary member instead of the bot itself.

    Returns False (never raises) on any Discord API failure, missing
    membership, or bot-not-in-guild - fail-closed, since this gates write
    access to another server's paid feed settings."""
    guild_status, guild = await client.get_guild(guild_id)
    if guild_status == 200 and isinstance(guild, dict) and str(guild.get("owner_id")) == str(user_id):
        return True

    roles_status, roles = await client.get_guild_roles(guild_id)
    if roles_status != 200 or not isinstance(roles, list):
        return False

    member_status, member = await client.get_guild_member_in(guild_id, user_id)
    if member_status != 200 or not isinstance(member, dict):
        return False

    everyone_perms = 0
    role_perms: dict[str, int] = {}
    for role in roles:
        role_perms[str(role["id"])] = _perm_int(role.get("permissions"))
        if str(role["id"]) == str(guild_id):
            everyone_perms = _perm_int(role.get("permissions"))

    effective = everyone_perms
    for role_id in member.get("roles") or []:
        effective |= role_perms.get(str(role_id), 0)

    return bool(effective & (MANAGE_GUILD | ADMINISTRATOR))


async def check_bot_channel_access(client, guild_id: str, channel_id: str) -> tuple[bool, Optional[str]]:
    """Fetches everything needed over the REST API and returns
    (ok, reason). reason is None on success, otherwise one of:
    'channel_not_found', 'invalid_channel_type', 'bot_not_in_guild',
    'missing_permissions', 'discord_unavailable'."""
    status, channel = await client.get_channel(channel_id)
    if status == 401:
        log.error("Discord channel check failed authentication: guild=%s channel=%s status=401", guild_id, channel_id)
        return False, "bot_auth_failed"
    if status == 403:
        # A 403 here usually means the bot exists in the guild but cannot
        # View Channel. Distinguish that from a bot that is not installed
        # in the guild at all so /feed setup gives the owner a useful fix.
        guild_status, _ = await client.get_guild(guild_id)
        if guild_status in (403, 404):
            log.warning("Discord channel check: bot is not accessible in guild=%s", guild_id)
            return False, "bot_not_in_guild"
        if guild_status == 401:
            log.error("Discord guild check failed authentication: guild=%s status=401", guild_id)
            return False, "bot_auth_failed"
        if guild_status == 200:
            log.info("Discord channel check: bot lacks access to guild=%s channel=%s", guild_id, channel_id)
            return False, "missing_permissions"
        log.warning("Discord guild lookup unavailable while checking channel: guild=%s status=%s", guild_id, guild_status)
        return False, "discord_unavailable"
    if status == 404:
        return False, "channel_not_found"
    if status != 200 or not isinstance(channel, dict):
        log.warning("Discord channel lookup unavailable: guild=%s channel=%s status=%s", guild_id, channel_id, status)
        return False, "discord_unavailable"
    if channel.get("type") not in ALLOWED_CHANNEL_TYPES:
        return False, "invalid_channel_type"

    bot_status, bot_user = await client.get_bot_user()
    if bot_status == 401:
        log.error("Discord bot identity lookup failed authentication: status=401")
        return False, "bot_auth_failed"
    if bot_status != 200 or not isinstance(bot_user, dict):
        log.warning("Discord bot identity lookup unavailable: status=%s", bot_status)
        return False, "discord_unavailable"
    bot_user_id = str(bot_user["id"])

    roles_status, roles = await client.get_guild_roles(guild_id)
    if roles_status == 401:
        log.error("Discord guild role lookup failed authentication: guild=%s status=401", guild_id)
        return False, "bot_auth_failed"
    if roles_status in (403, 404):
        return False, "bot_not_in_guild"
    if roles_status != 200 or not isinstance(roles, list):
        log.warning("Discord guild role lookup unavailable: guild=%s status=%s", guild_id, roles_status)
        return False, "discord_unavailable"

    member_status, member = await client.get_guild_member_in(guild_id, bot_user_id)
    if member_status == 401:
        log.error("Discord bot member lookup failed authentication: guild=%s status=401", guild_id)
        return False, "bot_auth_failed"
    if member_status in (403, 404):
        return False, "bot_not_in_guild"
    if member_status != 200 or not isinstance(member, dict):
        log.warning("Discord bot member lookup unavailable: guild=%s status=%s", guild_id, member_status)
        return False, "discord_unavailable"

    everyone_perms = 0
    role_perms: dict[str, int] = {}
    for role in roles:
        role_perms[str(role["id"])] = _perm_int(role.get("permissions"))
        if str(role["id"]) == str(guild_id):
            everyone_perms = _perm_int(role.get("permissions"))

    effective = resolve_effective_permissions(
        guild_id=guild_id,
        everyone_role_permissions=everyone_perms,
        member_role_ids=member.get("roles") or [],
        member_roles_permissions=role_perms,
        member_user_id=bot_user_id,
        channel_overwrites=channel.get("permission_overwrites") or [],
    )
    if bot_can_post_embeds(effective):
        return True, None
    return False, "missing_permissions"
