"""Check that daily discovery waits for an FC27 card, not the nav."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_player_sync.py").read_text()


def test_listing_wait_requires_game_year_card_url():
    ast.parse(SOURCE)
    assert SOURCE.count('page.locator(f"a[href*=\'/players/\'][href*=\'/{GAME_YEAR}-\']").first.wait_for') == 2
    assert 'page.locator("a[href*=\'/players/\']").first.wait_for' not in SOURCE


def test_listing_card_regex_accepts_new_fc27_urls():
    import re
    regex = re.compile(
        r"^/players/(?P<player_id>\\d+)-(?P<slug>[^/]+)/"
        r"(?P<game_year>\\d+)-(?P<card_id>\\d+)/?$".replace('\\\\', '\\')
    )
    for path in (
        "/players/266127-lewis-hall/27-50597775/",
        "/players/256675-omar-marmoush/27-67365539/",
    ):
        assert regex.match(path)
