# tests/test_dashboard_futgg_adapter.py
#
# Unit tests for app/routers/v2/dashboard.py's _futgg_to_recommendation()
# - the adapter that replaced the legacy recommendations_latest/
# fut_players/fair_value_mv-backed _to_recommendation() call sites with
# the FUT.GG-native (futgg_market_snapshot row, CardIntelligence) pair
# Trade Finder/Opportunities already score successfully. Pure-function
# tests, no DB needed - same "no DB needed" bar test_futgg_market_router.py
# already sets for this data source.
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict

from app.routers.v2.dashboard import _compute_live_market_regime, _futgg_to_recommendation
from app.services.futgg_intelligence import CardIntelligence

AS_OF = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _row(**overrides) -> Dict[str, Any]:
    defaults = dict(
        source_card_id=84131117, name="Eric Garcia", rating=91,
        rarity="gold_rare", primary_position="CB",
        player_image_url="https://cdn.example/84131117.png",
        nation_image_url="https://cdn.example/nation.png",
        league_image_url="https://cdn.example/league.png",
        club_image_url="https://cdn.example/club.png",
        nation="Spain", league="La Liga", current_bin=50000,
        sales_count=42, sales_stddev=1200.0, sales_window_span_minutes=600.0,
        card_design_image_url=None, price_tier="gold_rare",
        pace=None, shooting=None, passing=None, dribbling=None,
        defending=None, physicality=None,
    )
    defaults.update(overrides)
    return defaults


def _ci(**overrides) -> CardIntelligence:
    defaults = dict(
        card_id=84131117, signal="buy", risk_level="low",
        confidence_score=0.82, expected_roi=Decimal("0.15"),
        fair_value=Decimal("48000"), current_executable_buy=Decimal("47000"),
        break_even_price=Decimal("49500"), signal_reasons=["liquid market", "under fair value"],
        evaluated_at=AS_OF, engine_version="futgg-v1",
    )
    defaults.update(overrides)
    return CardIntelligence(**defaults)


def test_buy_signal_maps_to_buy_status_with_percent_confidence():
    d = _futgg_to_recommendation(_row(), _ci(signal="buy", confidence_score=0.82))
    assert d["status"] == "BUY"
    assert d["recommendation"] == "BUY"
    assert d["confidence"] == 82.0  # 0-1 scale converted to 0-100, matching the legacy contract


def test_strong_buy_signal_also_maps_to_buy_status():
    d = _futgg_to_recommendation(_row(), _ci(signal="strong_buy"))
    assert d["status"] == "BUY"


def test_avoid_signal_maps_to_avoid_status():
    d = _futgg_to_recommendation(_row(), _ci(signal="avoid"))
    assert d["status"] == "AVOID"
    assert d["recommendation"] == "AVOID"


def test_watch_and_hold_signals_map_to_wait_status():
    assert _futgg_to_recommendation(_row(), _ci(signal="watch"))["status"] == "WAIT"
    assert _futgg_to_recommendation(_row(), _ci(signal="hold"))["status"] == "WAIT"


def test_sell_signal_maps_to_sell_status():
    assert _futgg_to_recommendation(_row(), _ci(signal="sell"))["status"] == "SELL"


def test_movers_card_has_no_ci_and_renders_no_verdict():
    """Movers are a price-activity signal, not an AI verdict - passing
    ci=None must render status/recommendation as None rather than
    fabricating a BUY/AVOID call the mover never earned (same guard the
    legacy _to_recommendation() enforced via has_evaluation)."""
    d = _futgg_to_recommendation(_row(), None)
    assert d["status"] is None
    assert d["recommendation"] is None
    assert d["confidence"] == 0.0
    assert d["reasoning"] == ""


def test_player_fields_come_from_the_futgg_snapshot_row():
    d = _futgg_to_recommendation(_row(name="Eric Garcia", rating=91), _ci())
    assert d["cardId"] == 84131117
    assert d["player"]["name"] == "Eric Garcia"
    assert d["player"]["rating"] == 91
    assert d["player"]["imageUrl"] == "https://cdn.example/84131117.png"
    # No generated-card-art pipeline exists for FUT.GG cards, but no
    # generation is needed either: FUT.GG already serves the frame art
    # directly via card_design_image_url. With none captured on this row,
    # cardBgImage stays honestly None rather than fabricated.
    assert d["player"]["cardBgImage"] is None
    assert d["player"]["cardCutoutImage"] == "https://cdn.example/84131117.png"


