"""Check daily discovery waits for a real FC27 card, not nav links."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_player_sync.py").read_text()


def test_listing_wait_requires_game_year_card_url():
    ast.parse(SOURCE)
    assert SOURCE.count("page.locator(f\"a[href*='/players/'][href*='/{GAME_YEAR}-']\").first.wait_for") == 2
    assert "page.locator(\"a[href*='/players/']\").first.wait_for" not in SOURCE


def test_listing_regex_is_used_for_card_validation():
    assert "CARD_URL_RE.match(parsed.path)" in SOURCE
    assert "current_hrefs = await _read_card_hrefs(page)" in SOURCE
