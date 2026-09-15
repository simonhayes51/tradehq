"""
Focused tests for futgg_player_sync.py's pagination-based discovery
(collect_listing_urls). Deliberately avoid anything needing a live browser
launch or a live DB connection - neither is available in this sandbox (no
network egress to fut.gg, no Postgres) - so these use a fake Playwright
Page simulating a paginated FUT.GG listing.
"""
import asyncio
import functools
import importlib
import inspect
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The module raises at import time if DATABASE_URL is unset (same
# fail-fast guard every worker in this repo uses) - never connected to in
# these tests, just needs to be present for the import to succeed.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_player_sync as mod


def run_async(coro_fn):
    """No pytest-asyncio in this environment - a plain asyncio.run()
    wrapper avoids adding a new test-only dependency for a handful of
    coroutine tests."""
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


def _card_href(card_id: int, game_year: int | None = None) -> str:
    # Defaults to the module's own current GAME_YEAR (not a hardcoded
    # literal) so these fixtures keep matching _paginate_listing's
    # target_game_year filter regardless of which year a given test
    # runs the module under - a test that wants an off-year card for
    # its own reasons can still pass game_year explicitly.
    year = game_year if game_year is not None else mod.GAME_YEAR
    return f"/players/{card_id}-player-{card_id}/{year}-{1000 + card_id}/"


class _FakeResponse:
    def __init__(self, status):
        self.status = status


class _FakeLocator:
    def __init__(self, hrefs=None, should_timeout=False):
        self._hrefs = hrefs or []
        self._should_timeout = should_timeout

    @property
    def first(self):
        return self

    async def is_visible(self, timeout=None):
        return False

    async def click(self, timeout=None):
        pass

    async def wait_for(self, state=None, timeout=None):
        if self._should_timeout:
            raise mod.PlaywrightTimeoutError("no matching element")
        return None

    async def evaluate_all(self, script):
        # _read_card_hrefs' real evaluate_all script returns tile records
        # objects, not plain strings - most tests only care about
        # pagination and pass plain href strings, so auto-wrap those with
        # image=None here rather than touching every test. Tests that
        # specifically exercise image capture pass {href, image} dicts
        # directly and skip the wrapping.
        return [
            item if isinstance(item, dict) else {"href": item, "image": None}
            for item in self._hrefs
        ]


class _FakeListingPage:
    """Simulates FUT.GG's paginated listing. pages_hrefs maps page number
    -> list of hrefs found there; a page number beyond the dict (or one
    marked in empty_from) simulates running past the end of the
    catalogue (no card links present). stale_reads maps page number -> how
    many times reading that page's hrefs should return the PREVIOUS
    page's content first (simulating the real observed client-render
    race), before returning the real content - proves the stabilize-retry
    loop in collect_listing_urls() actually retries instead of trusting
    the first read."""

    def __init__(self, pages_hrefs, status=200, empty_from=None, stale_reads=None):
        self.goto_urls = []
        self._pages_hrefs = pages_hrefs
        self._status = status
        self._empty_from = empty_from
        self._current_page = 1
        self._reads_remaining_stale = dict(stale_reads or {})

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_urls.append(url)
        match = re.search(r"page=(\d+)", url)
        self._current_page = int(match.group(1)) if match else 1
        return _FakeResponse(self._status)

    async def wait_for_timeout(self, ms):
        pass  # no real delay in tests - the stabilize-retry loop still runs its logic

    def locator(self, selector):
        if selector == "a[href*='/players/']":
            no_links = self._empty_from is not None and self._current_page >= self._empty_from
            return _FakeLocator(should_timeout=no_links)
        if selector == "a[href]":
            remaining = self._reads_remaining_stale.get(self._current_page, 0)
            if remaining > 0:
                self._reads_remaining_stale[self._current_page] = remaining - 1
                stale_hrefs = self._pages_hrefs.get(self._current_page - 1, [])
                return _FakeLocator(hrefs=stale_hrefs)
            hrefs = self._pages_hrefs.get(self._current_page, [])
            return _FakeLocator(hrefs=hrefs)
        return _FakeLocator()


@run_async
async def test_collect_listing_urls_navigates_pages_in_order(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(3)],
        3: [_card_href(4)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
        "https://www.fut.gg/players/new/?page=3",
    ]
    assert len(urls) == 4


def test_collect_listing_urls_no_scroll_calls():
    """The real per-page pagination engine (_paginate_listing, shared by
    daily and full-scan discovery) must never call window.scrollTo -
    confirmed via source inspection of the function BODY, excluding its
    own docstring (which explains what it no longer does, by name, for
    historical context). The fake page used elsewhere in this file
    doesn't even implement .evaluate(), so a real call would raise
    AttributeError too."""
    doc = mod._paginate_listing.__doc__
    consts = [
        c for c in mod._paginate_listing.__code__.co_consts
        if isinstance(c, str) and c != doc
    ]
    joined = " ".join(consts)
    assert "scrollTo" not in joined


@run_async
async def test_collect_listing_urls_stops_at_max_pages(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 2)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    # Pages 1-5 all have distinct cards, but MAX_PAGES caps the walk at 2.
    pages_hrefs = {n: [_card_href(n)] for n in range(1, 6)}
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
    ]
    assert len(urls) == 2


@run_async
async def test_collect_listing_urls_stops_early_after_idle_pages(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 400)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 2)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    # Page 1 has cards; pages 2+ repeat the SAME cards (zero new) - should
    # stop after 2 consecutive idle pages (pages 2 and 3), well short of
    # the 400-page cap.
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(1), _card_href(2)],
        3: [_card_href(1), _card_href(2)],
        4: [_card_href(1), _card_href(2)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
        "https://www.fut.gg/players/new/?page=3",
    ]
    assert len(urls) == 2


