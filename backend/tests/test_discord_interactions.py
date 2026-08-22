import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import app.routers.discord_interactions as interactions
from app.routers.discord_interactions import (
    APPLICATION_COMMAND,
    APPLICATION_COMMAND_AUTOCOMPLETE,
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT,
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
    EPHEMERAL,
    PING,
    PONG,
    _build_reply,
    _command_hits,
    _option,
    _rate_limited,
    _verify_signature,
    handle_autocomplete,
)
from app.services import discord_command_data as data
from app.services import discord_embeds as embeds

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def setup_function(_):
    _command_hits.clear()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_signature_verification_accepts_a_genuine_signature():
    from nacl.signing import SigningKey

    signing_key = SigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()
    body = b'{"type":1}'
    timestamp = "1700000000"
    signature_hex = signing_key.sign(timestamp.encode() + body).signature.hex()
    assert _verify_signature(body, signature_hex, timestamp, public_key_hex) is True


def test_signature_verification_rejects_a_tampered_body():
    from nacl.signing import SigningKey

    signing_key = SigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()
    timestamp = "1700000000"
    signature_hex = signing_key.sign(timestamp.encode() + b'{"type":1}').signature.hex()
    assert _verify_signature(b'{"type":99}', signature_hex, timestamp, public_key_hex) is False


def test_signature_verification_rejects_garbage_input():
    assert _verify_signature(b"body", "not-hex", "1700000000", "also-not-hex") is False


# ---------------------------------------------------------------------------
# Autocomplete (interaction type 4)
# ---------------------------------------------------------------------------


def test_autocomplete_returns_up_to_25_distinct_versions(monkeypatch):
    rows = [
        {"source_card_id": 1, "name": "Lionel Messi", "rating": 99, "version": "FOF Summer Stars"},
        {"source_card_id": 2, "name": "Lionel Messi", "rating": 97, "version": "TOTS"},
        {"source_card_id": 3, "name": "Lionel Messi", "rating": 90, "version": "Gold"},
    ]
    monkeypatch.setattr(data, "search_cards", AsyncMock(return_value=rows))
    options = [{"name": "player", "value": "messi", "focused": True}]
    result = run(handle_autocomplete("price", options))
    assert result["type"] == APPLICATION_COMMAND_AUTOCOMPLETE_RESULT
    choices = result["data"]["choices"]
    labels = {c["name"] for c in choices}
    assert "Lionel Messi — 99 — FOF Summer Stars" in labels
    assert "Lionel Messi — 97 — TOTS" in labels
    assert "Lionel Messi — 90 — Gold" in labels
    # card_id, not a name, is the submitted value.
    values = {c["value"] for c in choices}
    assert values == {"1", "2", "3"}


def test_autocomplete_never_returns_more_than_25(monkeypatch):
    rows = [{"source_card_id": i, "name": f"Player {i}", "rating": 80, "version": "Gold"} for i in range(30)]
    monkeypatch.setattr(data, "search_cards", AsyncMock(return_value=rows[:25]))
    options = [{"name": "player", "value": "player", "focused": True}]
    result = run(handle_autocomplete("fairvalue", options))
    assert len(result["data"]["choices"]) <= 25


def test_autocomplete_empty_query_returns_no_choices_without_querying(monkeypatch):
    mock_search = AsyncMock(return_value=[])
    monkeypatch.setattr(data, "search_cards", mock_search)
    options = [{"name": "player", "value": "", "focused": True}]
    result = run(handle_autocomplete("price", options))
    assert result["data"]["choices"] == []
    mock_search.assert_not_called()


def test_autocomplete_ignores_non_card_commands(monkeypatch):
    mock_search = AsyncMock(return_value=[])
    monkeypatch.setattr(data, "search_cards", mock_search)
    options = [{"name": "player", "value": "messi", "focused": True}]
    result = run(handle_autocomplete("watchlist", options))
    assert result["data"]["choices"] == []
    mock_search.assert_not_called()


def test_autocomplete_partial_names_are_passed_through(monkeypatch):
    mock_search = AsyncMock(return_value=[])
    monkeypatch.setattr(data, "search_cards", mock_search)
    for query in ("messi", "mbap", "ronal"):
        options = [{"name": "player", "value": query, "focused": True}]
        run(handle_autocomplete("price", options))
    calls = [c.args[0] for c in mock_search.call_args_list]
    assert calls == ["messi", "mbap", "ronal"]


