"""
Tests for boot-time slash-command registration
(app/services/discord_command_sync.py + app/services/discord_commands).

These are the regression tests for "the bot installs but Discord shows no
commands": the whole command set must be loaded before the sync, the sync
must hit both the global and the guild endpoint, the token's application
must be confirmed, and every failure must surface rather than being
swallowed.
"""
import asyncio
import logging

import pytest

from app.services import discord_command_sync as sync_mod
from app.services.discord_commands import (
    EXTENSIONS,
    ExtensionLoadError,
    assert_feed_group_loaded,
    load_all_commands,
    load_extension,
)


# --------------------------------------------------------------------------
# Extension loading (the "cogs are loaded before sync" half)
# --------------------------------------------------------------------------


def test_every_extension_loads_and_contributes_commands():
    for module_name in EXTENSIONS:
        commands = load_extension(module_name)
        assert commands, module_name


def test_loaded_set_includes_feed_group_with_setup():
    commands = load_all_commands()
    names = [c["name"] for c in commands]
    assert "feed" in names
    # Does not raise.
    assert_feed_group_loaded(commands)
    feed = next(c for c in commands if c["name"] == "feed")
    subs = {o["name"] for o in feed["options"]}
    assert {"setup", "activate", "status", "billing"} <= subs


def test_loaded_set_covers_every_handled_command():
    from app.routers import discord_interactions as ix

    names = {c["name"] for c in load_all_commands()}
    assert ix.ACCOUNT_COMMANDS <= names
    assert ix.CARD_COMMANDS <= names
    assert {"help", "feed", "leaks"} <= names





def test_loaded_set_includes_leaks_group_with_self_service_controls():
    commands = load_all_commands()
    leaks = next(c for c in commands if c["name"] == "leaks")
    subs = {o["name"] for o in leaks["options"]}
    assert {"setup", "status", "channel", "pause", "resume", "remove"} <= subs
    assert leaks["default_member_permissions"] == "32"
    assert leaks["dm_permission"] is False


def test_missing_feed_group_is_loud():
    with pytest.raises(ExtensionLoadError, match="/feed"):
        assert_feed_group_loaded([{"name": "help", "description": "x"}])


def test_feed_without_setup_subcommand_is_loud():
    with pytest.raises(ExtensionLoadError, match="setup"):
        assert_feed_group_loaded([{"name": "feed", "description": "x", "options": [{"name": "status"}]}])


def test_broken_extension_raises_rather_than_returning_empty():
    with pytest.raises(ExtensionLoadError, match="failed to import"):
        load_extension("app.services.discord_commands.does_not_exist")


