from __future__ import annotations

"""
Core (per-user) slash commands: /price /fairvalue /watchlist /alerts
/results /account /help.

Definitions only - the handlers live in
app/routers/discord_interactions.py.
"""

from typing import Any, Dict, List

PLATFORM_OPTION: Dict[str, Any] = {
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

PLAYER_OPTION: Dict[str, Any] = {
    "type": 3,
    "name": "player",
    "description": "Start typing a player's name and pick a suggestion",
    "required": True,
    "autocomplete": True,
}

COMMANDS: List[Dict[str, Any]] = [
    {
        "name": "price",
        "description": "Current buy price for a card",
        "options": [PLAYER_OPTION, PLATFORM_OPTION],
    },
    {
        "name": "fairvalue",
        "description": "Fair value, verdict, risk and selling speed for a card",
        "options": [PLAYER_OPTION, PLATFORM_OPTION],
    },
    {"name": "watchlist", "description": "Your tracked cards (private reply)"},
    {"name": "alerts", "description": "Your active price alerts (private reply)"},
    {"name": "results", "description": "Your recent logged trades (private reply)"},
    {"name": "account", "description": "Your EAFC.Live account and plan (private reply)"},
    {"name": "help", "description": "List available EAFC.Live commands"},
]