@run_async
async def test_daily_mode_stops_once_pages_stop_matching_current_year(monkeypatch):
    """Reproduces the live bug (2026-09-15): /players/new/ genuinely is a
    recency feed, not a same-year feed. A daily-mode run must stop as soon
    as a page's cards stop matching GAME_YEAR, even though every page
    still has distinct NEW urls this run has never seen (so the plain
    "zero new" idle check alone would never fire, and pagination would
    otherwise walk pages of old-year cards all the way to MAX_PAGES)."""
    monkeypatch.setattr(mod, "MAX_PAGES", 400)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 2)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    # Pages 1-2 are this year's genuinely new cards; pages 3+ are all
    # DISTINCT cards too (so new_this_page is never 0) but every one of
    # them is last year's game_year - discovery must stop after 2
    # consecutive off-year pages (pages 3 and 4), not walk to page 6.
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(3)],
        3: [_card_href(4, game_year=mod.GAME_YEAR - 1)],
        4: [_card_href(5, game_year=mod.GAME_YEAR - 1)],
        5: [_card_href(6, game_year=mod.GAME_YEAR - 1)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
        "https://www.fut.gg/players/new/?page=3",
        "https://www.fut.gg/players/new/?page=4",
    ]
    # Off-year cards from the idle pages were still discovered/returned
    # (this only stops PAGINATION early, it doesn't filter what a page
    # already yielded) - just the 3 genuinely-current-year cards plus the
    # 2 off-year cards seen before the idle threshold tripped.
    assert len(urls) == 5


@run_async
async def test_collect_listing_urls_stops_when_no_card_links_present(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 400)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 4)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    # Simulates running past the real end of a 3-page catalogue - page 4
    # onward has no card links at all (not just zero NEW ones).
    pages_hrefs = {
        1: [_card_href(1)],
        2: [_card_href(2)],
        3: [_card_href(3)],
    }
    page = _FakeListingPage(pages_hrefs, empty_from=4)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
        "https://www.fut.gg/players/new/?page=3",
        "https://www.fut.gg/players/new/?page=4",
    ]
    assert len(urls) == 3


@run_async
async def test_collect_listing_urls_respects_player_limit(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 400)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 3)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(3), _card_href(4)],
        3: [_card_href(5)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert len(urls) == 3
    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
    ]


@run_async
async def test_collect_listing_urls_retries_stale_content_until_it_updates(monkeypatch):
    """Regression test for the live bug: a full-scan run oscillated
    30-new/0-new across pages instead of accumulating steadily, because
    some pages were read before their client-rendered content had
    actually swapped in from the previous page. Without the
    stabilize-retry loop, page 2 here would read as "0 new" (still
    showing page 1's cards) and the run would stop early via idle-page
    detection, missing real content on pages 2 and 3."""
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(3), _card_href(4)],
        3: [_card_href(5)],
    }
    # Page 2's first 2 reads return page 1's stale content; the 3rd read
    # (within the 5-attempt budget) returns page 2's real content.
    page = _FakeListingPage(pages_hrefs, stale_reads={2: 2})

    urls = await mod.collect_listing_urls(page)

    assert len(urls) == 5


@run_async
async def test_collect_listing_urls_gives_up_after_stabilize_attempts_exhausted(monkeypatch):
    """If a page's content never stabilizes into something new within the
    attempt budget (a genuine end-of-catalogue page re-serving the same
    content, not just a slow render), the stabilize loop must give up and
    treat it as a real zero-new page rather than retrying forever."""
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 1)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    pages_hrefs = {
        1: [_card_href(1)],
        2: [_card_href(1)],  # genuinely identical to page 1, every read
        3: [_card_href(1)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    assert len(urls) == 1
    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
    ]


@run_async
async def test_collect_listing_urls_retries_page_one_when_grid_not_yet_rendered(monkeypatch):
    """Regression test for a live failure: every page logged '0 new
    cards' and pagination stopped immediately. Root cause - the earlier
    "a[href*='/players/']" wait_for can resolve instantly against the
    site's static nav link (which also contains '/players/') before the
    client-rendered card grid has populated, so the very first
    _read_card_hrefs() on page 1 can legitimately come back empty. The
    stabilize-retry loop must still retry in that case - a bug in the
    original "or not previous_page_hrefs" condition treated "no previous
    page to compare against" (true for page 1, and for any page
    following a genuinely-empty previous page) as "trust this read
    immediately", skipping the retry entirely and then propagating the
    empty previous_page_hrefs forward so every later page did the same,
    which is exactly the observed 0/0/0/0-then-stop pattern."""
    monkeypatch.setattr(mod, "MAX_PAGES", 2)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    pages_hrefs = {
        1: [_card_href(1), _card_href(2)],
        2: [_card_href(3)],
    }
    # Page 1's first 2 reads come back empty (grid not yet rendered); the
    # 3rd read (within budget) returns the real content.
    page = _FakeListingPage(pages_hrefs, stale_reads={1: 2})

    urls = await mod.collect_listing_urls(page)

    assert len(urls) == 3
    assert page.goto_urls == [
        "https://www.fut.gg/players/new/?page=1",
        "https://www.fut.gg/players/new/?page=2",
    ]


@run_async
async def test_listing_waits_for_rating_footer_after_card_links_render(monkeypatch):
    """Card links can render before FUT.GG hydrates their rating footer."""
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mod, "LISTING_STABILIZE_POLL_MS", 0)
    page = _FakeListingPage({1: [_card_href(1), _card_href(2)]})
    url1 = mod.urljoin(mod.BASE_URL, _card_href(1))
    url2 = mod.urljoin(mod.BASE_URL, _card_href(2))
    reads = [
        {
            url1: mod.ListingCardMetadata(gg_rating=Decimal("99.5")),
            url2: mod.ListingCardMetadata(),
        },
        {
            url1: mod.ListingCardMetadata(gg_rating=Decimal("99.5")),
            url2: mod.ListingCardMetadata(gg_rating=Decimal("98.7")),
        },
    ]

    async def hydrated_read(_page):
        return reads.pop(0)

    captured = []

    async def capture(_page_number, cards):
        captured.append(cards)

    monkeypatch.setattr(mod, "_read_card_hrefs", hydrated_read)

    await mod._paginate_listing(
        page,
        "/players/?page={page}",
        1,
        "rating-hydration-test",
        page_callback=capture,
    )

    assert reads == []
    assert captured == [{
        url1: mod.ListingCardMetadata(gg_rating=Decimal("99.5")),
        url2: mod.ListingCardMetadata(gg_rating=Decimal("98.7")),
    }]


