# tests/test_public_access.py
#
# What someone with NO account may see.
#
# Phase 2 gave signed-out visitors the complete live price and fair-value
# analysis, because they resolve to tier "free" and FEATURE_MIN_TIER only
# ever asked about tier. That made the headline reason to create a Free
# account something you already had without one - a free tier that is not
# worth creating.
#
# The rule this file pins: SEARCH and IDENTITY are public (discovery is
# what makes a shared link worth opening); ANALYSIS is not.
from __future__ import annotations

import pytest

from app.auth.access import (
    public_card_preview,
    public_companion_preview,
    require_account,
    serialise_card_detail,
    serialise_player_search,
)
from app.auth.entitlements import (
    ACCOUNT_ONLY_FEATURES,
    FEATURE_MIN_TIER,
    Viewer,
    features_for_tier,
)

# Everything that IS the analysis. None of it may reach a signed-out
# response from any endpoint.
ANALYSIS_FIELDS = {
    "current_bin", "fair_value", "sales_median", "sales_low", "sales_high",
    "sales_trimmed_mean", "sales_stddev", "latest_sale_price", "signal",
    "status", "confidence_score", "confidence", "risk_level", "liquidity_score",
    "trend_description", "trend_state", "recommended_buy_max", "buy_below",
    "recommended_sell_target", "expected_profit_after_tax", "expected_roi",
    "signal_reasons", "discount_pct", "verdict",
}


def viewer(tier="free", *, user_id="u1"):
    return Viewer(
        user_id=user_id, tier=tier, is_admin=False,
        features=frozenset(features_for_tier(tier, authenticated=user_id is not None)),
    )


ANON = viewer(user_id=None)
FREE = viewer()
ELITE = viewer("elite")


def card(card_id=111, name="Kylian Mbappe"):
    return {
        "source_card_id": card_id, "card_id": card_id, "name": name,
        "display_name": name, "rating": 91, "rarity": "team_of_the_week",
        "primary_position": "ST", "club": "Real Madrid", "league": "LaLiga",
        "nation": "France", "player_image_url": "https://example.com/a.png",
        "current_bin": 420431, "fair_value": 505000, "sales_median": 498000,
        "sales_low": 470000, "sales_high": 530000, "latest_sale_price": 501000,
        "signal": "buy", "confidence_score": 0.78, "risk_level": "low",
        "liquidity_score": 0.7, "trend_description": "Rising steadily",
        "recommended_buy_max": 410000, "buy_below": 410000,
        "recommended_sell_target": 520000, "expected_profit_after_tax": 84000,
        "expected_roi": 0.2, "signal_reasons": ["Trading below recent median"],
        "price_age_seconds": 300,
    }


class TestTheFeatureSetItself:
    def test_a_signed_out_visitor_does_not_get_fair_value(self):
        assert "fair_value" not in features_for_tier("free", authenticated=False)

    def test_a_free_member_does(self):
        # This is the whole point of the account.
        assert "fair_value" in features_for_tier("free", authenticated=True)

    def test_elite_still_gets_everything_free_gets(self):
        assert features_for_tier("free") <= features_for_tier("elite")

    def test_account_only_features_are_all_real_free_features(self):
        # A name in ACCOUNT_ONLY_FEATURES that is not a free feature would
        # silently do nothing.
        for feature in ACCOUNT_ONLY_FEATURES:
            assert FEATURE_MIN_TIER.get(feature) == "free", feature

    def test_the_write_scoped_features_need_an_account(self):
        anon = features_for_tier("free", authenticated=False)
        for feature in ("watchlist", "trade_journal"):
            assert feature not in anon

    def test_search_is_not_account_only(self):
        # Search must stay public: it is discovery, it is shareable, and
        # making it account-only would remove the reason a link is worth
        # opening at all.
        assert "fair_value" in ACCOUNT_ONLY_FEATURES
        anon = features_for_tier("free", authenticated=False)
        assert anon, "a signed-out visitor must still have SOME features"