def test_diacritics_insensitive_matching_is_inherited_from_the_engine():
    """discord_command_data.search_cards delegates straight to the same
    search_players()/normalize_search_term() the rest of the app uses -
    this proves that underlying diacritic-folding property directly,
    rather than re-implementing it."""
    from app.services.market_data_provider import normalize_search_term

    assert normalize_search_term("traore") in normalize_search_term("Adama Traoré")
    assert normalize_search_term("HAALAND") == normalize_search_term("haaland")


# ---------------------------------------------------------------------------
# Command option parsing
# ---------------------------------------------------------------------------


def test_option_extracts_named_value():
    options = [{"name": "player", "value": "12345"}]
    assert _option(options, "player") == "12345"
    assert _option(options, "missing") is None
    assert _option(None, "player") is None


# ---------------------------------------------------------------------------
# Discord-to-user resolution and account-linked data
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, fetchval=None, fetch=None):
        self._fetchval = fetchval
        self._fetch = fetch

    async def fetchval(self, *a, **kw):
        return self._fetchval

    async def fetch(self, *a, **kw):
        return self._fetch or []

    def acquire(self):
        raise AssertionError("resolve_user_id should call fetchval directly on the pool")


def test_resolve_user_id_queries_users_table_by_discord_id(monkeypatch):
    pool = _FakePool(fetchval="user-abc")

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(data, "get_core_pool", fake_get_core_pool)
    result = run(data.resolve_user_id("712239212749127740"))
    assert result == "user-abc"


def test_resolve_user_id_returns_none_when_unlinked(monkeypatch):
    pool = _FakePool(fetchval=None)

    async def fake_get_core_pool():
        return pool

    monkeypatch.setattr(data, "get_core_pool", fake_get_core_pool)
    result = run(data.resolve_user_id("999999999999999999"))
    assert result is None


def test_watchlist_uses_the_watchlist_pool_not_core(monkeypatch):
    """Regression test for the root cause of 'my linked account has a
    watchlist entry but /watchlist says empty': querying the watchlist
    table through the core pool instead of get_watchlist_pool()."""
    calls = {}

    async def fake_get_watchlist_pool():
        calls["watchlist_pool_used"] = True
        return _FakePool(fetch=[{"id": 1, "card_id": 1, "player_name": "Mbappe", "version": "TOTS",
                                  "platform": "ps", "started_price": 100000, "last_price": 120000,
                                  "started_at": NOW}])

    async def fake_get_core_pool():
        calls["core_pool_used"] = True
        return _FakePool(fetch=[])

    monkeypatch.setattr(data, "get_watchlist_pool", fake_get_watchlist_pool)
    monkeypatch.setattr(data, "get_core_pool", fake_get_core_pool)
    items = run(data.get_watchlist("user-abc"))
    assert calls.get("watchlist_pool_used") is True
    assert calls.get("core_pool_used") is None
    assert len(items) == 1
    assert items[0]["player_name"] == "Mbappe"


def test_alerts_uses_the_watchlist_pool_not_core(monkeypatch):
    async def fake_get_watchlist_pool():
        return _FakePool(fetch=[{"card_id": 1, "platform": "ps", "metric": "price", "ref_mode": "last_close",
                                  "ref_price": 100000, "rise_pct": 5, "fall_pct": 5, "last_alert_at": None}])

    async def fake_get_core_pool():
        raise AssertionError("alerts must not be read from the core pool")

    monkeypatch.setattr(data, "get_watchlist_pool", fake_get_watchlist_pool)
    monkeypatch.setattr(data, "get_core_pool", fake_get_core_pool)
    items = run(data.get_alerts("user-abc"))
    assert len(items) == 1


def test_trades_use_the_core_pool(monkeypatch):
    async def fake_get_core_pool():
        return _FakePool(fetch=[{"player": "Haaland", "version": "TOTS", "buy": 100000, "sell": 130000,
                                  "profit": 23500, "ea_tax": 6500, "platform": "ps", "timestamp": NOW}])

    monkeypatch.setattr(data, "get_core_pool", fake_get_core_pool)
    trades = run(data.get_trades("user-abc"))
    assert len(trades) == 1
    assert trades[0]["player"] == "Haaland"


