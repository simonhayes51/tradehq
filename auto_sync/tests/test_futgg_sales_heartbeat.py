"""
The sales collector reports its own state.

THE BUG
-------
`futgg_sales_sync.py` replaced `futgg_price_sync.py`, and inherited none
of its monitoring: it wrote no row into `pipeline_heartbeats` at all.
The retired worker's row stayed behind, frozen at whatever its final run
recorded - `ok: true` - because `ok` describes the outcome of the LAST
run and says nothing about whether another one ever happened.

So the backend's freshness panel and readiness check, both of which read
that table, could only see a three-week-old `ok: true` under a
sales-shaped worker name, while the live sales collector's actual state
was invisible. Sales are 70% of fair value and the gate on every signal
the engine emits; when the collector fell behind, every market surface
emptied and the only monitoring anywhere said the pipeline was fine.

What the collector's real failure looks like matters here too. It does
not crash and it does not go silent: FUT.GG rate-limits the fast
transport, the circuit breaker falls back to page capture, and the cycle
grinds out single-digit cards at minutes each. The container restarts on
schedule and checks in punctually the whole time. So `ok` has to mean
"collecting fast enough to stay inside the freshness gate", not "the
process is alive" - liveness is already carried by last_run_at.

No DB and no network: write_heartbeat is exercised against fakes.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")

import futgg_sales_sync as sales  # noqa: E402


class FakeConn:
    pass


class FakePool:
    def __init__(self):
        self.acquired = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.acquired += 1
                return FakeConn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class Recorder:
    """Stands in for monitoring.heartbeat."""

    def __init__(self, explode: bool = False):
        self.calls = []
        self.explode = explode

    async def __call__(self, conn, worker, ok=True, detail=None):
        if self.explode:
            raise RuntimeError("database is down")
        self.calls.append({"worker": worker, "ok": ok, "detail": detail})


@pytest.fixture
def recorder(monkeypatch):
    """Install a fake `monitoring` module.

    write_heartbeat imports it lazily precisely so an optional dependency
    cannot stop the collector at load time, which also makes it
    injectable here without aiohttp installed.
    """
    rec = Recorder()
    module = types.ModuleType("monitoring")
    module.heartbeat = rec
    monkeypatch.setitem(sys.modules, "monitoring", module)
    return rec


def _write(pool=None, *, transport="signed_fetch", stats=None, elapsed=60.0,
           breaker_tripped=False, coverage=None):
    return asyncio.run(sales.write_heartbeat(
        pool if pool is not None else FakePool(),
        transport=transport,
        stats=stats if stats is not None else {"ok": 180, "failed": 4, "sales_seen": 9000},
        elapsed=elapsed,
        breaker_tripped=breaker_tripped,
        coverage=coverage,
    ))


class TestItReportsAtAll:
    def test_the_worker_writes_a_heartbeat(self, recorder):
        _write()
        assert len(recorder.calls) == 1

    def test_it_reports_under_its_own_name(self, recorder):
        # NOT 'futgg_price_sync'. That row belongs to the retired scraper
        # and is a tombstone; writing to it would keep a decommissioned
        # worker looking alive and still leave this one unmonitored.
        _write()
        assert recorder.calls[0]["worker"] == "futgg_sales_sync"
        assert sales.HEARTBEAT_WORKER == "futgg_sales_sync"

    def test_it_is_called_every_cycle(self):
        # Once per cycle, not once per process: a bounded run is stopped
        # by its runtime limit or an external SIGTERM at an arbitrary
        # point, and a report written only on clean shutdown is a report
        # that never arrives on the runs that matter.
        import inspect
        assert "await write_heartbeat(" in inspect.getsource(sales.run_forever)


class TestOkMeansKeepingUp:
    def test_a_healthy_cycle_is_ok(self, recorder):
        _write(stats={"ok": 180, "failed": 4, "sales_seen": 9000}, elapsed=120.0)
        assert recorder.calls[0]["ok"] is True

    def test_a_tripped_breaker_with_healthy_throughput_is_still_ok(self, recorder):
        # Corrected after production evidence (1 September) contradicted
        # the original assumption here. With
        # PAGE_CAPTURE_FALLBACK_ON_THROTTLE off, hitting FUT.GG's
        # rate-limit wall after ~150-175 productive cards is the normal,
        # sustainable steady state now, not a failure - a real cycle
        # logged cards_ok=153 cards_per_min=27.9 circuit_breaker=tripped,
        # comfortably beating the throughput the catalogue actually
        # needs. Marking every such cycle not-ok would cry wolf on
        # exactly the cycles that are working as intended.
        _write(stats={"ok": 153, "failed": 15, "sales_seen": 4781},
               elapsed=329.0, breaker_tripped=True)
        assert recorder.calls[0]["ok"] is True

    def test_a_tripped_breaker_with_no_throughput_is_not_ok(self, recorder):
        # The pattern this still has to catch: a cycle that trips
        # immediately having collected nothing (the "instant retrip"
        # cycle right after a backoff, seen in production as
        # cards_ok=0 failed=6 in a few seconds). Caught by cards_per_min
        # alone, with no need to also look at breaker_tripped.
        _write(stats={"ok": 0, "failed": 6, "sales_seen": 0},
               elapsed=6.1, breaker_tripped=True)
        assert recorder.calls[0]["ok"] is False

    def test_a_throughput_collapse_is_not_ok(self, recorder):
        # Straight from production: page_capture managing cards_ok=2
        # failed=18 in 2311s. Nothing crashed, nothing is silent, and at
        # this rate the 72h sales-freshness gate empties the feed.
        _write(transport="page_capture",
               stats={"ok": 2, "failed": 18, "sales_seen": 200},
               elapsed=2311.5)
        assert recorder.calls[0]["ok"] is False

    def test_a_cycle_that_collected_nothing_is_not_ok(self, recorder):
        _write(stats={"ok": 0, "failed": 20, "sales_seen": 0}, elapsed=300.0)
        assert recorder.calls[0]["ok"] is False

    def test_the_throughput_floor_is_configurable(self):
        assert sales.HEARTBEAT_MIN_CARDS_PER_MIN > 0


class TestTheDetailIsUseful:
    def test_it_names_the_transport_and_the_rate(self, recorder):
        # Which transport, and how fast. Together these say whether the
        # fallback is in use and whether it is coping - the two questions
        # an operator actually has.
        _write(transport="page_capture",
               stats={"ok": 2, "failed": 18, "sales_seen": 200},
               elapsed=2311.5)
        detail = recorder.calls[0]["detail"]
        assert "transport=page_capture" in detail
        assert "cards_per_min=0.1" in detail

    def test_a_tripped_breaker_is_named(self, recorder):
        _write(breaker_tripped=True)
        assert "circuit_breaker=tripped" in recorder.calls[0]["detail"]

    def test_coverage_rides_along_when_known(self, recorder):
        # Coverage is the metric that says whether the collector is
        # winning or losing over time. A cycle can look fine and still be
        # losing ground; only the trend shows that.
        _write(coverage={"total": 16013, "any_sales": 7370, "enough_sales": 6228,
                         "never_read": 0, "chronic_failures": 0})
        detail = recorder.calls[0]["detail"]
        assert "coverage=7370/16013" in detail
        assert "46.0%" in detail
        # The population that can actually produce a signal, which is the
        # number the engine's minimum-sales gate keys on.
        assert "signal_capable=6228" in detail

    def test_missing_coverage_is_simply_omitted(self, recorder):
        _write(coverage=None)
        assert "coverage=" not in recorder.calls[0]["detail"]

    def test_top_errors_are_included_but_bounded(self, recorder):
        _write(stats={"ok": 2, "failed": 18, "sales_seen": 200, "errors": {
            "navigation_failed:TimeoutError": 9,
            "page_capture_challenge": 4,
            "navigation_failed:Error": 3,
            "page_capture_status_200": 2,
        }})
        detail = recorder.calls[0]["detail"]
        assert "navigation_failed:TimeoutError:9" in detail
        # Three, ordered by count - the column is truncated at 500 chars
        # and a long tail of one-offs would push out the useful part.
        assert "page_capture_status_200" not in detail

    def test_the_detail_fits_the_column(self, recorder):
        # pipeline_heartbeats.detail is truncated at 500 chars on write,
        # so anything past that is silently lost. The error list is the
        # only unbounded part, which is why it is capped above rather
        # than trusted to be short.
        _write(stats={"ok": 2, "failed": 18, "sales_seen": 200,
                      "errors": {f"error_kind_{i}": i for i in range(40)}},
               coverage={"total": 16013, "any_sales": 7370, "enough_sales": 6228,
                         "never_read": 0, "chronic_failures": 0})
        assert len(recorder.calls[0]["detail"]) <= 500


class TestMonitoringNeverKillsTheWorker:
    def test_a_failing_write_is_swallowed(self, monkeypatch):
        module = types.ModuleType("monitoring")
        module.heartbeat = Recorder(explode=True)
        monkeypatch.setitem(sys.modules, "monitoring", module)
        _write()  # must not raise

    def test_a_missing_monitoring_module_is_survivable(self, monkeypatch):
        def _fail(name, *args, **kwargs):
            if name == "monitoring":
                raise ImportError("no aiohttp")
            return real_import(name, *args, **kwargs)

        import builtins
        real_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", _fail)
        _write()  # must not raise

    def test_it_is_not_imported_at_module_load(self):
        # monitoring pulls in aiohttp. A collector must not fail to start
        # because an optional monitoring dependency is missing.
        import inspect
        source = inspect.getsource(sales)
        header = source.split("async def write_heartbeat")[0]
        assert "from monitoring import" not in header
        assert "import monitoring" not in header
