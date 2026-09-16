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


def test_concrete_leaked_card_is_publishable():
    text = "91 Mbappe TOTY leaked for Friday"
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is True


def test_upcoming_sbc_is_publishable():
    text = "New Icon SBC coming tonight"
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is True


def test_trading_discord_ad_is_not_publishable():
    text = "We offer THE COMPLETE FUT experience. Our FUT Content alerts updates instantly with New Evolution and New SBC channels."
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is False


def test_gameplay_banter_is_not_publishable():
    text = "If they patch FC 27 with AI Defending we are finished"
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is False


def test_generic_market_opinion_is_not_publishable():
    text = "EASILY one of the best years for the FUT market inbound. Certain cards will rise like crazy."
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is False


def test_launch_schedule_is_not_publishable():
    text = "FC 27 HAS ARRIVED. Streamers = Playing. Web App = Today. TOTW 1 = Today."
    parsed = LeakMonitor.parse_regex(text)
    assert LeakMonitor.is_relevant_content(text, parsed) is False