class TestThePublicCardPreview:
    def test_no_analysis_field_survives(self):
        preview = public_card_preview(card())
        leaked = ANALYSIS_FIELDS & set(preview)
        assert not leaked, f"analysis reached a signed-out client: {sorted(leaked)}"

    def test_identity_does_survive(self):
        preview = public_card_preview(card())
        assert preview["name"] == "Kylian Mbappe"
        assert preview["rating"] == 91
        assert preview["rarity"] == "team_of_the_week"
        assert preview["club"] == "Real Madrid"
        assert preview["player_image_url"]

    def test_the_price_is_present_but_approximate(self):
        preview = public_card_preview(card())
        assert preview["price_is_approximate"] is True
        assert preview["current_bin_approx"] == 425_000
        assert preview["current_bin_approx"] != 420_431

    @pytest.mark.parametrize("price,expected", [
        (3_240, 3_000),          # cheap cards keep useful resolution
        (9_900, 10_000),
        (42_300, 40_000),
        (420_431, 425_000),
        (1_090_000, 1_100_000),  # and expensive ones round coarsely
    ])
    def test_the_approximation_scales_with_the_price(self, price, expected):
        # A flat rounding is wrong at both ends: round a 3,200-coin card to
        # the nearest 1,000 and you lose a third of its value; round a
        # 900,000-coin card to the nearest 1,000 and you have not
        # approximated anything.
        assert public_card_preview({"current_bin": price})["current_bin_approx"] == expected

    def test_a_card_with_no_price_does_not_invent_one(self):
        assert public_card_preview({"current_bin": None})["current_bin_approx"] is None
        assert public_card_preview({})["current_bin_approx"] is None

    def test_freshness_survives_because_it_reveals_nothing(self):
        # "Updated 5 minutes ago" proves the product is live without
        # saying what the number is.
        assert public_card_preview(card())["price_age_seconds"] == 300

    def test_a_new_analysis_field_cannot_leak_by_default(self):
        item = card()
        item["some_new_valuation_field"] = 12345
        assert "some_new_valuation_field" not in public_card_preview(item)

    def test_the_response_says_an_account_is_needed(self):
        assert public_card_preview(card())["requires_account"] is True


class TestCardDetailByViewer:
    def test_signed_out_gets_the_public_preview(self):
        out = serialise_card_detail(card(), ANON)
        assert not (ANALYSIS_FIELDS & set(out))
        assert out["requires_account"] is True

    def test_a_free_member_gets_the_real_market_data(self):
        out = serialise_card_detail(card(), FREE)
        assert out["current_bin"] == 420431
        assert out["fair_value"] == 505000
        assert out["signal"] == "buy"

    def test_a_free_member_still_does_not_get_the_executable_plan(self):
        out = serialise_card_detail(card(), FREE)
        for withheld in ("recommended_buy_max", "recommended_sell_target",
                         "expected_profit_after_tax", "buy_below"):
            assert withheld not in out
        assert out["trade_plan_locked"] is True

    def test_a_free_member_gets_the_plan_on_one_of_todays_picks(self):
        out = serialise_card_detail(card(111), FREE, unlocked_ids=[111])
        assert out["recommended_sell_target"] == 520000

    def test_a_daily_pick_does_not_unlock_a_signed_out_visitor(self):
        # There is no account for a daily allowance to belong to.
        out = serialise_card_detail(card(111), ANON, unlocked_ids=[111])
        assert not (ANALYSIS_FIELDS & set(out))

    def test_elite_is_untouched(self):
        item = card()
        assert serialise_card_detail(item, ELITE) == item


class TestSearchResults:
    def test_signed_out_search_returns_identity_only(self):
        items = serialise_player_search([card(1), card(2, "Erling Haaland")], ANON)
        assert len(items) == 2
        for item in items:
            assert not (ANALYSIS_FIELDS & set(item))
        assert items[1]["name"] == "Erling Haaland"

    def test_search_still_works_signed_out(self):
        # The point of keeping search public: a signed-out visitor must
        # still get results, not an error.
        assert serialise_player_search([card()], ANON)

    def test_a_free_member_sees_real_prices_in_search(self):
        item = serialise_player_search([card()], FREE)[0]
        assert item["current_bin"] == 420431
        assert "recommended_sell_target" not in item

    def test_elite_search_is_untouched(self):
        items = [card()]
        assert serialise_player_search(items, ELITE) == items