# ---------------------------------------------------------------------------
# /results resilience - must not raise on null legacy columns
# ---------------------------------------------------------------------------


def test_results_embed_handles_null_legacy_columns_without_raising():
    trades = [
        {"player": "Legacy Trade", "version": None, "buy": None, "sell": None, "profit": None, "timestamp": None},
        {"player": "Haaland", "version": "TOTS", "buy": 100000, "sell": 130000, "profit": 23500, "timestamp": NOW},
    ]
    embed = embeds.results_embed(trades, "https://app.eafc.live")
    assert "Legacy Trade" in embed["description"]
    assert "Haaland" in embed["description"]
    assert "23,500" in embed["footer"]["text"]


def test_results_embed_empty_says_no_trades_not_an_error():
    embed = embeds.results_embed([], "https://app.eafc.live")
    assert "haven't logged" in embed["description"]


# ---------------------------------------------------------------------------
# Unlinked vs genuinely empty
# ---------------------------------------------------------------------------


def test_watchlist_not_linked_message_differs_from_empty_message():
    not_linked = embeds.not_linked_embed("https://app.eafc.live")
    empty = embeds.watchlist_embed([], "https://app.eafc.live")
    assert "link" in not_linked["description"].lower()
    assert "empty" in empty["description"].lower()
    assert not_linked["title"] != empty["title"]


def test_build_reply_unlinked_discord_id_gets_not_linked_message(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value=None))
    reply = run(_build_reply("watchlist", [], "999999999999999999", "corr123"))
    assert "not linked" in reply["embeds"][0]["title"].lower() or "link" in reply["embeds"][0]["description"].lower()


def test_build_reply_no_discord_id_at_all_gets_not_linked_message():
    reply = run(_build_reply("account", [], None, "corr123"))
    assert "link" in reply["embeds"][0]["description"].lower()


def test_build_reply_genuinely_empty_account_says_empty(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_watchlist", AsyncMock(return_value=[]))
    reply = run(_build_reply("watchlist", [], "712239212749127740", "corr123"))
    assert "empty" in reply["embeds"][0]["description"].lower()


def test_build_reply_existing_watchlist_records_are_shown(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_watchlist", AsyncMock(return_value=[
        {"player_name": "Mbappe", "version": "TOTS", "started_price": 100000, "last_price": 120000},
    ]))
    reply = run(_build_reply("watchlist", [], "712239212749127740", "corr123"))
    assert "Mbappe" in reply["embeds"][0]["description"]


def test_build_reply_existing_trades_are_shown(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_trades", AsyncMock(return_value=[
        {"player": "Haaland", "version": "TOTS", "buy": 100000, "sell": 130000, "profit": 23500, "timestamp": NOW},
    ]))
    reply = run(_build_reply("results", [], "712239212749127740", "corr123"))
    assert "Haaland" in reply["embeds"][0]["description"]


def test_build_reply_existing_alerts_are_shown(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_alerts", AsyncMock(return_value=[
        {"card_id": 1, "platform": "ps", "metric": "price", "rise_pct": 5, "fall_pct": 5, "last_alert_at": None},
    ]))
    reply = run(_build_reply("alerts", [], "712239212749127740", "corr123"))
    assert "Card 1" in reply["embeds"][0]["description"]


def test_build_reply_account_never_includes_email(monkeypatch):
    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_account_summary", AsyncMock(return_value={
        "tier": "pro", "premium_until": None, "watchlist_count": 2, "alerts_count": 1, "trades_count": 9,
    }))
    reply = run(_build_reply("account", [], "712239212749127740", "corr123"))
    payload_text = str(reply)
    assert "@" not in payload_text  # no email address leaked into the reply


# ---------------------------------------------------------------------------
# Card search: no silent version guessing
# ---------------------------------------------------------------------------


