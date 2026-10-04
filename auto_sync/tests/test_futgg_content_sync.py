import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
AUTO_SYNC = Path(__file__).resolve().parents[1]
if str(AUTO_SYNC) not in sys.path:
    sys.path.insert(0, str(AUTO_SYNC))

sync = importlib.import_module("futgg_content_sync")


def test_discovers_unique_fc27_sbc_links():
    page = """
      <a href="/sbc/upgrades/27-11-2x-79-upgrade/">one</a>
      <a href="/sbc/upgrades/27-11-2x-79-upgrade/">duplicate</a>
      <a href='/sbc/challenges/27-15-madrid-dreams/'>two</a>
      <a href="/sbc/challenges/26-900-old/">old game</a>
    """
    assert sync.discover_sbc_links(page) == [
        "https://www.fut.gg/sbc/upgrades/27-11-2x-79-upgrade/",
        "https://www.fut.gg/sbc/challenges/27-15-madrid-dreams/",
    ]


def test_discovers_challenge_ids_from_assets_and_embedded_array():
    page = """
      <img src="https://game-assets.fut.gg/2027/sbcs/challenges/28.png">
      <script>{"challengeEaIds":[28,31]}</script>
    """
    assert sync.discover_challenge_ids(page) == [28, 31]


def test_normalises_futgg_sbc_costs_requirements_and_solution_ids():
    raw = {
        "id": 5260,
        "eaId": 11,
        "name": "2x 79+ Upgrade",
        "description": "Earn a pack",
        "url": "/sbc/upgrades/27-11-2x-79-upgrade/",
        "cost": 7700,
        "costPc": 8100,
        "isRepeatable": True,
        "numberOfRepeats": 3,
        "repeatRefreshInterval": 86400,
        "endTime": "2026-09-28T17:00:01Z",
        "category": {"name": "Upgrades", "slug": "upgrades"},
        "awards": [{"count": 1, "pack": "2x 79+ Gold Players Pack"}],
        "challenges": [{
            "eaId": 28,
            "name": "2x 79+ Upgrade",
            "requirementsText": ["Exactly Gold Players", "Number of Players: 11"],
            "cheapestSolutionPrice": 7700,
            "cheapestSolutionPricePc": 8100,
            "cheapestSolutionUrl": "/27/squad-builder/db5695fb-8f42-4f3e-9627-376ea5cd22f5/",
            "cheapestSolutionPcUrl": "/27/squad-builder/c87e5fc8-d0bf-446a-80c1-383d899b5d95/",
        }],
    }
    sbc = sync.normalise_sbc(raw)
    assert sbc["external_id"] == "11"
    assert sbc["cost_console"] == 7700
    assert sbc["cost_pc"] == 8100
    assert sbc["repeat_count"] == 3
    assert sbc["reward_label"] == "2x 79+ Gold Players Pack"
    challenge = sbc["challenges"][0]
    assert challenge["external_challenge_id"] == 28
    assert challenge["requirements"]["requirement_1"] == "Exactly Gold Players"
    assert challenge["solution_console_uuid"] == "db5695fb-8f42-4f3e-9627-376ea5cd22f5"
    assert challenge["solution_pc_uuid"] == "c87e5fc8-d0bf-446a-80c1-383d899b5d95"


def test_normalises_squad_positions():
    payload = {"data": {"data": {"activeGroupPositions": [
        {"playerEaId": 223273, "positionIdx": 0},
        {"playerEaId": 227381, "positionIdx": 4},
        {"playerEaId": None, "positionIdx": 8},
    ]}}}
    assert sync.normalise_squad(payload) == [
        {"player_ea_id": 223273, "position_idx": 0},
        {"player_ea_id": 227381, "position_idx": 4},
    ]


