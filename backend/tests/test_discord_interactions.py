from app.routers.discord_interactions import (
    HELP_TEXT,
    _command_hits,
    _option,
    _rate_limited,
    _respond,
)


def setup_function(_):
    _command_hits.clear()


def test_help_lists_every_command():
    for cmd in ("/price", "/fairvalue", "/watchlist", "/alerts", "/results", "/account", "/help"):
        assert cmd in HELP_TEXT


def test_option_extracts_named_value():
    options = [{"name": "player", "value": "Mbappe"}]
    assert _option(options, "player") == "Mbappe"
    assert _option(options, "missing") is None
    assert _option(None, "player") is None


def test_ephemeral_response_sets_flag():
    resp = _respond("hi", ephemeral=True)
    assert resp["data"]["flags"] == 64
    resp = _respond("hi")
    assert "flags" not in resp["data"]


def test_rate_limit_blocks_after_threshold():
    discord_id = "111"
    for _ in range(10):
        assert _rate_limited(discord_id, "watchlist") is False
    assert _rate_limited(discord_id, "watchlist") is True


def test_rate_limit_is_per_user():
    assert _rate_limited("aaa", "account") is False
    # A different user starts with a fresh bucket.
    for _ in range(10):
        assert _rate_limited("bbb", "account") is False
    assert _rate_limited("bbb", "account") is True
    assert _rate_limited("aaa", "account") is False
