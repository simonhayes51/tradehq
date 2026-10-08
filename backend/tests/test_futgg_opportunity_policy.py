from decimal import Decimal
from types import SimpleNamespace

from app.services import futgg_opportunity_policy as policy
from app.services import trading_math as tm
from app.routers.v2 import futgg_market
from app.services import discord_trading_feed


def test_app_and_discord_use_the_same_live_opportunity_policy():
    assert futgg_market.LIVE_OPPORTUNITY_MIN_LIQUIDITY_SCORE == policy.MIN_OPPORTUNITY_LIQUIDITY
    assert futgg_market.LIVE_OPPORTUNITY_MAX_PRICE_AGE_MINUTES == policy.MAX_OPPORTUNITY_PRICE_AGE_MINUTES
    assert discord_trading_feed.DISCORD_FEED_MIN_CONFIDENCE == policy.MIN_OPPORTUNITY_CONFIDENCE
    assert discord_trading_feed.DISCORD_FEED_MIN_ROI == policy.MIN_OPPORTUNITY_ROI
    assert discord_trading_feed.DISCORD_FEED_MIN_LIQUIDITY == policy.MIN_OPPORTUNITY_LIQUIDITY
    assert discord_trading_feed.OPPORTUNITY_MAX_PRICE_AGE_MINUTES == policy.MAX_OPPORTUNITY_PRICE_AGE_MINUTES


def test_max_buy_profit_and_roi_use_identical_conservative_entry():
    ci = SimpleNamespace(
        recommended_buy_max=Decimal("1000"),
        recommended_sell_target=Decimal("1200"),
    )
    assert policy.minimum_profit_at_max_buy(ci) == tm.net_profit(1200, 1000)
    assert policy.roi_at_max_buy(ci) == tm.net_roi(1200, 1000)
    assert policy.minimum_profit_at_max_buy(ci) == Decimal("140.00")


def test_missing_economics_remain_unknown():
    ci = SimpleNamespace(recommended_buy_max=None, recommended_sell_target=1200)
    assert policy.minimum_profit_at_max_buy(ci) is None
    assert policy.roi_at_max_buy(ci) is None
