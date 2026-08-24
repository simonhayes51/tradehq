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

_PLATFORM_OPTION = {
    "type": 3,
    "name": "platform",
    "description": "Platform (defaults to PS)",
    "required": False,
    "choices": [
        {"name": "PlayStation", "value": "ps"},
        {"name": "Xbox", "value": "xbox"},
        {"name": "PC", "value": "pc"},
    ],
}

_PLAYER_OPTION = {
    "type": 3,
    "name": "player",
    "description": "Start typing a player's name and pick a suggestion",
    "required": True,
    "autocomplete": True,
}

COMMANDS = [
    {
        "name": "price",
        "description": "Current buy price for a card",
        "options": [_PLAYER_OPTION, _PLATFORM_OPTION],
    },
    {
        "name": "fairvalue",
        "description": "Fair value, verdict, risk and selling speed for a card",
        "options": [_PLAYER_OPTION, _PLATFORM_OPTION],
    },
    {"name": "watchlist", "description": "Your tracked cards (private reply)"},
    {"name": "alerts", "description": "Your active price alerts (private reply)"},
    {"name": "results", "description": "Your recent logged trades (private reply)"},
    {"name": "account", "description": "Your EAFC.Live account and plan (private reply)"},
    {"name": "help", "description": "List available EAFC.Live commands"},
]

_PLAN_CHOICES = [
    {"name": "Server Feed — £9.99/mo, up to 6/day", "value": "server_feed"},
    {"name": "Server Feed+ — £19.99/mo, up to 12/day", "value": "server_feed_plus"},
]
_FEED_PLATFORM_CHOICES = [
    {"name": "Console", "value": "console"},
    {"name": "PC", "value": "pc"},
    {"name": "Both", "value": "both"},
]
_BUDGET_MIX_CHOICES = [
    {"name": "Balanced", "value": "balanced"},
    {"name": "Mostly low-budget", "value": "mostly_low"},
    {"name": "Mostly medium-budget", "value": "mostly_medium"},
    {"name": "Mostly high-budget", "value": "mostly_high"},
]
_MIN_CONFIDENCE_CHOICES = [
    {"name": "50%", "value": "0.5"}, {"name": "60%", "value": "0.6"},
    {"name": "70%", "value": "0.7"}, {"name": "80%", "value": "0.8"},
]
_MIN_PROFIT_CHOICES = [
    {"name": "1,000 coins", "value": "1000"}, {"name": "2,500 coins", "value": "2500"},
    {"name": "5,000 coins", "value": "5000"}, {"name": "10,000 coins", "value": "10000"},
    {"name": "25,000 coins", "value": "25000"},
]
_CHANNEL_OPTION = {
    "type": 7,  # CHANNEL - Discord's native channel selector
    "name": "channel",
    "description": "Posting channel",
    "required": True,
    "channel_types": [0, 5],  # GUILD_TEXT, GUILD_ANNOUNCEMENT only
}

FEED_COMMAND = {
    "name": "feed",
    "description": "Manage this server's paid trading feed (requires Manage Server)",
    "default_member_permissions": "32",  # MANAGE_GUILD - hides the command from members without it
    "dm_permission": False,  # not usable in DMs
    "options": [
        {
            "type": 1, "name": "setup", "description": "Set up the paid trading feed for this server",
            "options": [
                {"type": 3, "name": "plan", "description": "Plan", "required": True, "choices": _PLAN_CHOICES},
                _CHANNEL_OPTION,
                {"type": 3, "name": "platform", "description": "Platform", "required": True, "choices": _FEED_PLATFORM_CHOICES},
                {"type": 3, "name": "budget_mix", "description": "Budget mix", "required": True, "choices": _BUDGET_MIX_CHOICES},
            ],
        },
        {"type": 1, "name": "test", "description": "Send a synthetic test opportunity to the configured channel"},
        {"type": 1, "name": "activate", "description": "Activate the live trading feed"},
        {"type": 1, "name": "status", "description": "Show this server's feed status"},
        {
            "type": 1, "name": "channel", "description": "Change the posting channel",
            "options": [_CHANNEL_OPTION],
        },
        {
            "type": 1, "name": "preferences", "description": "Update feed preferences",
            "options": [
                {"type": 3, "name": "platform", "description": "Platform", "required": False, "choices": _FEED_PLATFORM_CHOICES},
                {"type": 3, "name": "budget_mix", "description": "Budget mix", "required": False, "choices": _BUDGET_MIX_CHOICES},
                {"type": 3, "name": "min_confidence", "description": "Minimum confidence", "required": False, "choices": _MIN_CONFIDENCE_CHOICES},
                {"type": 3, "name": "min_expected_profit", "description": "Minimum expected profit", "required": False, "choices": _MIN_PROFIT_CHOICES},
                {
                    "type": 4, "name": "posting_start_hour", "description": "Posting window start (0-23, local time)",
                    "required": False, "min_value": 0, "max_value": 23,
                },
                {
                    "type": 4, "name": "posting_end_hour", "description": "Posting window end (0-23, local time)",
                    "required": False, "min_value": 0, "max_value": 23,
                },
                {
                    "type": 3, "name": "posting_timezone",
                    "description": "IANA timezone for the posting window, e.g. Europe/London", "required": False,
                },
                {
                    "type": 5, "name": "clear_posting_window",
                    "description": "Remove the posting window restriction (post any time)", "required": False,
                },
            ],
        },
        {"type": 1, "name": "pause", "description": "Pause new live opportunity posts (billing unaffected)"},
        {"type": 1, "name": "resume", "description": "Resume the live trading feed"},
        {"type": 1, "name": "billing", "description": "Get a secure link to manage billing"},
        {"type": 1, "name": "help", "description": "Explain the Server Feed plans and commands"},
    ],
}

COMMANDS.append(FEED_COMMAND)


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