def test_card_frame_art_is_surfaced_when_the_snapshot_row_has_it():
    d = _futgg_to_recommendation(
        _row(card_design_image_url="https://cdn.example/rarities-gold-rare.png", price_tier="gold_rare"),
        _ci(),
    )
    assert d["player"]["cardBgImage"] == "https://cdn.example/rarities-gold-rare.png"
    assert d["player"]["cardCutoutImage"] == "https://cdn.example/84131117.png"
    # A frame exists, so CardImage should render PlayerCardArt rather than
    # short-circuiting to the bare <img generatedCardUrl> path.
    assert d["player"]["generatedCardUrl"] is None


def test_composited_card_image_wins_as_generated_card_url_even_with_a_bg_image():
    # Regression test: when the composited full-card image (background+
    # cutout+rating+stats already baked in by FUT.GG, mirrored to R2) is
    # available, it must be used as generatedCardUrl - shown as a plain
    # <img>, never routed through the cardBgImage/cardCutoutImage
    # PlayerCardArt compositing path, which draws its own rating/
    # position/stats text and would double it up. This shipped once as a
    # real production bug (doubled rating digits on card art).
    d = _futgg_to_recommendation(
        _row(
            card_design_image_url="https://cdn.example/rarities-gold-rare.png",
            card_image_stored_url="https://cdn.example.com/fc26/futgg-card-images/1/abc.webp",
        ),
        _ci(),
    )
    assert d["player"]["generatedCardUrl"] == "https://cdn.example.com/fc26/futgg-card-images/1/abc.webp"
    assert d["player"]["generatedCardStatus"] == "ready"


# --- _compute_live_market_regime ---------------------------------------
# Regression coverage for the "Strong Market 95%" bug: the old
# implementation read a single row from `market_states`, a table only
# ever written by analytics_engine.compute_market_regime() - which only
# runs behind the disabled-by-default ENABLE_LEGACY_FUTBIN flag - so it
# never changed. _compute_live_market_regime recomputes the same shape of
# signal directly off the FUT.GG-native (row, CardIntelligence) pairs
# already evaluated for the opportunity feeds, so it must vary with them.
#
# The pools below are scaled with _pool() rather than written out as two
# or three literal cards. That is not cosmetic: the function now refuses
# to characterise a market from a handful of cards at all (see
# MIN_VALUED_CARDS_FOR_REGIME and test_dashboard_market_regime.py), and a
# three-card fixture was only ever a convenience of test-writing - it
# described a situation the dashboard should never render a verdict from.
# Scaling keeps each test's proportions, and therefore what it checks,
# exactly as they were.


def _pool(patterns, copies=12):
    """Repeat a list of (row, intel) patterns into a realistically-sized
    candidate pool, preserving their proportions."""
    return [pattern for _ in range(copies) for pattern in patterns]


def test_empty_candidate_pool_reports_unknown_not_a_fabricated_state():
    result = _compute_live_market_regime([])
    assert result["label"] == "Unknown"
    assert result["dataQuality"] == "LIMITED"
    assert result["confidence"] == 0


def test_mostly_falling_trend_reports_bearish():
    pairs = _pool([
        (_row(), _ci(signal="watch", trend_state="falling_knife")),
        (_row(), _ci(signal="watch", trend_state="downtrend")),
        (_row(), _ci(signal="watch", trend_state="downtrend")),
        (_row(), _ci(signal="watch", trend_state="sideways")),
    ])
    result = _compute_live_market_regime(pairs)
    assert result["label"] == "Prices Dropping"
    assert result["dataQuality"] == "GOOD"


def test_wide_discount_with_little_falling_reports_bullish():
    pairs = _pool([
        (_row(current_bin=45000), _ci(signal="buy", trend_state="sideways", fair_value=Decimal("50000"))),
        (_row(current_bin=44000), _ci(signal="strong_buy", trend_state="sideways", fair_value=Decimal("50000"))),
        (_row(current_bin=45000), _ci(signal="watch", trend_state="sideways", fair_value=Decimal("50000"))),
    ])
    result = _compute_live_market_regime(pairs)
    assert result["label"] == "Good Time to Buy"


def test_low_average_liquidity_reports_slow_trading():
    pairs = _pool([
        (_row(sales_count=1, sales_window_span_minutes=600.0), _ci(signal="hold", trend_state="sideways")),
        (_row(sales_count=1, sales_window_span_minutes=600.0), _ci(signal="hold", trend_state="sideways")),
    ])
    result = _compute_live_market_regime(pairs)
    assert result["label"] == "Slow Trading"


