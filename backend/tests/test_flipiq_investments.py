from datetime import datetime, timedelta, timezone

from app.services.flipiq_investments import investment_case


def test_no_history_is_not_promoted():
    result = investment_case([], current_bin=50000)
    assert result["qualified"] is False


def test_single_fake_low_bin_is_not_an_investment():
    now = datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
    history = [(60000, now - timedelta(hours=h)) for h in range(12, 72)]
    history += [(60000, now - timedelta(hours=h)) for h in range(6)]
    assert investment_case(history, current_bin=40000, now=now)["reason"] == "latest_bin_outlier"


def test_sustained_discount_produces_research_not_buy():
    now = datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
    history = [(60000, now - timedelta(hours=h)) for h in range(12, 72)]
    history += [(45000, now - timedelta(hours=h)) for h in range(6)]
    case = investment_case(history, current_bin=45000, now=now)
    assert case["qualified"] is True
    assert case["status"] == "RESEARCH"
    assert case["hypothetical_net_profit"] >= 10000
    assert case["market_depth_verified"] is False
    assert case["ready_to_publish"] is False


def test_thin_observations_do_not_qualify():
    now = datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
    history = [(60000, now - timedelta(hours=h)) for h in range(12, 72, 12)]
    history += [(45000, now - timedelta(hours=h)) for h in range(6)]
    assert investment_case(history, current_bin=45000, now=now)["qualified"] is False
