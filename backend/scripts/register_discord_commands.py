"""
Registers the multi-server trading feed's slash commands
(/price /fairvalue /watchlist /alerts /results /account /help) with a
Discord application, scoped to a single guild so they show up instantly
(global commands can take up to an hour to propagate).

This only registers command *definitions* with Discord - it does not
touch the app's own database and has no interaction with
DISCORD_TRADING_FEED_ENABLED. Safe to run against production any time;
re-running is idempotent (Discord replaces the guild's command set).

Usage:
    export DISCORD_BOT_TOKEN=...
    export DISCORD_APPLICATION_ID=...   # aka "client ID"
    export DISCORD_GUILD_ID=...         # the server to register commands in
    python scripts/register_discord_commands.py

    # To remove all guild commands instead (e.g. rolling back a test):
    python scripts/register_discord_commands.py --clear
"""
import argparse
import os
import sys

import requests

COMMANDS = [
    {
        "name": "price",
        "description": "Current buy price for a card",
        "options": [
            {"type": 3, "name": "player", "description": "Player name", "required": True}
        ],
    },
    {
        "name": "fairvalue",
        "description": "Fair value and signal for a card",
        "options": [
            {"type": 3, "name": "player", "description": "Player name", "required": True}
        ],
    },
    {"name": "watchlist", "description": "Your tracked cards (private reply)"},
    {"name": "alerts", "description": "Your active price alerts (private reply)"},
    {"name": "results", "description": "Your recent logged trades (private reply)"},
    {"name": "account", "description": "Your EAFC.Live account and plan (private reply)"},
    {"name": "help", "description": "List available EAFC.Live commands"},
]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clear", action="store_true", help="Remove all guild commands instead of registering them"
    )
    args = parser.parse_args()

    bot_token = _require_env("DISCORD_BOT_TOKEN")
    application_id = _require_env("DISCORD_APPLICATION_ID")
    guild_id = _require_env("DISCORD_GUILD_ID")

    url = f"https://discord.com/api/v10/applications/{application_id}/guilds/{guild_id}/commands"
    headers = {"Authorization": f"Bot {bot_token}"}
    payload = [] if args.clear else COMMANDS

    resp = requests.put(url, headers=headers, json=payload, timeout=15)
    if resp.status_code not in (200, 201):
        print(f"Discord API error {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    registered = resp.json()
    if args.clear:
        print("Cleared all guild commands.")
        return

    print(f"Registered {len(registered)} guild command(s) for guild {guild_id}:")
    for cmd in registered:
        print(f"  /{cmd['name']} - {cmd['description']} (id={cmd['id']})")
    print(
        "\nThese are guild-scoped and should be visible in that server "
        "immediately. Note this only registers command definitions - the "
        "interactions endpoint itself must also be configured in the "
        "Developer Portal (see the runbook) before any command will "
        "actually reply."
    )


if __name__ == "__main__":
    main()
