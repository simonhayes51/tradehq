from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.services import managed_startup_tasks as managed


def run(coro):
    return asyncio.run(coro)


def _reset(monkeypatch):
    monkeypatch.setattr(managed, "_jobs", {})
    monkeypatch.setattr(managed, "_installed", False)


def test_registered_job_runs_inside_existing_lifespan_and_is_cancelled(monkeypatch):
    _reset(monkeypatch)
    events = []

    @asynccontextmanager
    async def original_lifespan(app):
        events.append("original_start")
        try:
            yield
        finally:
            events.append("original_stop")

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def job():
        events.append("job_start")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("job_cancel")
            cancelled.set()
            raise

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=original_lifespan))
    application = SimpleNamespace(app=app)

    managed.register_startup_job("test", job)
    managed.install_managed_startup_jobs(application)

    async def exercise():
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), timeout=1)
            assert events[:2] == ["original_start", "job_start"]
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    run(exercise())
    assert events == ["original_start", "job_start", "job_cancel", "original_stop"]


def test_registering_same_name_replaces_job(monkeypatch):
    _reset(monkeypatch)

    async def first():
        return None

    async def second():
        return None

    managed.register_startup_job("same", first)
    managed.register_startup_job("same", second)
    assert managed._jobs == {"same": second}


def test_install_is_idempotent(monkeypatch):
    _reset(monkeypatch)

    @asynccontextmanager
    async def original_lifespan(app):
        yield

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=original_lifespan))
    application = SimpleNamespace(app=app)

    managed.install_managed_startup_jobs(application)
    installed_context = app.router.lifespan_context
    managed.install_managed_startup_jobs(application)
    assert app.router.lifespan_context is installed_context


def test_performance_prewarm_installers_do_not_use_deprecated_startup_decorator():
    from app.services import dashboard_market_cache, track_record_cache_routes

    dashboard_source = inspect.getsource(dashboard_market_cache.install_dashboard_market_cache)
    track_source = inspect.getsource(track_record_cache_routes.install_track_record_prewarm)

    assert '.on_event("startup")' not in dashboard_source
    assert ".on_event('startup')" not in dashboard_source
    assert '.on_event("startup")' not in track_source
    assert ".on_event('startup')" not in track_source
    assert "register_startup_job" in dashboard_source
    assert "register_startup_job" in track_source
