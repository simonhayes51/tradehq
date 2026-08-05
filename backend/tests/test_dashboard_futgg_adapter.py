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

from app.routers.v2.dashboard import _futgg_to_recommendation
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
    # No generated-card-art pipeline exists for FUT.GG cards (unlike the
    # legacy FUTBIN async-generation flow) - these must be omitted, not
    # fabricated, matching this module's own "omit rather than fake" rule.
    assert d["player"]["cardBgImage"] is None
    assert d["player"]["cardCutoutImage"] is None


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
