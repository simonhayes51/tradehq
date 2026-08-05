"""
Tests for futgg_common.py's _parse_card_visual() image-branch logic and
upsize_card_image() - the cutout (player_image_url) / background
(card_design_image_url) captures, and the explicit absence of composited
card_image_url capture here.

card_image_url is captured elsewhere entirely (futgg_player_sync.py's
collect_listing_urls()/_read_card_hrefs(), tested in
test_futgg_player_sync.py) - a live production backfill confirmed the
composited full-card image FUT.GG renders on the /players/new/ LISTING
grid does NOT also appear on a card's own detail page (100% coverage on
player_image_url/card_design_image_url below, 0% on card_image_url
across the whole catalogue), so _parse_card_visual must never attempt to
capture it - see its own comment for the full story. No network/DB
needed - BeautifulSoup against inline HTML fixtures only.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futgg_common import FutggCard, _parse_card_visual, upsize_card_image

_THUMB_URL = (
    "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=300"
    "/2026/futgg-player-item-card/26-84119928.b8608a4e9131f830.webp"
)
_CUTOUT_URL = "https://game-assets.fut.gg/26/player-item/84119928.png"
_BACKGROUND_URL = "https://game-assets.fut.gg/26/rarities-fut26/rare_gold.png"


def _card() -> FutggCard:
    return FutggCard(
        source_card_id=84119928, source_player_id=1, source_slug="hauge",
        game_year=26, source_url="https://www.fut.gg/players/1-hauge/26-84119928/",
    )


def test_cutout_and_background_images_are_captured_separately():
    html = f"""
    <div class="fc-card">
        <img alt="cutout" src="{_CUTOUT_URL}">
        <img alt="background" src="{_BACKGROUND_URL}">
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = _card()
    _parse_card_visual(soup, card)
    assert card.player_image_url == _CUTOUT_URL
    assert card.card_design_image_url == _BACKGROUND_URL


def test_composited_listing_style_image_is_never_captured_from_a_detail_page():
    # Regression test: this exact image shape DOES appear if you feed it
    # in (e.g. if FUT.GG ever changes the detail page to also render it),
    # but _parse_card_visual must not attempt to read it as
    # card_image_url - that field is only ever set by
    # futgg_player_sync.py, from the listing crawl.
    html = f"""
    <div class="fc-card">
        <img alt="composited" src="{_THUMB_URL}">
        <img alt="cutout" src="{_CUTOUT_URL}">
        <img alt="background" src="{_BACKGROUND_URL}">
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = _card()
    _parse_card_visual(soup, card)
    assert card.card_image_url is None
    assert card.player_image_url == _CUTOUT_URL
    assert card.card_design_image_url == _BACKGROUND_URL


def test_upsize_card_image_replaces_only_the_width_param():
    url = "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=300/x.webp"
    upsized = upsize_card_image(url, width=750)
    assert upsized == "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=750/x.webp"


def test_upsize_card_image_is_a_no_op_for_a_non_cdn_url():
    url = "https://game-assets.fut.gg/26/player-item/84119928.png"
    assert upsize_card_image(url) == url
