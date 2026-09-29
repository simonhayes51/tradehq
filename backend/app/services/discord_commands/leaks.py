from __future__ import annotations

from typing import Any, Dict, List

CHANNEL_OPTION: Dict[str, Any] = {
    "type": 7,
    "name": "channel",
    "description": "Channel where EAFC.Live leaks should be posted",
    "required": True,
    "channel_types": [0, 5],
}

LEAKS_COMMAND: Dict[str, Any] = {
    "name": "leaks",
    "description": "Manage this server's EAFC.Live leak feed",
    "default_member_permissions": "32",
    "dm_permission": False,
    "options": [
        {"type": 1, "name": "setup", "description": "Post the EAFC.Live leak feed in a channel", "options": [CHANNEL_OPTION]},
        {"type": 1, "name": "status", "description": "Show this server's leak-feed status"},
        {"type": 1, "name": "channel", "description": "Change the leak-feed channel", "options": [CHANNEL_OPTION]},
        {"type": 1, "name": "pause", "description": "Pause leak posts in this server"},
        {"type": 1, "name": "resume", "description": "Resume leak posts in this server"},
        {"type": 1, "name": "remove", "description": "Remove the leak feed from this server"},
    ],
}

COMMANDS: List[Dict[str, Any]] = [LEAKS_COMMAND]
