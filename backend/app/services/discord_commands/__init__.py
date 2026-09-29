from __future__ import annotations

"""
Slash-command *definitions*, split into one module per command group -
the HTTP-interactions equivalent of discord.py cogs/extensions.

This app has no persistent gateway bot (see
app/routers/discord_interactions.py's docstring for why), so there is no
`bot.tree` to sync. The equivalent of "load every extension, then sync
the tree" is:

  1. `load_all_commands()` imports every module listed in `EXTENSIONS`
     and collects its `COMMANDS` list. A module that fails to import, or
     that contributes no commands, raises `ExtensionLoadError` - nothing
     here is swallowed, because a silently-missing extension is exactly
     how `/feed` ends up invisible in Discord.
  2. `app/services/discord_command_sync.py` PUTs the collected set to
     Discord (globally, and to the test guild), which is the actual
     "tree sync".

These definitions are the single source of truth: the boot-time sync and
`scripts/register_discord_commands.py` both read them, so the two can
never drift.
"""

import importlib
import logging
from typing import Any, Dict, List, Tuple

log = logging.getLogger("discord_commands")

# The "extensions" to load, in order. Every module here must define a
# module-level `COMMANDS: list[dict]` of Discord application-command
# payloads. Adding a command group means adding its module here - there is
# no auto-discovery, so a new group can't be half-registered by accident.
EXTENSIONS: Tuple[str, ...] = (
    "app.services.discord_commands.core",
    "app.services.discord_commands.feed",
    "app.services.discord_commands.leaks",
)


class ExtensionLoadError(RuntimeError):
    """A command extension could not be imported, or contributed nothing."""


def _validate(module_name: str, commands: Any) -> List[Dict[str, Any]]:
    if not isinstance(commands, list) or not commands:
        raise ExtensionLoadError(
            f"{module_name} defines COMMANDS but it is empty or not a list "
            f"(got {type(commands).__name__}) - it would contribute no slash commands"
        )
    for cmd in commands:
        if not isinstance(cmd, dict) or not cmd.get("name") or not cmd.get("description"):
            raise ExtensionLoadError(
                f"{module_name} contains an invalid command payload (needs name + description): {cmd!r}"
            )
    return commands


def load_extension(module_name: str) -> List[Dict[str, Any]]:
    """Import one extension and return the commands it defines.

    Raises `ExtensionLoadError` (never returns an empty list, never
    swallows the import error) so a broken extension is loud at boot.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # ImportError, SyntaxError, anything at import time
        raise ExtensionLoadError(f"failed to import command extension {module_name}: {exc}") from exc

    commands = getattr(module, "COMMANDS", None)
    if commands is None:
        raise ExtensionLoadError(f"command extension {module_name} has no COMMANDS list")
    return _validate(module_name, commands)


def load_all_commands() -> List[Dict[str, Any]]:
    """Load every extension in `EXTENSIONS` and return the full command set.

    Logs each extension and the commands it contributed. Raises on the
    first failure - a partial command set must never be synced, because
    syncing it would *delete* the missing commands from Discord (a PUT
    replaces the whole set).
    """
    collected: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}

    for module_name in EXTENSIONS:
        commands = load_extension(module_name)
        for cmd in commands:
            name = str(cmd["name"])
            if name in seen:
                raise ExtensionLoadError(
                    f"duplicate command /{name} defined in both {seen[name]} and {module_name}"
                )
            seen[name] = module_name
        collected.extend(commands)
        log.info(
            "📦 Loaded command extension %s (%d command(s): %s)",
            module_name,
            len(commands),
            ", ".join(f"/{c['name']}" for c in commands),
        )

    log.info(
        "📦 All %d command extension(s) loaded: %d command(s) total",
        len(EXTENSIONS),
        len(collected),
    )
    return collected


def assert_feed_group_loaded(commands: List[Dict[str, Any]]) -> None:
    """Fail loudly unless the `/feed` group - with its `setup` subcommand -
    is in the set about to be synced. `/feed setup` is the entry point for
    every paid Server Feed, so a sync that would quietly drop it is a bug
    worth aborting the sync for."""
    feed = next((c for c in commands if c.get("name") == "feed"), None)
    if feed is None:
        raise ExtensionLoadError(
            "the /feed command group is missing from the loaded command set - "
            f"expected it from one of: {', '.join(EXTENSIONS)}"
        )
    subcommands = {str(o.get("name")) for o in (feed.get("options") or [])}
    if "setup" not in subcommands:
        raise ExtensionLoadError(
            f"/feed loaded but has no `setup` subcommand (found: {sorted(subcommands)})"
        )
    log.info(
        "✅ /feed group present with %d subcommand(s): %s",
        len(subcommands),
        ", ".join(f"/feed {s}" for s in sorted(subcommands)),
    )
