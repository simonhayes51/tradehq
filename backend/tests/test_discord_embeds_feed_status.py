from app.services.discord_embeds import feed_status_embed


def _base_feed(**overrides):
    base = dict(
        plan="server_feed", subscription_status="active", status="active",
        feed_channel_id="123", platform_scope="console",
        current_period_end=None, budget_low=2, budget_medium=2, budget_high=2,
        max_posts_per_day=6, test_post_confirmed_at="2026-01-01", bot_removed_at=None,
    )
    base.update(overrides)
    return base


def test_feed_status_embed_shows_any_time_when_no_window_set():
    embed = feed_status_embed(_base_feed(), posts_used=0, channel_ok=True, channel_problem=None)
    field = next(f for f in embed["fields"] if f["name"] == "POSTING WINDOW")
    assert field["value"] == "Any time"


def test_feed_status_embed_shows_configured_window():
    feed = _base_feed(posting_window_start_hour=8, posting_window_end_hour=23, posting_timezone="Europe/London")
    embed = feed_status_embed(feed, posts_used=0, channel_ok=True, channel_problem=None)
    field = next(f for f in embed["fields"] if f["name"] == "POSTING WINDOW")
    assert field["value"] == "08:00–23:00 (Europe/London)"


def test_feed_status_embed_defaults_missing_timezone_label_to_utc():
    feed = _base_feed(posting_window_start_hour=8, posting_window_end_hour=23, posting_timezone=None)
    embed = feed_status_embed(feed, posts_used=0, channel_ok=True, channel_problem=None)
    field = next(f for f in embed["fields"] if f["name"] == "POSTING WINDOW")
    assert field["value"] == "08:00–23:00 (UTC)"
