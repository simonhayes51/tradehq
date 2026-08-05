"""
Tests for futgg_common.py's _parse_card_visual() image-branch logic -
specifically the new card_image_url capture (the single, pre-composited
full-card image FUT.GG itself renders) and its distinction from the
existing player_image_url (cutout only) / card_design_image_url
(background only) branches. No network/DB needed - BeautifulSoup against
inline HTML fixtures only.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futgg_common import FutggCard, _parse_card_visual, _upsize_card_image

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


def test_composited_card_image_is_captured_as_card_image_url():
    html = f"""
    <div class="fc-card">
        <img alt="Hauge - 96 - FUTTIES" src="{_THUMB_URL}">
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    card = _card()
    _parse_card_visual(soup, card)
    assert card.card_image_url is not None
    assert "futgg-player-item-card" in card.card_image_url
    assert card.player_image_url is None
    assert card.card_design_image_url is None


def test_composited_card_image_is_upsized_from_the_300px_thumbnail():
    html = f"""<div class="fc-card"><img alt="x" src="{_THUMB_URL}"></div>"""
    soup = BeautifulSoup(html, "html.parser")
    card = _card()
    _parse_card_visual(soup, card)
    assert "width=300" not in card.card_image_url
    assert "width=512" in card.card_image_url


def test_cutout_and_background_images_are_still_captured_separately():
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
    assert card.card_image_url is None


def test_all_three_image_kinds_coexist_without_collision():
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
    assert card.card_image_url is not None and "futgg-player-item-card" in card.card_image_url
    assert card.player_image_url == _CUTOUT_URL
    assert card.card_design_image_url == _BACKGROUND_URL


def test_upsize_card_image_replaces_only_the_width_param():
    url = "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=300/x.webp"
    upsized = _upsize_card_image(url, width=750)
    assert upsized == "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=750/x.webp"


def test_upsize_card_image_is_a_no_op_for_a_non_cdn_url():
    url = "https://game-assets.fut.gg/26/player-item/84119928.png"
    assert _upsize_card_image(url) == url