def test_normalises_gallery_sets_and_items():
    base = {"data": {
        "tags": [{"id": 3, "name": "Same Nation"}],
        "categories": [{"name": "Premier League", "slug": "premier-league", "sets": [{"id": 7}]}],
    }}
    solutions = {"data": {"sets": [{
        "setId": 7,
        "name": "Leeds United",
        "grade": "B",
        "status": "optimal",
        "score": 2310,
        "tokens": 8,
        "costTiers": [{"cost": 11850, "tokens": 8}],
        "items": [{
            "eaId": 50596345, "playerEaId": 246697, "price": 700,
            "score": 160, "overall": 79, "clubEaId": 8,
            "nationEaId": 14, "rarityEaId": 0,
        }],
    }]}}
    gallery = sync.normalise_gallery(base, solutions)
    assert gallery["tags"][0]["name"] == "Same Nation"
    gallery_set = gallery["sets"][0]
    assert gallery_set["name"] == "Leeds United"
    assert gallery_set["category_slug"] == "premier-league"
    # The set cost is the displayed recommendation cost. Tier costs remain
    # available as target estimates.
    assert gallery_set["total_cost_coins"] == 700
    assert gallery_set["items"][0]["player_ea_id"] == 246697


def test_preserves_each_gallery_cost_tier_instead_of_flattening_them():
    solutions = {"data": {"sets": [{
        "setId": 9,
        "name": "Serie A Enilive",
        "grade": "S",
        "tokens": 95,
        "items": [{"eaId": 9001, "playerEaId": 101, "price": 10000}],
        "costTiers": [
            {"grade": "B", "cost": 2000, "tokens": 50, "items": [
                {"eaId": 9002, "playerEaId": 102, "price": 700},
                {"eaId": 9003, "playerEaId": 103, "price": 800},
            ]},
            {"grade": "A", "cost": 5000, "tokens": 75, "items": [
                {"eaId": 9004, "playerEaId": 104, "price": 5000},
            ]},
        ],
    }]}}
    gallery_set = sync.normalise_gallery({"data": {}}, solutions)["sets"][0]
    assert [tier["grade"] for tier in gallery_set["tiers"]] == ["B", "A", "S"]
    assert gallery_set["total_cost_coins"] == 10000
    assert gallery_set["item_count"] == 1
    assert gallery_set["max_tokens"] == 95
    assert [len(tier["items"]) for tier in gallery_set["tiers"]] == [0, 0, 1]
    assert gallery_set["items"][0]["card_ea_id"] == 9001


def test_gallery_recommendation_removes_exact_cards_but_keeps_player_versions():
    repeated = {"eaId": 9001, "playerEaId": 101, "overall": 89, "rarityEaId": 7, "clubEaId": 10, "price": 350000}
    other_version = {"eaId": 9002, "playerEaId": 101, "overall": 88, "rarityEaId": 7, "clubEaId": 10, "price": 105000}
    solutions = {"data": {"sets": [{
        "setId": 10, "name": "Duplicates", "grade": "B",
        "items": [repeated, repeated.copy(), other_version, other_version.copy()],
        "costTiers": [{"grade": "D", "cost": 10000, "tokens": 10}],
    }]}}
    gallery_set = sync.normalise_gallery({"data": {}}, solutions)["sets"][0]
    assert len(gallery_set["items"]) == 2
    assert [item["card_ea_id"] for item in gallery_set["items"]] == [9001, 9002]
    assert [item["position_idx"] for item in gallery_set["items"]] == [0, 1]


def test_browser_transport_reuses_warmed_page_for_same_origin_json_fetch():
    import asyncio

    class Page:
        async def evaluate(self, script, url):
            assert "fetch(url" in script
            assert "credentials: 'include'" in script
            assert url == "https://www.fut.gg/api/test/"
            return {"status": 200, "body": '{"data":{"ok":true}}'}

        async def goto(self, *args, **kwargs):
            raise AssertionError("JSON APIs must not be top-level navigations")

    async def exercise():
        transport = sync.BrowserTransport([Page()])
        payload = await transport.request("https://www.fut.gg/api/test/", expect_json=True)
        assert payload == {"data": {"ok": True}}
        assert transport.pages.qsize() == 1

    asyncio.run(exercise())


