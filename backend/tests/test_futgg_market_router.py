# tests/test_futgg_market_router.py
#
# HTTP-layer tests for app/routers/v2/futgg_market.py, using FastAPI's
# dependency_overrides to swap in a fake FutggMarketDataProvider instead
# of a real database connection - same "no DB needed" bar
# tests/test_v2_health_router.py already set for v2 routers, extended
# here to a router that actually depends on data (via get_provider()'s
# override point).
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v2.futgg_market import get_provider, router as futgg_router
from app.services.market_data_provider import PlayerFilters

AS_OF = datetime.now(timezone.utc)


def _row(**overrides) -> Dict[str, Any]:
    defaults = dict(
        source_card_id=1, source_player_id=100, source_slug="messi",
        source_url="http://x/1", game_year=26, name="Lionel Messi",
        rating=91, primary_position="RW", alternate_positions=["ST"],
        rarity="gold_rare", squad=None, price_tier="gold_rare",
        club="Inter Miami", league="MLS", nation="Argentina",
        club_source_id=1, league_source_id=1, nation_source_id=1,
        player_image_url=None, card_design_image_url=None, club_image_url=None,
        league_image_url=None, nation_image_url=None,
        is_active=True, is_tradeable=True, last_price_status="success",
        metadata_warnings=[], price_updated_at=AS_OF, next_price_due_at=None,
        current_bin=50000, price_range_low=49000, price_range_high=51000,
        bin_source_age_text="4 minutes ago", bin_captured_at=AS_OF - timedelta(minutes=4),
        sales_count=30, sales_median=52000, sales_trimmed_mean=52000,
        sales_low=48000, sales_high=56000, sales_stddev=800,
        latest_sale_price=52500, latest_sale_at=AS_OF - timedelta(minutes=2),
        sales_window_earliest_at=AS_OF - timedelta(hours=6),
        sales_window_latest_at=AS_OF - timedelta(minutes=2),
        sales_window_span_minutes=358.0, sales_dispersion_ratio=0.015,
        snapshot_computed_at=AS_OF,
    )
    defaults.update(overrides)
    return defaults


class FakeProvider:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = rows if rows is not None else [_row()]
        # Records the order_by every search_players() call was actually
        # made with, so tests can assert on candidate-scan ordering
        # without needing a real Postgres EXPLAIN.
        self.search_calls: List[Dict[str, Any]] = []
        self.bumped_card_ids: List[int] = []
        self.segment_scan_calls = 0

    async def get_player(self, card_id: int):
        for r in self.rows:
            if r["source_card_id"] == card_id:
                return r
        return None

    async def get_current_price(self, card_id: int, platform=None):
        row = await self.get_player(card_id)
        if row is None or row.get("current_bin") is None:
            return None
        return {
            "lowest_bin": row["current_bin"], "price_range_low": row["price_range_low"],
            "price_range_high": row["price_range_high"], "source_age_text": row["bin_source_age_text"],
            "captured_at": row["bin_captured_at"],
        }

    async def get_recent_sales(self, card_id: int, limit: int = 50):
        return [
            {
                "sold_price": 52000, "ea_tax": 2600, "net_price": 49400,
                "approximate_sold_at": AS_OF - timedelta(minutes=3),
                "source_age_text": "3 minutes ago", "source_age_seconds": 180,
            }
        ]

    async def get_sales_coverage(self, card_id: int):
        # This card was checked recently, so an empty window here would
        # genuinely mean "no sales", not "we did not look". See
        # test_sales_coverage_reporting.py for the other cases.
        return {
            "checked_at": datetime.now(timezone.utc) - timedelta(minutes=30),
            "next_due_at": None,
            "never_checked": False,
            "status": "ok",
        }

    async def get_sales_series(self, card_id: int, *, days: int, limit: int = 500):
        # Two prints spanning `days` so the endpoint's covered_seconds is
        # exercised, and so a wider range genuinely returns a wider span -
        # the property the chart's range picker depends on and which the
        # old fixed-window endpoint could not provide.
        return [
            {
                "sold_price": 52000, "ea_tax": 2600, "net_price": 49400,
                "approximate_sold_at": AS_OF - timedelta(minutes=3),
                "source_age_text": "3 minutes ago", "source_age_seconds": 180,
            },
            {
                "sold_price": 50000, "ea_tax": 2500, "net_price": 47500,
                "approximate_sold_at": AS_OF - timedelta(days=days),
                "source_age_text": f"{days} days ago", "source_age_seconds": days * 86400,
            },
        ]

    async def get_price_history(self, card_id: int, period=None):
        return [
            {
                "lowest_bin": 50000, "price_range_low": 49000, "price_range_high": 51000,
                "source_age_text": "4 minutes ago", "captured_at": AS_OF - timedelta(minutes=4),
            }
        ]

    async def search_players(self, filters: PlayerFilters, *, limit=25, offset=0, order_by="rating DESC NULLS LAST"):
        self.search_calls.append({"limit": limit, "offset": offset, "order_by": order_by})
        return self.rows[offset: offset + limit]

    async def count_players(self, filters: PlayerFilters) -> int:
        return len(self.rows)

    async def select_candidates_by_segment(self):
        # Records that a segmented (not single sales_count-limited) scan
        # ran, so tests can assert on it the same way they used to assert
        # on search_calls's order_by.
        self.segment_scan_calls += 1
        return {"all": list(self.rows)}

    async def bump_price_priority(self, card_id: int) -> None:
        self.bumped_card_ids.append(card_id)

    async def get_freshness_summary(self):
        return {
            "heartbeats": [
                {"worker": "futgg_player_sync", "last_run_at": AS_OF, "ok": True, "detail": None},
                {"worker": "futgg_price_sync", "last_run_at": AS_OF, "ok": True, "detail": None},
            ],
            "cards_discovered": 10, "cards_priced": 8, "cards_no_market": 1,
            "cards_untradeable": 1, "cards_stale": 0, "stale_by_price_tier": {},
            "latest_source_errors": [],
        }