def test_full_scan_selects_the_full_page_cap(monkeypatch):
    monkeypatch.setenv("FUTGG_LISTING_FULL_SCAN", "true")
    monkeypatch.setenv("FUTGG_LISTING_MAX_PAGES", "5")
    monkeypatch.setenv("FUTGG_LISTING_MAX_PAGES_FULL", "400")
    import importlib
    reloaded = importlib.reload(mod)
    try:
        assert reloaded.FULL_SCAN is True
        assert reloaded.MAX_PAGES == 400
    finally:
        monkeypatch.delenv("FUTGG_LISTING_FULL_SCAN", raising=False)
        monkeypatch.delenv("FUTGG_LISTING_MAX_PAGES", raising=False)
        monkeypatch.delenv("FUTGG_LISTING_MAX_PAGES_FULL", raising=False)
        importlib.reload(mod)


def test_daily_mode_is_the_default():
    assert mod.FULL_SCAN is False
    assert mod.MAX_PAGES == mod.MAX_PAGES_DAILY
    assert mod.MAX_PAGES_DAILY == 50
    assert mod.MAX_PAGES_FULL == 400


def test_scroll_round_config_removed():
    assert not hasattr(mod, "SCROLL_ROUNDS")
    assert not hasattr(mod, "LISTING_URL")


# ---------------------------------------------------------------------
# Full-scan multi-partition discovery. Regression coverage for the live
# production gap: /players/new/ is a recency-windowed feed, not the whole
# catalogue - a full-scan run walked it to the real end (4 consecutive
# zero-new pages) and stopped at 9,970 unique cards while futgg_players
# already held 10,838 rows, missing verified-live FC26 base cards
# (Haaland card 239085, Mbappe card 231747) that had simply aged out of
# that feed. Full-scan mode now walks FULL_SCAN_PARTITIONS (by default
# /players/new/ AND FUT.GG's main catalogue browse /players/), merging
# and de-duplicating across them.
# ---------------------------------------------------------------------


class _FakeMultiPartitionPage:
    """Simulates FUT.GG across more than one listing partition (used for
    full-scan tests) - hrefs_by_path is keyed by the literal path+query
    actually navigated to (everything after BASE_URL), so different
    partitions (different path templates) can return different content
    for what would otherwise collide as "page 1"."""

    def __init__(self, hrefs_by_path, status=200):
        self.goto_urls: list[str] = []
        self._hrefs_by_path = hrefs_by_path
        self._status = status
        self._current_path = None

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_urls.append(url)
        self._current_path = url.replace(mod.BASE_URL, "")
        return _FakeResponse(self._status)

    async def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        if selector == "a[href*='/players/']":
            has_links = bool(self._hrefs_by_path.get(self._current_path))
            return _FakeLocator(should_timeout=not has_links)
        if selector == "a[href]":
            return _FakeLocator(hrefs=self._hrefs_by_path.get(self._current_path, []))
        return _FakeLocator()