def test_browser_transport_still_navigates_for_html_pages():
    import asyncio

    class Response:
        status = 200

    class Page:
        async def goto(self, url, **kwargs):
            assert url == "https://www.fut.gg/sbc/"
            assert kwargs["wait_until"] == "domcontentloaded"
            return Response()

        async def content(self):
            return "<html>ok</html>"

        async def evaluate(self, *args, **kwargs):
            raise AssertionError("HTML pages should use organic navigation")

    async def exercise():
        transport = sync.BrowserTransport([Page()])
        payload = await transport.request("https://www.fut.gg/sbc/", expect_json=False)
        assert payload == "<html>ok</html>"
        assert transport.pages.qsize() == 1

    asyncio.run(exercise())


def test_crawl_uses_retrying_database_connection(monkeypatch):
    import asyncio

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Connection:
        def __init__(self):
            self.closed = False
            self.unlocked = False

        async def fetchval(self, query, *args):
            assert "pg_try_advisory_lock" in query
            return True

        async def execute(self, query, *args):
            if "pg_advisory_unlock" in query:
                self.unlocked = True

        def transaction(self):
            return Transaction()

        async def close(self):
            self.closed = True

    conn = Connection()
    calls = []

    async def retry_connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return conn

    async def noop(*args, **kwargs):
        return None

    async def collect():
        return [], {"sets": [], "tags": []}

    monkeypatch.setattr(sync, "DATABASE_URL", "postgresql://shared/prod")
    monkeypatch.setattr(sync, "DIAGNOSTIC", False)
    monkeypatch.setattr(sync, "connect_with_retry", retry_connect)
    monkeypatch.setattr(sync, "ensure_schema", noop)
    monkeypatch.setattr(sync, "_collect_remote_content", collect)
    monkeypatch.setattr(sync, "write_gallery", noop)
    monkeypatch.setattr(sync, "heartbeat", noop)

    assert asyncio.run(sync.crawl_once()) is True
    assert calls == [("postgresql://shared/prod", {"command_timeout": 120})]
    assert conn.unlocked is True
    assert conn.closed is True


def test_ingest_mode_collects_before_touching_database(monkeypatch):
    import asyncio

    async def collect():
        return [{"external_id": "1"}], {"sets": [{}], "tags": []}

    async def push(sbcs, gallery):
        assert len(sbcs) == 1
        assert len(gallery["sets"]) == 1
        return {"ok": True, "sbcs": 1, "solution_items": 0, "gallery_sets": 1, "gallery_tags": 0}

    async def database_must_not_be_used(*args, **kwargs):
        raise AssertionError("ingest mode must not connect directly to Postgres")

    monkeypatch.setattr(sync, "DIAGNOSTIC", False)
    monkeypatch.setattr(sync, "INGEST_URL", "https://api.eafc.live/api/v2/internal/futgg-content")
    monkeypatch.setattr(sync, "INGEST_TOKEN", "secret")
    monkeypatch.setattr(sync, "_collect_remote_content", collect)
    monkeypatch.setattr(sync, "_push_to_backend", push)
    monkeypatch.setattr(sync, "connect_with_retry", database_must_not_be_used)

    assert asyncio.run(sync.crawl_once()) is True


def test_gather_cancels_and_drains_sibling_before_propagating_error():
    import asyncio

    async def exercise():
        sibling_cancelled = asyncio.Event()
        let_sibling_start = asyncio.Event()

        async def fail():
            await let_sibling_start.wait()
            raise RuntimeError("primary failure")

        async def sibling():
            let_sibling_start.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

        try:
            await sync._gather_and_cancel_on_error(fail(), sibling())
        except RuntimeError as exc:
            assert str(exc) == "primary failure"
        else:
            raise AssertionError("the first task's failure must propagate")

        assert sibling_cancelled.is_set()

    asyncio.run(exercise())
