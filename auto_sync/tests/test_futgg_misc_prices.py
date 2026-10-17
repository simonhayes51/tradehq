"""Tests for parsing and change-filtering the FUT.GG misc prices feed."""
from datetime import datetime, timedelta, timezone

import pytest

from futgg_misc_prices import parse_misc_price_feed, select_misc_rows_to_write


def _feed(*, price=250, status="available"):
    return {
        "v": 1,
        "game": "27",
        "platform": "pc",
        "items": {
            "manager": {
                "1000047": {
                    "price": price,
                    "priceUpdatedAt": "2026-10-17T11:01:25.479687Z",
                    "lastCheckedAt": "2026-10-17T11:01:25.479687Z",
                    "status": status,
                }
            },
            "manager_league": {
                "13": {
                    "price": None,
                    "priceUpdatedAt": None,
                    "lastCheckedAt": "2026-10-17T11:01:26Z",
                    "status": "no_listings",
                }
            },
            "chemistry_style": {
                "250": {
                    "price": 700,
                    "priceUpdatedAt": "2026-10-17T11:01:27Z",
                    "lastCheckedAt": "2026-10-17T11:01:27Z",
                    "status": "available",
                }
            },
        },
    }


def test_parser_keeps_manager_consumable_groups_and_null_market_status():
    rows = parse_misc_price_feed(_feed(), game_year=27, platform="pc")
    assert [(r.item_type, r.item_id) for r in rows] == [
        ("manager", 1000047), ("manager_league", 13), ("chemistry_style", 250)
    ]
    no_market = rows[1]
    assert no_market.price is None
    assert no_market.status == "no_listings"
    assert no_market.last_checked_at == datetime(2026, 10, 17, 11, 1, 26, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "payload,year,platform",
    [(_feed(), 26, "pc"), (_feed(), 27, "ps5")],
)
def test_parser_rejects_wrong_game_or_platform(payload, year, platform):
    with pytest.raises(ValueError):
        parse_misc_price_feed(payload, game_year=year, platform=platform)


def test_change_filter_records_price_or_status_changes_and_heartbeats():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    rows = parse_misc_price_feed(_feed(), game_year=27, platform="pc")
    state = {}
    baseline, changed, heartbeats = select_misc_rows_to_write(
        rows, now=now, game_year=27, platform="pc", state=state
    )
    assert len(baseline) == 3 and changed == 3 and heartbeats == 0

    # A fresh copy with the same price/status does not create write churn.
    rows = parse_misc_price_feed(_feed(), game_year=27, platform="pc")
    records, changed, heartbeats = select_misc_rows_to_write(
        rows, now=now + timedelta(minutes=1), game_year=27, platform="pc", state=state
    )
    assert records == [] and changed == 0 and heartbeats == 0

    # Price changes are captured immediately.
    rows = parse_misc_price_feed(_feed(price=275), game_year=27, platform="pc")
    records, changed, heartbeats = select_misc_rows_to_write(
        rows, now=now + timedelta(minutes=2), game_year=27, platform="pc", state=state
    )
    assert records[0][3:6] == (1000047, 275, "available")
    assert changed == 1 and heartbeats == 0


def test_parser_does_not_turn_null_into_zero_and_rejects_invalid_price():
    payload = _feed()
    assert parse_misc_price_feed(payload, game_year=27, platform="pc")[1].price is None
    payload["items"]["manager"]["1000047"]["price"] = -1
    with pytest.raises(ValueError):
        parse_misc_price_feed(payload, game_year=27, platform="pc")
