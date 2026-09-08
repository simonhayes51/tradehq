from pathlib import Path


SERVICE = Path(__file__).resolve().parents[1] / "app" / "services" / "held_position_refresher.py"


def test_refresher_loads_recorded_target_and_persists_position_analysis():
    source = SERVICE.read_text()
    assert "SELECT trade_id, card_id, card_source, buy, platform, target_sell" in source
    assert "current_position_analysis = $5::jsonb" in source
    assert "decide_held_position(" in source


def test_refresher_no_longer_contains_discovery_target_only_sell_rule():
    source = SERVICE.read_text()
    assert "if target is not None and current >= target" not in source
    assert "_futgg_held_verdict" not in source
