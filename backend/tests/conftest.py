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

    futgg_market_module._segmented_candidates_cache = None
    futgg_market_module._segmented_candidates_cached_at = 0.0
    yield
    futgg_market_module._segmented_candidates_cache = None
    futgg_market_module._segmented_candidates_cached_at = 0.0
