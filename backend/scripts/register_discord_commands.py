"""
Registers the trading feed's slash commands with a Discord application.

**You normally do not need to run this.** The backend now syncs its whole
command set on every boot - globally and to the test guild - via
`app/services/discord_command_sync.py`. This script stays as a manual
escape hatch: registering into a one-off guild, or clearing a guild's
commands after a test.

The command definitions themselves live in
`app/services/discord_commands/` (one module per group, `/feed`
included) and are shared with the boot-time sync, so the two can never
disagree.

This only registers command *definitions* with Discord - it does not
touch the app's own database and has no interaction with
DISCORD_TRADING_FEED_ENABLED. Safe to run against production any time;
re-running is idempotent (Discord replaces the scope's command set).

Usage:
    export DISCORD_BOT_TOKEN=...
    export DISCORD_GUILD_ID=...         # the server to register commands in
    # DISCORD_APPLICATION_ID is optional - resolved from the token if unset
    python scripts/register_discord_commands.py

    # Global instead of guild-scoped (can take up to an hour to appear):
    python scripts/register_discord_commands.py --global

    # To remove all guild commands instead (e.g. rolling back a test):
    python scripts/register_discord_commands.py --clear
"""
import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.discord_commands import (  # noqa: E402
    assert_feed_group_loaded,
    load_all_commands,
)

API = "https://discord.com/api/v10"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _resolve_application_id(bot_token: str) -> str:
    """Env var if set, otherwise ask Discord which application the token
    belongs to - the token is the authoritative answer."""
    from_env = os.getenv("DISCORD_APPLICATION_ID")
    if from_env:
        return from_env
    resp = requests.get(
        f"{API}/oauth2/applications/@me", headers={"Authorization": f"Bot {bot_token}"}, timeout=15
    )
    if resp.status_code != 200:
        print(f"Could not resolve application from token: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)
    app = resp.json()
    print(f"Token belongs to application '{app.get('name')}' (id={app['id']})")
    return str(app["id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clear", action="store_true", help="Remove all commands from the target scope instead of registering them"
    )
    parser.add_argument(
        "--global", dest="global_scope", action="store_true",
        help="Register globally instead of to DISCORD_GUILD_ID",
    )
    args = parser.parse_args()

    bot_token = _require_env("DISCORD_BOT_TOKEN")
    application_id = _resolve_application_id(bot_token)

    if args.clear:
        payload = []
    else:
        payload = load_all_commands()
        assert_feed_group_loaded(payload)

    if args.global_scope:
        scope = "global"
        url = f"{API}/applications/{application_id}/commands"
    else:
        guild_id = _require_env("DISCORD_GUILD_ID")
        scope = f"guild {guild_id}"
        url = f"{API}/applications/{application_id}/guilds/{guild_id}/commands"

    resp = requests.put(url, headers={"Authorization": f"Bot {bot_token}"}, json=payload, timeout=15)
    if resp.status_code not in (200, 201):
        print(f"Discord API error {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    if args.clear:
        print(f"Cleared all {scope} commands.")
        return

    registered = resp.json()
    print(f"Registered {len(registered)} {scope} command(s):")
    for cmd in registered:
        print(f"  /{cmd['name']} - {cmd['description']} (id={cmd['id']})")
    if not args.global_scope:
        print("\nThese are guild-scoped and should be visible in that server immediately.")
    else:
        print("\nGlobal commands can take up to an hour to propagate to clients.")
    print(
        "Note this only registers command definitions - the interactions "
        "endpoint itself must also be configured in the Developer Portal "
        "(see the runbook) before any command will actually reply."
    )


if __name__ == "__main__":
    main()