class TestTheCompanionOverlay:
    def _payload(self):
        return {
            "source_card_id": 111, "name": "Kylian Mbappe", "rating": 91,
            "rarity": "Team of the Year", "image_url": "https://example.com/a.png",
            "reference_bin": 1_090_000, "fair_value": 1_200_000,
            "sales_median": 1_260_000, "signal": "buy", "status": "BUY",
            "confidence": 0.78, "liquidity_score": 0.7, "trend_state": "rising",
            "risk": {"overall": "low"}, "engine_version": "v2",
        }

    def test_no_valuation_reaches_a_signed_out_overlay(self):
        out = public_companion_preview(self._payload())
        for withheld in ("reference_bin", "fair_value", "sales_median", "signal",
                         "status", "confidence", "liquidity_score", "trend_state", "risk"):
            assert withheld not in out

    def test_it_stays_an_identification_aid(self):
        out = public_companion_preview(self._payload())
        assert out["name"] == "Kylian Mbappe"
        assert out["rating"] == 91
        assert out["reference_bin_approx"] == 1_100_000
        assert out["locked"] is True


class TestTheAccountGate:
    def test_a_signed_out_caller_is_refused(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            require_account(ANON, "the sales history")
        # 401, not 402: what is missing is an account, and one is free.
        assert exc.value.status_code == 401
        assert exc.value.detail["error"] == "account_required"
        assert "No card needed" in exc.value.detail["message"]

    def test_a_free_member_passes(self):
        require_account(FREE, "the sales history")

    def test_an_elite_member_passes(self):
        require_account(ELITE, "the sales history")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
#
# Auditing the endpoints one at a time is how the /companion bypass
# survived a whole phase: each surface looked fine on its own. These tests
# walk the routes instead, so a NEW endpoint that returns card analysis
# has to be named here before it can ship.

import inspect  # noqa: E402


def _routes_of(module_names):
    import importlib

    found = []
    for name in module_names:
        module = importlib.import_module(name)
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            found.append((name, getattr(route, "path", "?"), endpoint))
    return found


# Every route that can return per-card market analysis must either take a
# viewer (so it can serialise per state) or be listed here with a reason.
CARD_ANALYSIS_ROUTERS = [
    "app.routers.v2.futgg_market",
    "app.routers.v2.companion",
    "app.routers.players",
]

# Public on purpose. Each entry is a claim that the route returns identity
# or discovery data only - not a valuation.
PUBLIC_BY_DESIGN = {
    ("app.routers.v2.futgg_market", "/opportunities/preview"),  # already redacted server-side
    ("app.routers.v2.futgg_market", "/market/freshness"),       # counts, no card
    ("app.routers.v2.futgg_market", "/market/priority-metrics"),
    ("app.routers.v2.futgg_market", "/market/track-record"),
    ("app.routers.v2.futgg_market", "/market/evaluation-coverage"),
    ("app.routers.v2.futgg_market", "/trade-finder/strategies"),  # strategy names only
    # These carry the router's own /api/players prefix.
    ("app.routers.players", "/api/players/resolve"),          # name -> card id
    ("app.routers.players", "/api/players/search"),           # identity
    ("app.routers.players", "/api/players/autocomplete"),     # identity
    ("app.routers.players", "/api/players/batch/meta"),       # identity
    ("app.routers.players", "/api/players/{card_id}"),        # identity
    ("app.routers.players", "/api/players/{card_id}/meta"),   # identity
}


def test_every_card_analysis_route_knows_who_is_asking():
    unguarded = []
    for module, path, endpoint in _routes_of(CARD_ANALYSIS_ROUTERS):
        if (module, path) in PUBLIC_BY_DESIGN:
            continue
        params = inspect.signature(endpoint).parameters
        source = inspect.getsource(endpoint)
        knows = (
            "viewer" in params
            or "require_account" in source
            or "require_feature" in source
            or "require_trade_finder" in source
            or "require_full_track_record" in source
            or "compute_entitlements" in source
            or any(getattr(d, "dependency", None) is not None
                   for d in getattr(endpoint, "__dependencies__", []) or [])
        )
        if not knows:
            unguarded.append(f"{module}{path}")
    assert not unguarded, (
        "these routes can return card analysis to anyone and never ask who is "
        f"calling: {unguarded}. Add a viewer and serialise per state, or add "
        "the route to PUBLIC_BY_DESIGN with a reason."
    )


def test_the_public_by_design_list_has_not_gone_stale():
    # A path listed as public that no longer exists is a claim nobody is
    # checking any more.
    live = {(m, p) for m, p, _ in _routes_of(CARD_ANALYSIS_ROUTERS)}
    stale = PUBLIC_BY_DESIGN - live
    assert not stale, f"PUBLIC_BY_DESIGN names routes that no longer exist: {sorted(stale)}"