def test_price_by_card_id_does_not_search(monkeypatch):
    mock_get_card = AsyncMock(return_value={"source_card_id": 42, "name": "Messi", "rating": 99, "version": "TOTS",
                                             "current_executable_buy": 1000000})
    mock_search = AsyncMock()
    monkeypatch.setattr(data, "get_card", mock_get_card)
    monkeypatch.setattr(data, "search_cards", mock_search)
    reply = run(_build_reply("price", [{"name": "player", "value": "42"}], None, "corr123"))
    mock_get_card.assert_awaited_once_with(42)
    mock_search.assert_not_called()
    assert "Messi" in reply["embeds"][0]["title"]


def test_price_free_text_with_multiple_matches_does_not_guess(monkeypatch):
    monkeypatch.setattr(data, "search_cards", AsyncMock(return_value=[
        {"source_card_id": 1, "name": "Lionel Messi", "rating": 99, "version": "FOF"},
        {"source_card_id": 2, "name": "Lionel Messi", "rating": 90, "version": "Gold"},
    ]))
    reply = run(_build_reply("price", [{"name": "player", "value": "messi"}], None, "corr123"))
    assert "content" in reply
    assert "pick one" in reply["content"].lower()


def test_price_free_text_no_matches_says_no_card_found(monkeypatch):
    monkeypatch.setattr(data, "search_cards", AsyncMock(return_value=[]))
    reply = run(_build_reply("price", [{"name": "player", "value": "zzzznotaplayer"}], None, "corr123"))
    assert "no matching card" in reply["embeds"][0]["title"].lower()


# ---------------------------------------------------------------------------
# Fair value: never a confident verdict on insufficient data
# ---------------------------------------------------------------------------


def test_fairvalue_insufficient_data_is_not_a_confident_verdict():
    class CI:
        status = "insufficient_data"
        signal = "insufficient_data"
        fair_value = None
        risk_level = None
        liquidity_score = None

    row = {"source_card_id": 1, "name": "New Card", "rating": 75, "version": "Gold", "current_executable_buy": 5000}
    embed = embeds.fairvalue_embed(row, CI(), "https://app.eafc.live")
    assert embed["fields"][3]["value"] == "Insufficient data"
    assert "not enough" in embed.get("description", "").lower()


# ---------------------------------------------------------------------------
# /results error handling: correlation ID, no swallowed exception
# ---------------------------------------------------------------------------