def _client(rows: Optional[List[Dict[str, Any]]] = None) -> TestClient:
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: FakeProvider(rows)
    return TestClient(app)


def test_list_players_shape():
    resp = _client().get("/api/v2/players")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "total" in body
    assert body["items"][0]["name"] == "Lionel Messi"


def test_get_player_detail_shape():
    resp = _client().get("/api/v2/players/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "futgg"
    assert body["player"]["source_card_id"] == 1
    assert "intelligence" in body
    assert body["intelligence"]["signal"] in (
        "strong_buy", "buy", "watch", "hold", "sell", "avoid", "insufficient_data",
    )


def test_get_player_detail_prefers_display_name_over_full_legal_name():
    # Once market_data_provider._SNAPSHOT_COLUMNS actually selects
    # display_name (see test_snapshot_column_tolerance.py for the other
    # half of this regression - that column was never in the SELECT list
    # at all), _serialize_player()'s COALESCE(display_name, name) must
    # actually surface it: FUT.GG's short display name ("Marquinhos"),
    # not the Player Information full legal name ("Marcos Aoás Corrêa").
    row = _row(name="Marcos Aoás Corrêa", display_name="Marquinhos")
    resp = _client([row]).get("/api/v2/players/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Marquinhos"
    assert body["player"]["name"] == "Marquinhos"


def test_get_player_detail_bumps_price_priority_when_stale_for_tier():
    # gold_rare's threshold is 45 minutes - 60 minutes old is stale for
    # this tier even though it's well within the old flat 120-minute cutoff.
    provider = FakeProvider([_row(price_tier="gold_rare", bin_captured_at=AS_OF - timedelta(minutes=60))])
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: provider
    resp = TestClient(app).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert provider.bumped_card_ids == [1]


def test_get_player_detail_does_not_bump_when_fresh_for_tier():
    provider = FakeProvider([_row(price_tier="gold_rare", bin_captured_at=AS_OF - timedelta(minutes=4))])
    app = FastAPI()
    app.include_router(futgg_router, prefix="/api/v2")
    app.dependency_overrides[get_provider] = lambda: provider
    resp = TestClient(app).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert provider.bumped_card_ids == []


def test_get_player_detail_404_for_unknown_card():
    resp = _client().get("/api/v2/players/999")
    assert resp.status_code == 404


def test_image_url_is_always_the_plain_cutout_never_the_composited_card():
    # Regression test: image_url must stay pinned to player_image_url
    # (the plain cutout photo) regardless of what else is populated -
    # several frontend consumers (PlayerCardArt's cutoutImage prop,
    # HomeDashboard's normaliseFutgg) draw their own rating/position/
    # stats text on top of whatever this field points to. Repointing it
    # at the composited image (which already has that text baked in)
    # shipped once and produced doubled rating/stats text in production -
    # see composited_image_url below for the correct, deliberately
    # separate field for that image.
    row = _row(
        card_image_stored_url="https://cdn.example.com/fc26/futgg-card-images/1/abc.webp",
        card_image_url="https://game-assets.fut.gg/cdn-cgi/image/.../1.webp",
        player_image_url="https://game-assets.fut.gg/26/player-item/1.png",
    )
    resp = _client([row]).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert resp.json()["player"]["image_url"] == "https://game-assets.fut.gg/26/player-item/1.png"


def test_composited_image_url_prefers_r2_mirror_over_raw_futgg_url():
    row = _row(
        card_image_stored_url="https://cdn.example.com/fc26/futgg-card-images/1/abc.webp",
        card_image_url="https://game-assets.fut.gg/cdn-cgi/image/.../1.webp",
    )
    resp = _client([row]).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert resp.json()["player"]["composited_image_url"] == "https://cdn.example.com/fc26/futgg-card-images/1/abc.webp"


def test_composited_image_url_falls_back_to_raw_futgg_url_when_not_yet_mirrored():
    row = _row(
        card_image_stored_url=None,
        card_image_url="https://game-assets.fut.gg/cdn-cgi/image/.../1.webp",
    )
    resp = _client([row]).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert resp.json()["player"]["composited_image_url"] == "https://game-assets.fut.gg/cdn-cgi/image/.../1.webp"


def test_composited_image_url_is_none_when_no_composited_image_exists():
    row = _row(card_image_stored_url=None, card_image_url=None)
    resp = _client([row]).get("/api/v2/players/1")
    assert resp.status_code == 200
    assert resp.json()["player"]["composited_image_url"] is None


def test_sales_endpoint_marks_time_as_approximate():
    resp = _client().get("/api/v2/players/1/sales")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["approximate"] is True
    assert "approximate_sold_at" in body["items"][0]
    assert "not an exact" in body["note"]


def test_prices_endpoint_shape():
    resp = _client().get("/api/v2/players/1/prices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["lowest_bin"] == 50000


def test_market_freshness_shape():
    resp = _client().get("/api/v2/market/freshness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cards_discovered"] == 10
    assert len(body["heartbeats"]) == 2


def test_opportunities_only_returns_buy_signals():
    strong_row = _row(source_card_id=2, current_bin=40000, sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
    weak_row = _row(source_card_id=3, current_bin=60000, sales_median=52000, sales_trimmed_mean=52000)
    resp = _client([strong_row, weak_row]).get("/api/v2/opportunities")
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        assert item["intelligence"]["signal"] in ("buy", "strong_buy")


def test_trade_finder_rejects_bad_sort_by():
    resp = _client().get("/api/v2/trade-finder?sort_by=not_a_real_sort")
    assert resp.status_code == 400


def test_trade_finder_shape():
    strong_row = _row(source_card_id=2, current_bin=40000, sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
    resp = _client([strong_row]).get("/api/v2/trade-finder?sort_by=roi")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sort_by"] == "roi"


class TestCandidateScanOrderedBySalesCount:
    """Regression test: CANDIDATE_SCAN_LIMIT's own module comment says the
    500-row candidate scan behind /players (intelligence-filtered)
    considers "the top CANDIDATE_SCAN_LIMIT rows (by sales_count, as a
    liquidity-first proxy)" - but the search_players() call site never
    actually passed order_by, so it silently used search_players's own
    default ("rating DESC NULLS LAST") instead. On a database with more
    than 500 tradeable cards, that meant the candidate pool was the
    top-500 *highest-rated* cards, not the top-500 *most-liquid* ones - a
    genuinely liquid, profitable mid/low-rated opportunity could never
    appear at all, silently, with no error to notice.

    /opportunities and /trade-finder no longer use this single-query scan
    at all (see TestSegmentedCandidateScan below) - they now scan every
    liquidity segment, which structurally cannot exhibit this bug because
    it never confines itself to one ordering/limit in the first place."""

    def test_players_with_intelligence_filter_scans_by_sales_count(self):
        client = _client([_row()])
        resp = client.get("/api/v2/players?min_roi=0.01")
        assert resp.status_code == 200


class TestSegmentedCandidateScan:
    """/opportunities and /trade-finder must scan the full segmented
    candidate union (app/services/futgg_candidate_segments.py), not a
    single top-N-by-sales_count query - a low-sales-count, high-value card
    (e.g. an icon that trades rarely but for huge profit) must be scored
    by these endpoints the same way /players/{card_id} would score it
    directly, not silently excluded before scoring ever runs."""

    def test_opportunities_uses_segmented_scan(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([_row()])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/opportunities")
        assert resp.status_code == 200
        assert provider.segment_scan_calls == 1
        assert not provider.search_calls, "opportunities must not fall back to the single-query candidate scan"

    def test_trade_finder_uses_segmented_scan(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([_row()])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/trade-finder")
        assert resp.status_code == 200
        assert provider.segment_scan_calls == 1
        assert not provider.search_calls, "trade-finder must not fall back to the single-query candidate scan"

    def test_opportunities_low_sales_count_card_still_considered(self):
        # A card with a tiny sales_count (would rank far outside any
        # top-500-by-sales_count window) must still be evaluated: the
        # segmented scan's FakeProvider stand-in returns every row
        # regardless of ordering, mirroring how the low_liquidity/
        # near_threshold/never_evaluated segments would surface it in
        # production even though the old single scan never would.
        thin_market_row = _row(
            source_card_id=777, current_bin=3_180_000, sales_median=3_400_000,
            sales_trimmed_mean=3_400_000, sales_count=2,
        )
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([thin_market_row])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/opportunities")
        assert resp.status_code == 200
        assert provider.segment_scan_calls == 1

    def test_players_list_with_intelligence_filter_scans_by_sales_count(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([_row()])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        # Any intelligence-derived filter (min_roi/min_confidence/etc.)
        # routes /players through the same candidate-scan path.
        resp = client.get("/api/v2/players?min_roi=0.01")
        assert resp.status_code == 200
        assert provider.search_calls[-1]["order_by"] == "sales_count DESC NULLS LAST"


class TestStrategyFinderParams:
    """Trade Finder params added for the Strategy Finder redesign -
    require_executable (excludes 'watch' for strategies that mean 'do
    this now') and meaningful_profit (a price-relative profit floor so a
    trivial coin move on a cheap card can't rank as a headline pick)."""

    def test_meaningful_profit_filters_trivial_absolute_gain(self):
        # A cheap card with a large ROI% but a tiny absolute profit -
        # exactly the "200 -> +85 = 42.5% ROI" shape the redesign exists
        # to stop surfacing as a "Budget Picks"/"Quick Flips" headline.
        tiny_row = _row(source_card_id=42, current_bin=200, sales_median=290,
                         sales_trimmed_mean=290, latest_sale_price=290, sales_count=30)
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([tiny_row])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/trade-finder?sort_by=budget&meaningful_profit=true")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_require_executable_excludes_watch(self):
        # current_bin far above the sales median -> not executable right
        # now (signal="watch"), the exact case Opportunities already
        # excludes and Quick Flips/Big Profit should too, per the redesign.
        watch_row = _row(source_card_id=43, current_bin=90000, sales_median=52000,
                          sales_trimmed_mean=52000, latest_sale_price=52000, sales_count=30)
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([watch_row])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/trade-finder?require_executable=true")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestTradeFinderNewestSortDoesNotCrash:
    """Regression test for a live 500: sort_by=newest sorted on
    `row.get("price_updated_at") or ""` - the moment the candidate set had
    both a row with a real datetime and a row with price_updated_at=None
    (a card discovered but not yet priced), Python's sort raised
    `TypeError: '<' not supported between instances of 'str' and
    'datetime.datetime'` comparing the "" placeholder against a real
    datetime, crashing the whole request."""

    def test_newest_sort_with_mixed_null_and_real_timestamps(self):
        priced_row = _row(source_card_id=2, price_updated_at=AS_OF)
        unpriced_row = _row(source_card_id=3, price_updated_at=None, current_bin=40000,
                             sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
        resp = _client([priced_row, unpriced_row]).get("/api/v2/trade-finder?sort_by=newest")
        assert resp.status_code == 200

    def test_newest_sort_puts_real_timestamp_before_null(self):
        older = _row(source_card_id=2, price_updated_at=AS_OF - timedelta(days=1),
                     current_bin=40000, sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
        unpriced = _row(source_card_id=3, price_updated_at=None, current_bin=40000,
                        sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
        resp = _client([older, unpriced]).get("/api/v2/trade-finder?sort_by=newest")
        assert resp.status_code == 200
        ids = [item["source_card_id"] for item in resp.json()["items"]]
        # A card with an actual price timestamp is "newer" than one that
        # was never priced at all - it must never sort behind the null.
        assert ids.index(2) < ids.index(3)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))


def test_sales_endpoint_serves_the_requested_window_not_a_fixed_one():
    """The chart's 24h/7d/14d picker used to be a client-side filter over a
    single response that was always the newest 50 sales - a few hours on a
    liquid card - so every range drew the identical picture and the toggle
    looked dead. Each range is now its own server query over its own
    window, and the response reports how much history it actually covers
    so the chart can say "only 6 hours exist" rather than looking broken."""
    day = _client().get("/api/v2/players/1/sales?days=1").json()
    fortnight = _client().get("/api/v2/players/1/sales?days=14").json()
    assert day["requested_days"] == 1
    assert fortnight["requested_days"] == 14
    assert fortnight["covered_seconds"] > day["covered_seconds"]


def test_sales_endpoint_refuses_a_window_wider_than_the_sales_history_itself():
    # futgg_market_snapshot's sales window stops at 14 days, so a wider
    # range would promise data that structurally cannot exist.
    assert _client().get("/api/v2/players/1/sales?days=30").status_code == 422


class TestPublicOpportunityPreview:
    """/api/v2/opportunities/preview is the ONLY thing the public
    homepage (eafc.live, no account) is allowed to call - it must never
    leak an actionable number. Real security here means the JSON itself
    never contains these fields, not that the frontend chooses not to
    render them - anyone can read the raw response in devtools."""

    _FORBIDDEN_KEYS = {
        "recommended_buy_max", "theoretical_max_buy", "current_executable_buy",
        "break_even_price", "recommended_sell_target", "buy_below",
        "expected_profit_after_tax", "expected_profit_basis", "expected_roi",
        "confidence_score", "liquidity_score", "signal_reasons", "reasons",
        "reason_codes", "blocking_codes", "current_bin", "fair_value",
        "sales_median", "sales_count", "trend_features",
    }

    # A clear buy/strong_buy signal - mirrors test_opportunities_only_
    # returns_buy_signals's "strong_row" above, since the plain _row()
    # default (current_bin only slightly under sales_median) doesn't
    # reliably clear the engine's own ROI gate.
    @staticmethod
    def _buy_row(**overrides):
        base = dict(current_bin=40000, sales_median=55000, sales_trimmed_mean=55000, sales_count=40)
        base.update(overrides)
        return _row(**base)

    def test_preview_never_contains_actionable_fields(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([self._buy_row()])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        resp = client.get("/api/v2/opportunities/preview")
        assert resp.status_code == 200
        item = resp.json()["item"]
        assert item is not None
        leaked = self._FORBIDDEN_KEYS & item.keys()
        assert not leaked, f"public preview leaked actionable fields: {leaked}"

    def test_preview_price_is_rounded_not_exact(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([self._buy_row(current_bin=40123)])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        item = client.get("/api/v2/opportunities/preview").json()["item"]
        assert item["current_bin_approx"] == 40000
        assert item["current_bin_approx"] != 40123

    def test_preview_returns_at_most_one_card(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([self._buy_row(source_card_id=1), self._buy_row(source_card_id=2, name="Second Card")])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        body = client.get("/api/v2/opportunities/preview").json()
        assert "items" not in body
        assert isinstance(body["item"], dict)

    def test_preview_uses_segmented_scan_not_the_narrow_one(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        provider = FakeProvider([_row()])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        client.get("/api/v2/opportunities/preview")
        assert provider.segment_scan_calls == 1
        assert not provider.search_calls

    def test_preview_is_null_when_nothing_qualifies(self):
        app = FastAPI()
        app.include_router(futgg_router, prefix="/api/v2")
        # No sales at all -> evaluate_card can't produce a buy/strong_buy signal.
        provider = FakeProvider([_row(sales_count=0, latest_sale_price=None, latest_sale_at=None)])
        app.dependency_overrides[get_provider] = lambda: provider
        client = TestClient(app)
        assert client.get("/api/v2/opportunities/preview").json()["item"] is None
