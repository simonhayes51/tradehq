from __future__ import annotations

"""
The `/feed` command group - Free and paid multi-server trading-feed admin
commands (`/feed setup`, `/feed activate`, ...), gated to members with
Manage Server.

Definitions only - the handlers live in
app/routers/discord_interactions.py (`_build_feed_reply`). Nothing here
reads or writes the database or touches subscription state; changing a
choice label here changes what Discord *shows*, never what a feed is
billed.
"""

from typing import Any, Dict, List

PLAN_CHOICES = [
    {"name": "Free — 1 shared Trade of the Day", "value": "free"},
    {"name": "Server Feed — £9.99/mo, up to 6/day", "value": "server_feed"},
    {"name": "Server Feed+ — £19.99/mo, up to 15/day", "value": "server_feed_plus"},
]
FEED_PLATFORM_CHOICES = [
    {"name": "Console", "value": "console"},
    {"name": "PC", "value": "pc"},
    {"name": "Both", "value": "both"},
]
BUDGET_MIX_CHOICES = [
    {"name": "Balanced", "value": "balanced"},
    {"name": "Mostly low-budget", "value": "mostly_low"},
    {"name": "Mostly medium-budget", "value": "mostly_medium"},
    {"name": "Mostly high-budget", "value": "mostly_high"},
]
MIN_CONFIDENCE_CHOICES = [
    {"name": "50%", "value": "0.5"}, {"name": "60%", "value": "0.6"},
    {"name": "70%", "value": "0.7"}, {"name": "80%", "value": "0.8"},
]
MIN_PROFIT_CHOICES = [
    {"name": "1,000 coins", "value": "1000"}, {"name": "2,500 coins", "value": "2500"},
    {"name": "5,000 coins", "value": "5000"}, {"name": "10,000 coins", "value": "10000"},
    {"name": "25,000 coins", "value": "25000"},
]
CHANNEL_OPTION: Dict[str, Any] = {
    "type": 7,  # CHANNEL - Discord's native channel selector
    "name": "channel",
    "description": "Posting channel",
    "required": True,
    "channel_types": [0, 5],  # GUILD_TEXT, GUILD_ANNOUNCEMENT only
}

FEED_COMMAND: Dict[str, Any] = {
    "name": "feed",
    "description": "Manage this server's EAFC.Live trading feed (requires Manage Server)",
    "default_member_permissions": "32",  # MANAGE_GUILD - hides the command from members without it
    "dm_permission": False,  # not usable in DMs
    "options": [
        {
            "type": 1, "name": "setup", "description": "Start Free or set up a paid Server Feed",
            "options": [
                # Discord requires required options before optional ones. Channel
                # is the only thing Free needs; omitting plan defaults to Free.
                CHANNEL_OPTION,
                {"type": 3, "name": "plan", "description": "Optional — defaults to Free", "required": False, "choices": PLAN_CHOICES},
                {"type": 3, "name": "platform", "description": "Paid plans only", "required": False, "choices": FEED_PLATFORM_CHOICES},
                {"type": 3, "name": "budget_mix", "description": "Paid plans only", "required": False, "choices": BUDGET_MIX_CHOICES},
            ],
        },
        {"type": 1, "name": "test", "description": "Send a synthetic test opportunity to the configured channel"},
        {"type": 1, "name": "activate", "description": "Activate a paid live trading feed"},
        {"type": 1, "name": "status", "description": "Show this server's feed status"},
        {
            "type": 1, "name": "channel", "description": "Change the posting channel",
            "options": [CHANNEL_OPTION],
        },
        {
            "type": 1, "name": "sell_channel",
            "description": "Paid plans: route TARGET HIT / result posts to a separate channel",
            "options": [
                {
                    "type": 7,  # CHANNEL
                    "name": "channel",
                    "description": "Sell-time results channel - omit to use the main feed channel",
                    "required": False,
                    "channel_types": [0, 5],  # GUILD_TEXT, GUILD_ANNOUNCEMENT only
                },
            ],
        },
        {
            "type": 1, "name": "preferences", "description": "Paid plans: update server-specific feed preferences",
            "options": [
                {"type": 3, "name": "platform", "description": "Platform", "required": False, "choices": FEED_PLATFORM_CHOICES},
                {"type": 3, "name": "budget_mix", "description": "Budget mix", "required": False, "choices": BUDGET_MIX_CHOICES},
                {"type": 3, "name": "min_confidence", "description": "Minimum confidence", "required": False, "choices": MIN_CONFIDENCE_CHOICES},
                {"type": 3, "name": "min_expected_profit", "description": "Minimum expected profit", "required": False, "choices": MIN_PROFIT_CHOICES},
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
        {"type": 1, "name": "pause", "description": "Pause this server's feed"},
        {"type": 1, "name": "resume", "description": "Resume this server's feed"},
        {"type": 1, "name": "billing", "description": "Paid plans: get a secure billing-management link"},
        {"type": 1, "name": "help", "description": "Explain Free, Server Feed and Server Feed+"},
    ],
}

COMMANDS: List[Dict[str, Any]] = [FEED_COMMAND]