def test_run_command_logs_and_returns_correlation_id_on_db_failure(monkeypatch, caplog):
    async def boom(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(data, "resolve_user_id", AsyncMock(return_value="user-abc"))
    monkeypatch.setattr(data, "get_trades", boom)
    followups = []

    async def fake_followup(app_id, token, payload):
        followups.append(payload)

    monkeypatch.setattr(interactions, "_followup", fake_followup)
    with caplog.at_level("ERROR"):
        run(interactions._run_command("app123", "token123", "results", [], "712239212749127740"))
    assert len(followups) == 1
    description = followups[0]["embeds"][0]["description"]
    assert "unexpected error" in description.lower()
    # The same correlation id must appear in both the log and the user-facing reply.
    corr_id_in_reply = description.split("`")[1]
    assert any(corr_id_in_reply in record.message or corr_id_in_reply in str(record.args) for record in caplog.records)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_after_threshold():
    discord_id = "111"
    for _ in range(10):
        assert _rate_limited(discord_id, "watchlist") is False
    assert _rate_limited(discord_id, "watchlist") is True


def test_rate_limit_is_per_user():
    assert _rate_limited("aaa", "account") is False
    for _ in range(10):
        assert _rate_limited("bbb", "account") is False
    assert _rate_limited("bbb", "account") is True
    assert _rate_limited("aaa", "account") is False


def test_autocomplete_rate_limit_is_separate_from_command_rate_limit():
    for _ in range(30):
        assert _rate_limited("ccc", "price", autocomplete=True) is False
    assert _rate_limited("ccc", "price", autocomplete=True) is True
    # The command rate limit bucket for the same user/command is untouched.
    assert _rate_limited("ccc", "price") is False


# ---------------------------------------------------------------------------
# Interaction response timing / deferral / ephemeral handling
# ---------------------------------------------------------------------------


from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from fastapi import FastAPI


def _signed_request_kwargs(body: dict) -> dict:
    from nacl.signing import SigningKey
    import json as _json

    signing_key = SigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()
    raw_body = _json.dumps(body).encode()
    timestamp = "1700000000"
    signature_hex = signing_key.sign(timestamp.encode() + raw_body).signature.hex()
    return {
        "public_key": public_key_hex,
        "headers": {
            "X-Signature-Ed25519": signature_hex,
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
        "content": raw_body,
    }


def _client(monkeypatch, public_key_hex):
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", public_key_hex)
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "app123")
    app = FastAPI()
    app.include_router(interactions.router)
    return TestClient(app)


def test_ping_returns_pong_without_touching_the_database(monkeypatch):
    body = {"type": PING}
    kwargs = _signed_request_kwargs(body)
    client = _client(monkeypatch, kwargs["public_key"])
    resp = client.post("/api/discord/interactions", content=kwargs["content"], headers=kwargs["headers"])
    assert resp.status_code == 200
    assert resp.json() == {"type": PONG}


def test_application_command_returns_deferred_ack_immediately(monkeypatch):
    called = {}

    def fake_add_task(self, func, *args, **kwargs):
        called["scheduled"] = True

    monkeypatch.setattr(BackgroundTasks, "add_task", fake_add_task)
    body = {
        "type": APPLICATION_COMMAND,
        "token": "interaction-token",
        "data": {"name": "price", "options": [{"name": "player", "value": "1"}]},
        "user": {"id": "712239212749127740"},
    }
    kwargs = _signed_request_kwargs(body)
    client = _client(monkeypatch, kwargs["public_key"])
    resp = client.post("/api/discord/interactions", content=kwargs["content"], headers=kwargs["headers"])
    assert resp.status_code == 200
    assert resp.json()["type"] == DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    assert called.get("scheduled") is True


def test_account_command_ack_is_ephemeral(monkeypatch):
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, func, *a, **kw: None)
    body = {
        "type": APPLICATION_COMMAND,
        "token": "interaction-token",
        "data": {"name": "account", "options": []},
        "user": {"id": "712239212749127740"},
    }
    kwargs = _signed_request_kwargs(body)
    client = _client(monkeypatch, kwargs["public_key"])
    resp = client.post("/api/discord/interactions", content=kwargs["content"], headers=kwargs["headers"])
    payload = resp.json()
    assert payload["type"] == DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    assert payload["data"]["flags"] == EPHEMERAL


def test_price_command_ack_is_not_ephemeral(monkeypatch):
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, func, *a, **kw: None)
    body = {
        "type": APPLICATION_COMMAND,
        "token": "interaction-token",
        "data": {"name": "price", "options": [{"name": "player", "value": "1"}]},
        "user": {"id": "712239212749127740"},
    }
    kwargs = _signed_request_kwargs(body)
    client = _client(monkeypatch, kwargs["public_key"])
    resp = client.post("/api/discord/interactions", content=kwargs["content"], headers=kwargs["headers"])
    payload = resp.json()
    assert payload["type"] == DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    assert "data" not in payload or "flags" not in payload.get("data", {})


def test_autocomplete_interaction_type_answered_synchronously(monkeypatch):
    monkeypatch.setattr(data, "search_cards", AsyncMock(return_value=[
        {"source_card_id": 1, "name": "Lionel Messi", "rating": 99, "version": "TOTS"},
    ]))
    body = {
        "type": APPLICATION_COMMAND_AUTOCOMPLETE,
        "data": {"name": "price", "options": [{"name": "player", "value": "messi", "focused": True}]},
        "user": {"id": "712239212749127740"},
    }
    kwargs = _signed_request_kwargs(body)
    client = _client(monkeypatch, kwargs["public_key"])
    resp = client.post("/api/discord/interactions", content=kwargs["content"], headers=kwargs["headers"])
    payload = resp.json()
    assert payload["type"] == APPLICATION_COMMAND_AUTOCOMPLETE_RESULT
    assert payload["data"]["choices"][0]["value"] == "1"


def test_invalid_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", "aa" * 32)
    app = FastAPI()
    app.include_router(interactions.router)
    client = TestClient(app)
    resp = client.post(
        "/api/discord/interactions",
        content=b'{"type":1}',
        headers={"X-Signature-Ed25519": "00" * 64, "X-Signature-Timestamp": "1700000000"},
    )
    assert resp.status_code == 401