def test_steady_market_is_the_default_when_nothing_stands_out():
    pairs = _pool([
        (_row(sales_count=40, sales_window_span_minutes=60.0), _ci(signal="hold", trend_state="sideways")),
        (_row(sales_count=40, sales_window_span_minutes=60.0), _ci(signal="hold", trend_state="sideways")),
    ])
    result = _compute_live_market_regime(pairs)
    assert result["label"] == "Steady Market"


def test_confidence_scales_with_evidence_not_merely_with_pool_size():
    """This test previously asserted that confidence rose with the
    CANDIDATE COUNT, which is exactly the defect that put "STRONG MARKET
    95/100" next to "BUY SIGNALS 0" on the live dashboard: a big scan of
    cards nothing could be said about scored the same as a well-evidenced
    market. Pool size still bounds confidence, but only alongside the
    share of that pool actually valued."""
    valued_row = (_row(sales_count=40, sales_window_span_minutes=60.0),
                  _ci(signal="hold", trend_state="sideways"))
    unvalued_row = (_row(sales_count=0, sales_window_span_minutes=None),
                    _ci(signal="insufficient_data", trend_state="sideways"))

    small = _compute_live_market_regime([valued_row] * 40)
    large = _compute_live_market_regime([valued_row] * 400)
    assert large["confidence"] > small["confidence"]
    assert large["metrics"]["liquidCards"] == 400

    # Same pool size as `large`, same 400 cards looked at - but three
    # quarters of them could not be valued. Confidence must fall.
    diluted = _compute_live_market_regime([valued_row] * 100 + [unvalued_row] * 300)
    assert diluted["metrics"]["liquidCards"] == 400
    assert diluted["confidence"] < large["confidence"]


def test_composited_card_image_falls_back_to_raw_futgg_url_when_not_yet_mirrored():
    d = _futgg_to_recommendation(
        _row(card_image_stored_url=None, card_image_url="https://game-assets.fut.gg/cdn-cgi/image/.../1.webp"),
        _ci(),
    )
    assert d["player"]["generatedCardUrl"] == "https://game-assets.fut.gg/cdn-cgi/image/.../1.webp"


def test_cutout_type_is_base_for_plain_gold_rare_and_special_otherwise():
    base = _futgg_to_recommendation(_row(price_tier="gold_rare"), _ci())
    assert base["player"]["cardCutoutType"] == "base"
    promo = _futgg_to_recommendation(_row(price_tier="special"), _ci())
    assert promo["player"]["cardCutoutType"] == "special"


def test_per_stat_ratings_are_surfaced_when_present():
    d = _futgg_to_recommendation(
        _row(pace=88, shooting=84, passing=86, dribbling=89, defending=45, physicality=81),
        _ci(),
    )
    assert d["player"]["stats"] == {
        "pace": 88, "shooting": 84, "passing": 86,
        "dribbling": 89, "defending": 45, "physicality": 81,
    }


def test_missing_name_falls_back_to_card_id_label():
    d = _futgg_to_recommendation(_row(name=None), _ci())
    assert d["player"]["name"] == "Card 84131117"


def test_expected_roi_and_fair_value_are_populated_when_available():
    d = _futgg_to_recommendation(_row(current_bin=50000), _ci(expected_roi=Decimal("0.20"), fair_value=Decimal("48000")))
    assert d["expectedRoi"] == 20.0
    assert d["netRoi"]["likely"] == 20.0
    # No conservative/bullish/potential tiers exist in this engine (only
    # a single expected_roi figure) - must stay honestly None, never
    # duplicated or invented from the one number that does exist.
    assert d["netRoi"]["conservative"] is None
    assert d["netRoi"]["bullish"] is None
    assert d["fairValue"] == 48000
    assert d["currentBin"] == 50000


def test_scores_and_card_source_specific_fields_are_honestly_empty():
    """card_scores_latest is keyed by the legacy FUTBIN card_id space
    (migration 024) - not reconciled with FUT.GG card IDs anywhere in
    this codebase (see migrations/038's card_source_map comment), so
    `scores` must stay {} rather than risk joining the wrong card's
    scores in by ID collision."""
    d = _futgg_to_recommendation(_row(), _ci())
    assert d["scores"] == {}
    assert d["qualifiedStrategies"] == []
    assert d["purchasePrice"] is None
    assert d["isHeld"] is False


def test_risk_level_is_title_cased_from_the_engine_string():
    d = _futgg_to_recommendation(_row(), _ci(risk_level="low"))
    assert d["risk"] == "Low"


def test_insufficient_data_signal_maps_correctly():
    d = _futgg_to_recommendation(_row(), _ci(signal="insufficient_data"))
    assert d["status"] == "INSUFFICIENT_DATA"
