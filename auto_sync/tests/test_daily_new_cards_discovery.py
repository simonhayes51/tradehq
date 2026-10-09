"""Daily discovery must use FUT.GG's canonical first listing page.

The public newest-cards grid is /players/new/, not necessarily equivalent
to /players/new/?page=1 while its client-side data hydrates.
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "futgg_player_sync.py"


def test_daily_discovery_canonicalises_first_page():
    source = SOURCE.read_text()
    daily = source.split("if not FULL_SCAN:", 1)[1].split("combined: dict", 1)[0]
    assert 'MAX_PAGES, "daily-new",' in daily
    assert "canonical_first_page=True, target_game_year=GAME_YEAR" in daily


def test_alternate_first_page_remains_available_on_empty_grid():
    source = SOURCE.read_text()
    assert "_alternate_first_listing_url(listing_url)" in source
    assert 'canonical_first_page=True' in source
