from __future__ import annotations

"""
Boot-time slash-command registration - the HTTP-interactions equivalent
of discord.py's `await bot.tree.sync()`.

Why this exists: installing the bot with the `applications.commands`
OAuth scope only grants *permission* to register commands, it does not
register any. Until something PUTs the command definitions to Discord's
API, the app has an empty command set and the client shows nothing -
which is exactly the "bot installs fine but `/feed setup` doesn't exist"
symptom. That registration used to happen only if a human remembered to
run `scripts/register_discord_commands.py` by hand with the right env
vars, and Coolify never ran it, so on a fresh application the command
set stayed empty forever.

What this does, in order, on every boot:

  1. Loads every command extension (app/services/discord_commands) and
     asserts the `/feed` group with its `setup` subcommand is present -
     a partial set is never synced, because a PUT *replaces* the whole
     command set and would delete what's missing.
  2. Resolves the application the bot token actually belongs to
     (`GET /oauth2/applications/@me`) and logs its id + name.
  3. Confirms that id matches `DISCORD_EXPECTED_APPLICATION_ID`
     (default: the EAFC.Live app). A mismatch means the deployment is
     holding some other app's token - registering there would put the
     commands on the wrong application, so the sync aborts loudly
     instead.
  4. Syncs globally (`PUT /applications/{id}/commands`).
  5. Syncs to each guild in `DISCORD_COMMAND_SYNC_GUILD_IDS` (default:
     the EAFC.Live test guild) so commands are usable immediately -
     global commands can take up to an hour to propagate to clients.
  6. Logs the count and every command name returned by Discord for each
     scope.

Every failure is logged at ERROR with the Discord status code and
response body, and re-raised to the caller. `sync_commands_on_boot()`
turns that into a loud, greppable startup banner; set
`DISCORD_COMMAND_SYNC_STRICT=1` to make it fail the boot outright.

This module never touches the database, feed rows, or subscription
state - it only registers command *definitions* with Discord.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import aiohttp

from app.services.discord_commands import assert_feed_group_loaded, load_all_commands

log = logging.getLogger("discord_command_sync")

DISCORD_API = "https://discord.com/api/v10"

# The EAFC.Live Discord application. The bot token in the environment must
# belong to this application, otherwise the commands would be registered
# on someone else's app. Override with DISCORD_EXPECTED_APPLICATION_ID
# (set it empty to skip the check entirely).
DEFAULT_EXPECTED_APPLICATION_ID = "1408193521986437251"

# Guild-scoped sync target, so commands appear instantly for testing
# instead of waiting on global propagation. Override with
# DISCORD_COMMAND_SYNC_GUILD_IDS (comma-separated; empty = global only).
DEFAULT_SYNC_GUILD_IDS = ("818516082934546432",)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Set once a sync has confirmed which application the token belongs to.
# app/routers/discord_interactions.py falls back to this when
# DISCORD_APPLICATION_ID is not configured, so interaction follow-ups
# still work on a deployment that only has the bot token.
_authenticated_application_id: Optional[str] = None


def authenticated_application_id() -> Optional[str]:
    """The application id the bot token resolved to, or None if no sync
    has successfully identified it in this process yet."""
    return _authenticated_application_id


class CommandSyncError(RuntimeError):
    """A Discord API call needed for command registration failed."""


@dataclass
class SyncReport:
    application_id: str
    application_name: str
    global_commands: List[str] = field(default_factory=list)
    guild_commands: Dict[str, List[str]] = field(default_factory=dict)


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name) or default).strip() in ("1", "true", "True", "yes")


def _sync_guild_ids() -> List[str]:
    raw = os.getenv("DISCORD_COMMAND_SYNC_GUILD_IDS")
    if raw is None:
        return list(DEFAULT_SYNC_GUILD_IDS)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _expected_application_id() -> Optional[str]:
    raw = os.getenv("DISCORD_EXPECTED_APPLICATION_ID")
    if raw is None:
        return DEFAULT_EXPECTED_APPLICATION_ID
    raw = raw.strip()
    return raw or None


async def _request(
    session: aiohttp.ClientSession, method: str, url: str, token: str, *, json_body: Any = None
) -> Any:
    headers = {"Authorization": f"Bot {token}"}
    try:
        async with session.request(method, url, headers=headers, json=json_body) as resp:
            body = await resp.text()
            if resp.status not in (200, 201):
                raise CommandSyncError(
                    f"{method} {url.replace(DISCORD_API, '')} -> HTTP {resp.status}: {body[:500]}"
                )
            return await resp.json()
    except aiohttp.ClientError as exc:
        raise CommandSyncError(f"{method} {url.replace(DISCORD_API, '')} failed: {exc}") from exc


async def _fetch_application(session: aiohttp.ClientSession, token: str) -> Dict[str, Any]:
    """Resolve the application the token belongs to. This is the
    authoritative answer - it comes from the token itself, not from an
    env var that could disagree with it."""
    app = await _request(session, "GET", f"{DISCORD_API}/oauth2/applications/@me", token)
    if not isinstance(app, dict) or not app.get("id"):
        raise CommandSyncError(f"unexpected /oauth2/applications/@me response: {app!r}")
    return app


async def sync_commands(
    *,
    token: str,
    session: Optional[aiohttp.ClientSession] = None,
    guild_ids: Optional[Sequence[str]] = None,
    expected_application_id: Optional[str] = None,
) -> SyncReport:
    """Load every command extension, then register the full set globally
    and per test guild. Raises `CommandSyncError` /
    `ExtensionLoadError` - callers decide how loud to be."""
    global _authenticated_application_id

    if not token:
        raise CommandSyncError("no bot token supplied - cannot register slash commands")

    if guild_ids is None:
        guild_ids = _sync_guild_ids()
    if expected_application_id is None:
        expected_application_id = _expected_application_id()

    # 1. Extensions first. Every command must be loaded *before* the sync,
    #    because the PUT below replaces Discord's whole command set.
    log.info("🔄 Discord command sync: loading command extensions...")
    commands = load_all_commands()
    assert_feed_group_loaded(commands)

    owns_session = session is None
    session = session or aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT)
    try:
        # 2. Who are we?
        app = await _fetch_application(session, token)
        application_id = str(app["id"])
        application_name = str(app.get("name") or "unknown")
        _authenticated_application_id = application_id
        log.info(
            "🤖 Discord command sync: authenticated as application '%s' (application_id=%s)",
            application_name,
            application_id,
        )

        # 3. ...and is that who we're supposed to be?
        if expected_application_id and application_id != expected_application_id:
            raise CommandSyncError(
                "bot token belongs to application id "
                f"{application_id} ('{application_name}') but this deployment expects "
                f"{expected_application_id} - refusing to register commands on the wrong "
                "application. Fix DISCORD_BOT_TOKEN (or DISCORD_EXPECTED_APPLICATION_ID)."
            )
        if expected_application_id:
            log.info(
                "✅ Discord command sync: token confirmed for expected application_id=%s",
                expected_application_id,
            )
        else:
            log.warning(
                "⚠️  Discord command sync: DISCORD_EXPECTED_APPLICATION_ID is unset - "
                "skipping the token/application check"
            )

        report = SyncReport(application_id=application_id, application_name=application_name)

        # 4. Global sync - the real command set for every installed server.
        registered = await _request(
            session, "PUT", f"{DISCORD_API}/applications/{application_id}/commands", token, json_body=commands
        )
        report.global_commands = [str(c.get("name")) for c in registered]
        log.info(
            "🌍 Discord command sync: %d global command(s) synced: %s",
            len(report.global_commands),
            ", ".join(f"/{n}" for n in report.global_commands),
        )

        # 5. Guild sync - same set, instantly visible in the test guild.
        for guild_id in guild_ids:
            guild_registered = await _request(
                session,
                "PUT",
                f"{DISCORD_API}/applications/{application_id}/guilds/{guild_id}/commands",
                token,
                json_body=commands,
            )
            names = [str(c.get("name")) for c in guild_registered]
            report.guild_commands[guild_id] = names
            log.info(
                "🏠 Discord command sync: %d command(s) synced to guild %s: %s",
                len(names),
                guild_id,
                ", ".join(f"/{n}" for n in names),
            )
        if not guild_ids:
            log.info(
                "🏠 Discord command sync: no guild-scoped sync targets configured "
                "(DISCORD_COMMAND_SYNC_GUILD_IDS is empty) - global only"
            )

        log.info(
            "✅ Discord command sync complete: application_id=%s, %d global command(s), %d guild(s) synced",
            application_id,
            len(report.global_commands),
            len(report.guild_commands),
        )
        return report
    finally:
        if owns_session:
            await session.close()


async def sync_commands_on_boot(*, timeout_seconds: float = 45.0) -> Optional[SyncReport]:
    """Lifespan entry point. Runs the sync unless disabled, and turns any
    failure into an unmissable startup banner.

    Returns the report on success, None if it was skipped or failed.
    Failures do not crash the process (the rest of the API stays up and
    the previously-registered command set keeps working) unless
    `DISCORD_COMMAND_SYNC_STRICT=1`.
    """
    if not _env_flag("DISCORD_SYNC_COMMANDS_ON_BOOT", "1"):
        log.warning(
            "⏸️  Discord command sync skipped: DISCORD_SYNC_COMMANDS_ON_BOOT=0 - "
            "slash commands will only change if scripts/register_discord_commands.py is run manually"
        )
        return None

    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    strict = _env_flag("DISCORD_COMMAND_SYNC_STRICT")

    if not token:
        message = (
            "DISCORD_BOT_TOKEN is not set - slash commands were NOT registered, "
            "so Discord will show no commands for this application"
        )
        if strict:
            raise CommandSyncError(message)
        log.error("❌ Discord command sync FAILED: %s", message)
        return None

    try:
        return await asyncio.wait_for(sync_commands(token=token), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        if strict:
            raise
        log.error(
            "❌ Discord command sync FAILED: timed out after %ss talking to Discord - "
            "slash commands may be stale or missing",
            timeout_seconds,
            exc_info=True,
        )
        return None
    except Exception:
        if strict:
            raise
        # Deliberately ERROR + traceback rather than a warning: an
        # unregistered command set is invisible in Discord with no other
        # symptom, so this line is the only way to find out.
        log.error(
            "❌ Discord command sync FAILED - Discord may show no commands for this "
            "application. See the traceback below.",
            exc_info=True,
        )
        return None
