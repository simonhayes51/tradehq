# tests/conftest.py
#
# Shared fixtures across the whole test suite.
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_segmented_candidates_cache():
    # _segmented_candidates() (app/routers/v2/futgg_market.py) caches its
    # result for _SEGMENTED_CANDIDATES_CACHE_SECONDS to collapse the
    # redundant simultaneous scans a single page load fires in production
    # (see that cache's own docstring). In tests this means a cache
    # warmed by one test's FakeProvider would otherwise leak into a later
    # test using a different one - several test modules
    # (test_futgg_market_router.py, test_strategy_finder.py, ...) hit this
    # router. Reset around every test so each starts with a guaranteed
    # cache miss, regardless of which module it lives in.
    import app.routers.v2.futgg_market as futgg_market_module

    def _reset():
        futgg_market_module._segmented_candidates_cache = None
        futgg_market_module._segmented_candidates_cached_at = 0.0
        # _cached_evaluate_card()'s memoization (same production reasoning,
        # keyed by card_id) - different tests reuse the same small set of
        # card_ids (1, 2, 3, ...) with different evaluate_card() behaviour,
        # so a result cached by one test would otherwise leak into another.
        futgg_market_module._evaluate_card_cache.clear()
        # _sales_for_rows()/_bin_observations_for_rows()'s own short-lived
        # caches - same leak risk across tests as the two above.
        futgg_market_module._sales_by_card_cache = None
        futgg_market_module._sales_by_card_cache_ids = frozenset()
        futgg_market_module._sales_by_card_cached_at = 0.0
        futgg_market_module._bin_by_card_cache = None
        futgg_market_module._bin_by_card_cache_ids = frozenset()
        futgg_market_module._bin_by_card_cached_at = 0.0

    _reset()
    yield
    _reset()