# --------------------------------------------------------------------------
# The sync itself
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def text(self):
        return str(self._payload)

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records requests and replies with whatever the test queued."""

    def __init__(self, *, application, status=200, guild_status=200):
        self.application = application
        self.status = status
        self.guild_status = guild_status
        self.requests = []

    def request(self, method, url, headers=None, json=None):
        self.requests.append((method, url, headers, json))
        if url.endswith("/oauth2/applications/@me"):
            return _FakeResponse(200, self.application)
        status = self.guild_status if "/guilds/" in url else self.status
        payload = [{"name": c["name"], "id": "1"} for c in (json or [])] if status == 200 else {"message": "boom"}
        return _FakeResponse(status, payload)

    async def close(self):
        pass


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_sync_registers_globally_and_to_the_test_guild():
    session = _FakeSession(application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"})
    report = _run(sync_mod.sync_commands(token="tok", session=session))

    app_id = sync_mod.DEFAULT_EXPECTED_APPLICATION_ID
    urls = [url for _method, url, _h, _j in session.requests]
    assert f"{sync_mod.DISCORD_API}/applications/{app_id}/commands" in urls
    assert (
        f"{sync_mod.DISCORD_API}/applications/{app_id}/guilds/818516082934546432/commands" in urls
    )
    # Identity is resolved before anything is registered.
    assert urls[0].endswith("/oauth2/applications/@me")

    assert report.application_id == app_id
    assert "feed" in report.global_commands
    assert "feed" in report.guild_commands["818516082934546432"]
    # Same full set in both scopes - a guild sync must never be a subset.
    assert sorted(report.global_commands) == sorted(report.guild_commands["818516082934546432"])


def test_sync_sends_the_full_loaded_command_set():
    session = _FakeSession(application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"})
    _run(sync_mod.sync_commands(token="tok", session=session))
    expected = {c["name"] for c in load_all_commands()}
    for method, url, _h, body in session.requests:
        if method == "PUT":
            assert {c["name"] for c in body} == expected, url


def test_sync_refuses_a_token_from_the_wrong_application():
    session = _FakeSession(application={"id": "999", "name": "Someone Else"})
    with pytest.raises(sync_mod.CommandSyncError, match="wrong application"):
        _run(sync_mod.sync_commands(token="tok", session=session))
    # Nothing was registered on the wrong app.
    assert all(method == "GET" for method, _url, _h, _j in session.requests)


def test_expected_application_id_can_be_disabled(monkeypatch):
    monkeypatch.setenv("DISCORD_EXPECTED_APPLICATION_ID", "")
    session = _FakeSession(application={"id": "999", "name": "Staging App"})
    report = _run(sync_mod.sync_commands(token="tok", session=session))
    assert report.application_id == "999"


def test_discord_error_surfaces_with_status_and_body():
    session = _FakeSession(
        application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"}, status=400
    )
    with pytest.raises(sync_mod.CommandSyncError, match="HTTP 400"):
        _run(sync_mod.sync_commands(token="tok", session=session))


def test_guild_sync_failure_is_not_swallowed():
    session = _FakeSession(
        application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"}, guild_status=403
    )
    with pytest.raises(sync_mod.CommandSyncError, match="HTTP 403"):
        _run(sync_mod.sync_commands(token="tok", session=session))


def test_guild_ids_are_configurable(monkeypatch):
    monkeypatch.setenv("DISCORD_COMMAND_SYNC_GUILD_IDS", "111, 222")
    session = _FakeSession(application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"})
    report = _run(sync_mod.sync_commands(token="tok", session=session))
    assert set(report.guild_commands) == {"111", "222"}


def test_sync_logs_application_id_and_command_names(caplog):
    session = _FakeSession(application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"})
    with caplog.at_level(logging.INFO):
        _run(sync_mod.sync_commands(token="tok", session=session))
    text = caplog.text
    assert f"application_id={sync_mod.DEFAULT_EXPECTED_APPLICATION_ID}" in text
    assert "/feed" in text
    assert "global command(s) synced" in text
    assert "synced to guild 818516082934546432" in text


# --------------------------------------------------------------------------
# The boot wrapper
# --------------------------------------------------------------------------


def test_boot_sync_logs_an_error_when_the_token_is_missing(monkeypatch, caplog):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with caplog.at_level(logging.INFO):
        assert _run(sync_mod.sync_commands_on_boot()) is None
    assert "FAILED" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_boot_sync_strict_mode_raises(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_COMMAND_SYNC_STRICT", "1")
    with pytest.raises(sync_mod.CommandSyncError):
        _run(sync_mod.sync_commands_on_boot())


def test_boot_sync_can_be_disabled(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_SYNC_COMMANDS_ON_BOOT", "0")
    with caplog.at_level(logging.INFO):
        assert _run(sync_mod.sync_commands_on_boot()) is None
    assert "skipped" in caplog.text


def test_boot_sync_reports_a_discord_failure_without_crashing(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.delenv("DISCORD_COMMAND_SYNC_STRICT", raising=False)

    async def _boom(**_kwargs):
        raise sync_mod.CommandSyncError("PUT /applications/x/commands -> HTTP 401: unauthorized")

    monkeypatch.setattr(sync_mod, "sync_commands", _boom)
    with caplog.at_level(logging.INFO):
        assert _run(sync_mod.sync_commands_on_boot()) is None
    assert "FAILED" in caplog.text
    assert "HTTP 401" in caplog.text


def test_authenticated_application_id_feeds_the_interactions_router():
    from app.routers import discord_interactions as ix

    session = _FakeSession(application={"id": sync_mod.DEFAULT_EXPECTED_APPLICATION_ID, "name": "EAFC.Live"})
    _run(sync_mod.sync_commands(token="tok", session=session))
    assert sync_mod.authenticated_application_id() == sync_mod.DEFAULT_EXPECTED_APPLICATION_ID
    assert ix.command_sync.authenticated_application_id() == sync_mod.DEFAULT_EXPECTED_APPLICATION_ID
