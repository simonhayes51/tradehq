from app.services.leak_monitor import LeakMonitor


def test_regex_rating_name_and_card_type():
    parsed = LeakMonitor.parse_regex("91 Mbappe - TOTY is coming to FC27")
    assert parsed.rating == 91
    assert parsed.player_name.lower().startswith("mbappe")
    assert parsed.card_type == "TOTY"
    assert parsed.parse_confidence >= 0.9


def test_regex_name_rating_format():
    parsed = LeakMonitor.parse_regex("Bellingham 90 RTTK leaked for tonight")
    assert parsed.rating == 90
    assert "bellingham" in parsed.player_name.lower()
    assert parsed.card_type == "RTTK"
    assert parsed.useful is True


def test_regex_does_not_invent_rating():
    parsed = LeakMonitor.parse_regex("New FUTTIES player coming tomorrow")
    assert parsed.rating is None
    assert parsed.card_type == "FUTTIES"


def test_plain_non_leak_text_stays_low_confidence():
    parsed = LeakMonitor.parse_regex("What a match that was")
    assert parsed.player_name is None
    assert parsed.rating is None
    assert parsed.card_type is None
    assert parsed.parse_confidence == 0