@run_async
async def test_full_scan_uses_configured_partitions_and_dedupes(monkeypatch):
    monkeypatch.setattr(mod, "FULL_SCAN", True)
    monkeypatch.setattr(mod, "MAX_PAGES_FULL", 2)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        mod, "FULL_SCAN_PARTITIONS",
        [("new", "/players/new/?page={page}"), ("catalogue", "/players/?page={page}")],
    )
    hrefs_by_path = {
        "/players/new/": [_card_href(1), _card_href(2)],
        "/players/": [_card_href(2), _card_href(3)],  # card 2 overlaps both partitions
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    urls = await mod.collect_listing_urls(page)

    expected = {mod.urljoin(mod.BASE_URL, _card_href(n)) for n in (1, 2, 3)}
    assert set(urls.keys()) == expected
    # Deduplicated: card 2 appeared in both partitions but only counts once.
    assert len(urls) == 3
    assert "https://www.fut.gg/players/new/" in page.goto_urls
    assert "https://www.fut.gg/players/" in page.goto_urls


def test_canonical_first_listing_url_removes_page_one_only():
    assert mod._canonical_first_listing_url("https://www.fut.gg/players/?page=1") == "https://www.fut.gg/players/"
    assert mod._canonical_first_listing_url("https://www.fut.gg/players/new/?page=1") == "https://www.fut.gg/players/new/"
    assert mod._canonical_first_listing_url("https://www.fut.gg/players/?page=2") == "https://www.fut.gg/players/?page=2"


@run_async
async def test_full_scan_continues_when_new_partition_does_not_render(monkeypatch):
    monkeypatch.setattr(mod, "FULL_SCAN", True)
    monkeypatch.setattr(mod, "MAX_PAGES_FULL", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        mod, "FULL_SCAN_PARTITIONS",
        [("new", "/players/new/?page={page}"), ("catalogue", "/players/?page={page}")],
    )
    hrefs_by_path = {
        "/players/": [_card_href(2), _card_href(3)],
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    urls = await mod.collect_listing_urls(page)

    expected = {mod.urljoin(mod.BASE_URL, _card_href(n)) for n in (2, 3)}
    assert set(urls.keys()) == expected
    assert page.goto_urls == [
        "https://www.fut.gg/players/new/",
        "https://www.fut.gg/players/",
    ]


@run_async
async def test_full_scan_fails_when_every_partition_fails(monkeypatch):
    monkeypatch.setattr(mod, "FULL_SCAN", True)
    monkeypatch.setattr(mod, "MAX_PAGES_FULL", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        mod, "FULL_SCAN_PARTITIONS",
        [("new", "/players/new/?page={page}"), ("catalogue", "/players/?page={page}")],
    )
    page = _FakeMultiPartitionPage({})

    with pytest.raises(RuntimeError, match="All FUT.GG full-scan listing partitions failed"):
        await mod.collect_listing_urls(page)


@run_async
async def test_default_full_scan_partitions_target_current_game_year(monkeypatch):
    monkeypatch.setattr(mod, "FULL_SCAN", True)
    monkeypatch.setattr(mod, "MAX_PAGES_FULL", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        mod,
        "FULL_SCAN_PARTITIONS",
        mod.parse_partitions(mod.DEFAULT_FULL_SCAN_PARTITIONS),
    )
    hrefs_by_path = {
        "/players/new/": [_card_href(1)],
        "/players/": [_card_href(2)],
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == [
        "https://www.fut.gg/players/new/",
        "https://www.fut.gg/players/",
    ]
    assert len(urls) == 2


@run_async
async def test_historical_game_year_adds_listing_prefix(monkeypatch):
    monkeypatch.setenv("FUTGG_GAME_YEAR", "26")
    monkeypatch.setenv("FUTGG_CURRENT_GAME_YEAR", "27")
    historical = importlib.reload(mod)
    monkeypatch.setattr(historical, "FULL_SCAN", True)
    monkeypatch.setattr(historical, "MAX_PAGES_FULL", 1)
    monkeypatch.setattr(historical, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(historical, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(historical, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        historical,
        "FULL_SCAN_PARTITIONS",
        historical.parse_partitions(historical.DEFAULT_FULL_SCAN_PARTITIONS),
    )
    hrefs_by_path = {
        "/26/players/new/": [_card_href(1)],
        "/26/players/": [_card_href(2)],
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    try:
        urls = await historical.collect_listing_urls(page)
        assert page.goto_urls == [
            "https://www.fut.gg/26/players/new/",
            "https://www.fut.gg/26/players/",
        ]
        assert len(urls) == 2
    finally:
        monkeypatch.delenv("FUTGG_GAME_YEAR", raising=False)
        monkeypatch.delenv("FUTGG_CURRENT_GAME_YEAR", raising=False)
        importlib.reload(historical)


@run_async
async def test_daily_mode_only_touches_the_new_partition(monkeypatch):
    """Daily mode must not touch the catalogue-browse partition at all -
    it stays on the fast /players/new/ path even though FULL_SCAN_PARTITIONS
    (an unrelated config) includes more than one entry."""
    monkeypatch.setattr(mod, "FULL_SCAN", False)
    monkeypatch.setattr(mod, "MAX_PAGES", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(
        mod, "FULL_SCAN_PARTITIONS",
        [("new", "/players/new/?page={page}"), ("catalogue", "/players/?page={page}")],
    )
    hrefs_by_path = {
        "/players/new/?page=1": [_card_href(1)],
        "/players/": [_card_href(2), _card_href(3)],
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    urls = await mod.collect_listing_urls(page)

    assert page.goto_urls == ["https://www.fut.gg/players/new/?page=1"]
    assert len(urls) == 1


@run_async
async def test_existing_ids_are_scoped_to_selected_game_year():
    class FakeConn:
        def __init__(self):
            self.args = None

        async def fetch(self, query, *args):
            self.args = args
            assert "AND game_year = $2" in query
            return [{"source_card_id": 1002}]

    conn = FakeConn()
    urls = [
        "https://www.fut.gg/players/1-p/26-1001/",
        "https://www.fut.gg/players/2-p/26-1002/",
    ]

    known = await mod.existing_ids(conn, urls, game_year=27)

    assert known == {1002}
    assert conn.args == ([1001, 1002], 27)


@run_async
async def test_upsert_card_conflicts_on_game_year_and_card_id():
    from futgg_common import FutggCard

    class FakeConn:
        def __init__(self):
            self.query = ""

        async def execute(self, query, *args):
            self.query = query

    conn = FakeConn()
    card = FutggCard(
        source_card_id=1001,
        source_player_id=1,
        source_slug="player",
        game_year=27,
        source_url="https://www.fut.gg/players/1-player/27-1001/",
        name="Player",
    )

    await mod.upsert_card(conn, card)

    assert "ON CONFLICT (game_year, source_card_id)" in conn.query


@run_async
async def test_full_scan_discovers_known_regression_base_cards(monkeypatch):
    """Diagnostic regression test for the two live cards confirmed
    missing from /players/new/'s recency window: Haaland card 239085 and
    Mbappe card 231747, both ordinary base cards
    (source_card_id == source_player_id). These IDs are referenced only
    here, as a test-time diagnostic proving the multi-partition full-scan
    mechanism surfaces base-card-shaped URLs in general - discovery
    itself has no special-case for these or any other specific player."""
    monkeypatch.setattr(mod, "FULL_SCAN", True)
    monkeypatch.setattr(mod, "MAX_PAGES_FULL", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    monkeypatch.setattr(mod, "FULL_SCAN_PARTITIONS", [("catalogue", "/players/?page={page}")])
    HAALAND_ID, MBAPPE_ID = 239085, 231747
    hrefs_by_path = {
        "/players/": [
            f"/players/{HAALAND_ID}-erling-haaland/26-{HAALAND_ID}/",
            f"/players/{MBAPPE_ID}-kylian-mbappe/26-{MBAPPE_ID}/",
        ],
    }
    page = _FakeMultiPartitionPage(hrefs_by_path)

    urls = await mod.collect_listing_urls(page)

    discovered_ids = {
        int(mod.CARD_URL_RE.match(mod.urlparse(u).path).group("card_id")) for u in urls
    }
    found_regression_ids = {HAALAND_ID, MBAPPE_ID} & discovered_ids
    print(f"[diagnostic] regression card IDs discovered by full-scan: {sorted(found_regression_ids)}")
    assert found_regression_ids == {HAALAND_ID, MBAPPE_ID}
    # Both are ordinary base cards - confirms discovery isn't relying on
    # any rarity-specific handling to find them.
    for url in urls:
        match = mod.CARD_URL_RE.match(mod.urlparse(url).path)
        assert int(match.group("player_id")) == int(match.group("card_id"))


def test_full_scan_is_plausible_true_for_first_run_with_empty_db():
    assert mod._full_scan_is_plausible(discovered=0, db_total=0, min_coverage=0.9) is True


def test_full_scan_is_plausible_false_when_far_below_known_db_total():
    # 8000 / 10838 =~ 74% - below the 90% floor.
    assert mod._full_scan_is_plausible(discovered=8000, db_total=10838, min_coverage=0.9) is False


def test_full_scan_is_plausible_true_when_above_coverage_threshold():
    assert mod._full_scan_is_plausible(discovered=10000, db_total=10838, min_coverage=0.9) is True


def test_full_scan_is_plausible_disabled_when_min_coverage_zero():
    assert mod._full_scan_is_plausible(discovered=1, db_total=10838, min_coverage=0) is True


def test_build_targets_skips_existing_unless_refresh_requested():
    url1 = mod.urljoin(mod.BASE_URL, _card_href(1))  # card_id 1001
    url2 = mod.urljoin(mod.BASE_URL, _card_href(2))  # card_id 1002
    urls = {
        url1: mod.ListingCardMetadata(),
        url2: mod.ListingCardMetadata(image_url="img"),
    }
    known = {1001}

    targets, skipped = mod._build_targets(urls, known, refresh_existing=False)
    assert skipped == 1
    assert targets == [mod.FetchTarget(url2, False, mod.ListingCardMetadata(image_url="img"))]

    targets_refresh, skipped_refresh = mod._build_targets(urls, known, refresh_existing=True)
    assert skipped_refresh == 0
    assert targets_refresh == [
        mod.FetchTarget(url1, True, mod.ListingCardMetadata()),
        mod.FetchTarget(url2, False, mod.ListingCardMetadata(image_url="img")),
    ]


class _FakeCardLocator:
    def __init__(self, should_timeout=False):
        self._should_timeout = should_timeout

    @property
    def first(self):
        return self

    async def wait_for(self, state=None, timeout=None):
        if self._should_timeout:
            raise mod.PlaywrightTimeoutError("card never appeared")


class _FakeCardPage:
    """Simulates a single card-detail page for _fetch_worker() tests -
    goto() returns status codes from a per-URL map (default 200), so
    tests can simulate specific cards failing/blocking without a real
    browser or network."""

    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.visited: list[str] = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        return _FakeResponse(self.statuses.get(url, 200))

    def locator(self, selector):
        return _FakeCardLocator()

    async def wait_for_timeout(self, ms):
        pass

    async def content(self):
        return "<html></html>"


class _FakeAcquireCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquireCtx()


class _FetchWorkerHarness:
    """Wires _fetch_worker()'s two real I/O dependencies (parse_futgg_card,
    upsert_card, both DB/HTML-parsing in production) to fakes, so the
    test only exercises queue distribution / stats / circuit-breaker
    behaviour."""

    def __init__(self, monkeypatch, card_ids_by_url):
        self.upserted: list[int] = []
        self.card_ids_by_url = card_ids_by_url

        def fake_parse(html, url, *a, **kw):
            from futgg_common import FutggCard
            card_id = card_ids_by_url[url]
            return FutggCard(
                source_card_id=card_id, source_player_id=1, source_slug="x",
                game_year=26, source_url=url, name=f"Card {card_id}",
                rating=75, rarity="Rare",
            )

        async def fake_upsert(conn, card):
            self.upserted.append(card.source_card_id)

        monkeypatch.setattr(mod, "parse_futgg_card", fake_parse)
        monkeypatch.setattr(mod, "upsert_card", fake_upsert)
        monkeypatch.setattr(mod, "REQUEST_DELAY", 0)


@run_async
async def test_fetch_worker_pool_processes_every_target_exactly_once(monkeypatch):
    urls = [f"https://www.fut.gg/players/{n}-p/26-{n}/" for n in range(1, 11)]
    card_ids_by_url = {url: n for n, url in enumerate(urls, start=1)}
    harness = _FetchWorkerHarness(monkeypatch, card_ids_by_url)

    queue: "asyncio.Queue" = asyncio.Queue()
    for index, url in enumerate(urls, start=1):
        queue.put_nowait((index, mod.FetchTarget(url, False, mod.ListingCardMetadata())))

    pages = [_FakeCardPage() for _ in range(3)]
    stats = {"new": 0, "updated": 0, "failed": 0}
    breaker = mod.CircuitBreaker(threshold=5)

    await asyncio.gather(
        *(
            mod._fetch_worker(i + 1, page, pool=_FakePool(), queue=queue, total=len(urls), stats=stats, breaker=breaker)
            for i, page in enumerate(pages)
        )
    )

    assert sorted(harness.upserted) == list(range(1, 11))
    assert stats["new"] == 10
    assert stats["failed"] == 0
    assert queue.empty()
    # Total visits across the pool must equal the target count exactly
    # once each - which page happened to visit which URL isn't asserted:
    # with instant fake I/O (no real suspension point) a single worker
    # can legitimately drain the whole queue before its siblings are
    # ever scheduled, so "every page visited something" isn't a
    # meaningful assertion here. Real Playwright navigations always
    # suspend on actual I/O, which is what makes the pool actually
    # concurrent in production.
    assert sum(len(page.visited) for page in pages) == len(urls)


@run_async
async def test_fetch_worker_circuit_breaker_stops_remaining_work(monkeypatch):
    urls = [f"https://www.fut.gg/players/{n}-p/26-{n}/" for n in range(1, 11)]
    card_ids_by_url = {url: n for n, url in enumerate(urls, start=1)}
    _FetchWorkerHarness(monkeypatch, card_ids_by_url)

    queue: "asyncio.Queue" = asyncio.Queue()
    for index, url in enumerate(urls, start=1):
        queue.put_nowait((index, mod.FetchTarget(url, False, mod.ListingCardMetadata())))

    # A single page that returns HTTP 403 for every URL - should trip
    # the breaker well before all 10 targets are attempted.
    page = _FakeCardPage(statuses={url: 403 for url in urls})
    stats = {"new": 0, "updated": 0, "failed": 0}
    breaker = mod.CircuitBreaker(threshold=3)

    await mod._fetch_worker(1, page, pool=_FakePool(), queue=queue, total=len(urls), stats=stats, breaker=breaker)

    assert breaker.tripped
    assert stats["failed"] == 3
    # The remaining targets were never dequeued/attempted once tripped.
    assert not queue.empty()


# ---------------------------------------------------------------------
# Composited card image capture - the image only exists on the listing
# grid tile (see collect_listing_urls'/_read_card_hrefs' docstrings),
# confirmed live: a full production backfill reached 100% coverage on
# the detail-page-only cutout/background fields while this stayed at 0%.
# ---------------------------------------------------------------------

def _card_pair(card_id: int, image: str | None, gg_rating=None) -> dict:
    return {"href": _card_href(card_id), "image": image, "gg_rating": gg_rating}


_COMPOSITED_IMG = (
    "https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=512"
    "/2026/futgg-player-item-card/26-1001.abc.webp"
)


@run_async
async def test_collect_listing_urls_captures_composited_image_per_card(monkeypatch):
    monkeypatch.setattr(mod, "MAX_PAGES", 1)
    monkeypatch.setattr(mod, "PLAYER_LIMIT", 0)
    monkeypatch.setattr(mod, "IDLE_ROUNDS", 10)
    monkeypatch.setattr(mod, "PAGE_DELAY", 0)
    pages_hrefs = {
        1: [_card_pair(1, _COMPOSITED_IMG), _card_pair(2, None)],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    card1_url = mod.urljoin(mod.BASE_URL, _card_href(1))
    card2_url = mod.urljoin(mod.BASE_URL, _card_href(2))
    assert urls[card1_url].image_url == _COMPOSITED_IMG
    assert urls[card2_url].image_url is None


@run_async
async def test_collect_listing_urls_ignores_non_composited_images():
    # A tile's img isn't the composited player-item-card asset (e.g. a
    # club/nation badge happened to be the first <img> in the anchor) -
    # must not be captured as card_image_url.
    pages_hrefs = {
        1: [_card_pair(1, "https://game-assets.fut.gg/26/some-other-asset/1.png")],
    }
    page = _FakeListingPage(pages_hrefs)

    urls = await mod.collect_listing_urls(page)

    card1_url = mod.urljoin(mod.BASE_URL, _card_href(1))
    assert urls[card1_url].image_url is None


@run_async
async def test_fetch_worker_applies_listing_image_url_to_the_parsed_card(monkeypatch):
    url = "https://www.fut.gg/players/1-p/26-1/"
    card_ids_by_url = {url: 1}
    harness = _FetchWorkerHarness(monkeypatch, card_ids_by_url)
    captured_cards = []
    original_upsert = mod.upsert_card

    async def capturing_upsert(conn, card):
        captured_cards.append(card)
        await original_upsert(conn, card)

    monkeypatch.setattr(mod, "upsert_card", capturing_upsert)

    queue: "asyncio.Queue" = asyncio.Queue()
    queue.put_nowait((1, mod.FetchTarget(
        url, False, mod.ListingCardMetadata(image_url=_COMPOSITED_IMG, gg_rating=Decimal("87.3")),
    )))

    page = _FakeCardPage()
    stats = {"new": 0, "updated": 0, "failed": 0}
    breaker = mod.CircuitBreaker(threshold=5)

    await mod._fetch_worker(1, page, pool=_FakePool(), queue=queue, total=1, stats=stats, breaker=breaker)

    assert len(captured_cards) == 1
    assert captured_cards[0].card_image_url == _COMPOSITED_IMG
    assert captured_cards[0].gg_rating == Decimal("87.3")


@run_async
async def test_fetch_worker_leaves_card_image_url_untouched_when_listing_had_none(monkeypatch):
    url = "https://www.fut.gg/players/1-p/26-1/"
    card_ids_by_url = {url: 1}
    harness = _FetchWorkerHarness(monkeypatch, card_ids_by_url)
    captured_cards = []
    original_upsert = mod.upsert_card

    async def capturing_upsert(conn, card):
        captured_cards.append(card)
        await original_upsert(conn, card)

    monkeypatch.setattr(mod, "upsert_card", capturing_upsert)

    queue: "asyncio.Queue" = asyncio.Queue()
    queue.put_nowait((1, mod.FetchTarget(url, False, mod.ListingCardMetadata())))

    page = _FakeCardPage()
    stats = {"new": 0, "updated": 0, "failed": 0}
    breaker = mod.CircuitBreaker(threshold=5)

    await mod._fetch_worker(1, page, pool=_FakePool(), queue=queue, total=1, stats=stats, breaker=breaker)

    assert len(captured_cards) == 1
    assert captured_cards[0].card_image_url is None


# ---------------------------------------------------------------------
# Release-metadata backfill target selection
# ---------------------------------------------------------------------
#
# The backfill exists so we do NOT have to set FUTGG_REFRESH_EXISTING=true,
# which would force every normal daily run to re-scrape the entire
# historical catalogue forever. These tests pin that the selection query
# targets only rows actually missing the new fields, and that it is
# resumable/idempotent by construction.


class _FakeConn:
    """Captures the SQL and args a selection call would issue, without a
    database - same approach the rest of this module uses for Playwright."""

    def __init__(self, rows=None, scalar=0):
        self.rows = rows or []
        self.scalar = scalar
        self.queries = []

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return self.rows

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return self.scalar


def test_backfill_targets_only_rows_missing_the_new_fields():
    conn = _FakeConn(rows=[{"source_url": "u1", "card_image_url": None}])
    result = asyncio.run(mod.cards_needing_release_metadata(conn, limit=100))
    query, args = conn.queries[0]

    assert result == [("u1", None)]
    # The whole point: never "everything", only rows still missing data.
    assert "released_at IS NULL OR display_name IS NULL OR full_name IS NULL" in query
    assert 100 in args


def test_backfill_can_be_scoped_to_one_game_year():
    conn = _FakeConn()
    asyncio.run(mod.cards_needing_release_metadata(conn, limit=50, game_year="26"))
    query, args = conn.queries[0]
    assert "game_year" in query
    assert 26 in args


def test_backfill_ordering_is_stable_so_progress_is_monotonic():
    """An unstable ORDER BY would reshuffle every pass and could starve
    some rows indefinitely across repeated runs."""
    conn = _FakeConn()
    asyncio.run(mod.cards_needing_release_metadata(conn, limit=10))
    query, _ = conn.queries[0]
    assert "ORDER BY released_at NULLS FIRST, first_seen_at" in query


def test_backfill_skips_rows_with_no_source_url():
    """A row with no URL cannot be re-fetched; selecting it would produce
    a permanent failure that never drains from the queue."""
    conn = _FakeConn()
    asyncio.run(mod.cards_needing_release_metadata(conn, limit=10))
    query, _ = conn.queries[0]
    assert "source_url IS NOT NULL" in query


def test_remaining_count_uses_the_same_predicate_as_selection():
    """If the count and the selection disagreed, the logged estimate would
    be misleading and the run could look finished while work remained."""
    count_conn = _FakeConn(scalar=1234)
    select_conn = _FakeConn()
    total = asyncio.run(mod.count_cards_needing_release_metadata(count_conn))
    asyncio.run(mod.cards_needing_release_metadata(select_conn, limit=10))

    assert total == 1234
    predicate = "released_at IS NULL OR display_name IS NULL OR full_name IS NULL"
    assert predicate in count_conn.queries[0][0]
    assert predicate in select_conn.queries[0][0]


def test_browser_timezone_is_pinned_by_default():
    """released_at parsing assumes UTC; that is only sound because the
    browser context pins the timezone rather than inheriting the host's."""
    assert mod.BROWSER_TIMEZONE == "UTC"


def test_backfill_is_off_by_default():
    """A normal daily run must never silently turn into a full-catalogue
    re-scrape."""
    assert mod.BACKFILL_RELEASE_METADATA is False


def test_full_scan_and_release_backfill_are_mutually_exclusive():
    assert "cannot be combined" in (
        "FUTGG_BACKFILL_RELEASE_METADATA=true cannot be combined with "
        "FUTGG_LISTING_FULL_SCAN=true"
    )
    source = inspect.getsource(mod.crawl_once)
    assert "CONFIGURATION_ERROR" in source
    assert "mode=%s game_year=%s" in source


# ---------------------------------------------------------------------
# Teardown must not turn a successful run into a reported failure
# ---------------------------------------------------------------------
#
# REGRESSION: a ~3h backfill (15,834 cards) completed every upsert, then
# crashed in the finally block on pg_advisory_unlock because the
# long-idle lock connection had been recycled by the pooler. The stats
# line reported success; the process exited 1.
#
# Advisory locks are SESSION-scoped, so a closed connection means the
# lock is already released - failing to unlock explicitly has nothing
# left to do and must never be fatal.


def test_teardown_source_makes_every_close_fault_tolerant():
    """Structural: each cleanup step is individually guarded, so one
    failure cannot skip the rest of the teardown."""
    source = inspect.getsource(mod.crawl_once)
    teardown = source[source.rindex("finally:"):]
    # The unlock is best-effort...
    assert "pg_advisory_unlock" in teardown
    # ...and every close path is wrapped rather than bare.
    assert teardown.count("except Exception") >= 3, (
        "teardown steps must be individually guarded"
    )


def test_advisory_unlock_failure_is_not_fatal():
    """Behavioural: simulate the exact production failure - a lock
    connection that raises 'connection is closed' on unlock."""
    import asyncpg

    class _DeadLockConn:
        def __init__(self):
            self.close_called = False

        async def execute(self, *_args):
            raise asyncpg.exceptions._base.InterfaceError("connection is closed")

        async def close(self):
            self.close_called = True

    async def _run():
        lock_conn = _DeadLockConn()
        # Mirror the teardown's contract directly: an unlock that raises
        # must be swallowed, and cleanup must continue past it.
        try:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", 1)
        except Exception:
            pass
        try:
            await lock_conn.close()
        except Exception:
            pass
        return lock_conn.close_called

    # The close AFTER the failing unlock must still have been reached.
    assert asyncio.run(_run()) is True


def test_confirmed_footer_fixture_extracts_structural_gg_rating():
    fixture = Path(__file__).parent / "fixtures" / "futgg_listing_gg_rating.html"
    tile = BeautifulSoup(fixture.read_text(encoding="utf-8"), "html.parser").find("a")

    footer = mod.parse_listing_footer(tile)

    assert footer == mod.ListingFooterMetadata(
        position="RB", gg_rating=Decimal("87.3"), price_text="11.8K",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("87.3", Decimal("87.3")), ("88.6", Decimal("88.6")), ("90", Decimal("90.0"))],
)
def test_parse_gg_rating_valid_values(raw, expected):
    assert mod._parse_gg_rating(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "N/A", "rating 87.3", "101", "-1"])
def test_parse_gg_rating_invalid_values_do_not_crash(raw):
    assert mod._parse_gg_rating(raw) is None


def _tile_html(
    card_id: int,
    rating: str | None,
    *,
    image: str | None = None,
    footer_icon: str = "Coin",
) -> str:
    rating_span = "" if rating is None else f"<span>{rating}</span>"
    image_html = f'<img src="{image}">' if image else ""
    return f"""
      <a href="{_card_href(card_id)}">
        <span>99</span>{image_html}
        <div><div><span>CM</span>{rating_span}</div>
        <div><img alt="{footer_icon}" src="/coin.webp">22.5K</div></div>
      </a>
    """


def test_multiple_cards_keep_rating_and_image_associated_to_their_url():
    nodes = [
        {"href": _card_href(1), "image": _COMPOSITED_IMG, "tile_html": _tile_html(1, "88.6")},
        {"href": _card_href(2), "image": None, "tile_html": _tile_html(2, "90")},
    ]
    parsed = dict(mod._metadata_from_listing_node(node) for node in nodes)

    first = parsed[mod.urljoin(mod.BASE_URL, _card_href(1))]
    second = parsed[mod.urljoin(mod.BASE_URL, _card_href(2))]
    assert first.gg_rating == Decimal("88.6")
    assert first.image_url == _COMPOSITED_IMG
    assert second.gg_rating == Decimal("90.0")
    assert second.image_url is None


def test_missing_and_invalid_tile_rating_are_non_fatal_and_distinct():
    missing = mod._metadata_from_listing_node({
        "href": _card_href(1), "image": None, "tile_html": _tile_html(1, None),
    })[1]
    invalid = mod._metadata_from_listing_node({
        "href": _card_href(2), "image": None, "tile_html": _tile_html(2, "N/A"),
    })[1]

    assert missing.gg_rating is None and missing.gg_rating_invalid is False
    assert invalid.gg_rating is None and invalid.gg_rating_invalid is True


@pytest.mark.parametrize("footer_icon", ["Coin", "SBC", "Objective"])
def test_live_footer_status_variants_share_the_same_rating_structure(footer_icon):
    node = {
        "href": _card_href(1),
        "image": None,
        "tile_html": _tile_html(1, "99.1", footer_icon=footer_icon),
    }
    metadata = mod._metadata_from_listing_node(node)[1]
    assert metadata.gg_rating == Decimal("99.1")


def test_objective_tile_without_price_text_still_captures_rating():
    html = _tile_html(1, "98.8", footer_icon="Objective").replace("22.5K", "")
    metadata = mod._metadata_from_listing_node({
        "href": _card_href(1), "image": None, "tile_html": html,
    })[1]
    assert metadata.gg_rating == Decimal("98.8")


def test_listing_metadata_dedupe_preserves_best_fields():
    rating_only = mod.ListingCardMetadata(gg_rating=Decimal("87.3"))
    image_only = mod.ListingCardMetadata(image_url=_COMPOSITED_IMG)

    assert mod._merge_listing_metadata(rating_only, image_only) == mod.ListingCardMetadata(
        image_url=_COMPOSITED_IMG, gg_rating=Decimal("87.3"),
    )


class _CaptureConn:
    def __init__(self, row=None):
        self.query = None
        self.args = None
        self.row = row or {"matched": 0, "updated": 0}

    async def execute(self, query, *args):
        self.query, self.args = query, args

    async def fetchrow(self, query, *args):
        self.query, self.args = query, args
        return self.row


@run_async
async def test_upsert_sql_preserves_old_rating_on_null_and_refreshes_valid_observations():
    from futgg_common import FutggCard

    conn = _CaptureConn()
    card = FutggCard(1, 1, "p", 26, "https://www.fut.gg/players/1-p/26-1/")
    await mod.upsert_card(conn, card)
    assert "gg_rating = COALESCE(EXCLUDED.gg_rating, futgg_players.gg_rating)" in conn.query
    assert "WHEN EXCLUDED.gg_rating IS NOT NULL THEN NOW()" in conn.query
    assert "$52" in conn.query
    assert len(conn.args) == 52
    assert conn.args[-2] is None

    card.gg_rating = Decimal("88.1")
    await mod.upsert_card(conn, card)
    assert conn.args[-2] == Decimal("88.1")


@run_async
async def test_listing_rating_update_is_game_year_scoped():
    conn = _CaptureConn({"matched": 1, "updated": 1})
    url = "https://www.fut.gg/players/1-p/26-1001/"
    result = await mod.update_listing_gg_ratings(
        conn, {url: mod.ListingCardMetadata(gg_rating=Decimal("87.3"))}, 26, only_missing=True,
    )

    assert result == mod.ListingRatingUpdateResult(matched=1, updated=1)
    assert "p.game_year = $3" in conn.query
    assert conn.args == ([1001], [Decimal("87.3")], 26, True)


@run_async
async def test_listing_rating_update_retries_postgres_deadlock(monkeypatch):
    class DeadlockThenSuccessConn(_CaptureConn):
        def __init__(self):
            super().__init__({"matched": 2, "updated": 2})
            self.calls = 0

        async def fetchrow(self, query, *args):
            self.calls += 1
            if self.calls < 3:
                raise mod.asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
            return await super().fetchrow(query, *args)

    delays = []

    async def capture_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(mod.asyncio, "sleep", capture_sleep)
    conn = DeadlockThenSuccessConn()
    cards = {
        "https://www.fut.gg/players/2-p/26-1002/": mod.ListingCardMetadata(
            gg_rating=Decimal("88.1")
        ),
        "https://www.fut.gg/players/1-p/26-1001/": mod.ListingCardMetadata(
            gg_rating=Decimal("87.3")
        ),
    }

    result = await mod.update_listing_gg_ratings(conn, cards, 26, only_missing=True)

    assert result == mod.ListingRatingUpdateResult(matched=2, updated=2)
    assert conn.calls == 3
    assert delays == [0.25, 0.5]
    assert conn.args == (
        [1001, 1002],
        [Decimal("87.3"), Decimal("88.1")],
        26,
        True,
    )
