"""Regression guards for daily FC27 card metadata discovery."""
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "futgg_player_sync.py"


def test_daily_discovery_checks_new_and_catalogue_front_pages():
    source = SOURCE.read_text()
    daily = source.split("async def collect_listing_urls(", 1)[1]
    daily = daily.split("    combined: dict", 1)[0]
    assert '"daily-new", f"{GAME_PREFIX}/players/new/?page={{page}}", MAX_PAGES' in daily
    assert '"daily-catalogue", f"{GAME_PREFIX}/players/?page={{page}}", DAILY_CATALOGUE_PAGES' in daily
    assert "canonical_first_page=True, target_game_year=GAME_YEAR" in daily
    assert "FUTGG_DAILY_CATALOGUE_PAGES" in source


def test_feed_only_placeholders_are_not_skipped_as_known():
    source = SOURCE.read_text()
    assert "NOT ('feed_only_placeholder' = ANY(metadata_warnings))" in source
    assert "FC%d incomplete catalogue rows before enrichment" in source
    assert "metadata_warnings = EXCLUDED.metadata_warnings" in source


def test_alternate_first_page_remains_available_on_empty_grid():
    source = SOURCE.read_text()
    assert "_alternate_first_listing_url(listing_url)" in source
