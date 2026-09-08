"""Register small production prewarm jobs on FastAPI's lifespan.

Performance/bootstrap helpers are installed after ``main`` has already created
its FastAPI app, so they cannot be declared inside main.py's lifespan function
without turning every incident-safe helper into a main.py rewrite.

This registry composes one thin lifespan wrapper around the app's existing
lifespan context. Registered jobs start only after the original lifespan has
finished startup (so DB pools/app.state exist), run as managed asyncio tasks,
and are cancelled/awaited before the original lifespan performs shutdown.

It replaces deprecated ``app.on_event('startup')`` hooks without changing the
existing main.py lifecycle or ownership of its long-running workers.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Dict

log = logging.getLogger("managed_startup_tasks")

StartupJob = Callable[[], Awaitable[None]]

_jobs: Dict[str, StartupJob] = {}
_installed = False


def register_startup_job(name: str, job: StartupJob) -> None:
    """Register or replace one named managed startup job."""
    if not name:
        raise ValueError("startup job name is required")
    _jobs[name] = job


def install_managed_startup_jobs(application) -> None:
    """Wrap the existing app lifespan once and run registered jobs inside it."""
    global _installed
    if _installed:
        return

    app = application.app
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def managed_lifespan(fastapi_app):
        async with original_lifespan(fastapi_app):
            tasks: Dict[str, asyncio.Task] = {}
            for name, job in list(_jobs.items()):
                try:
                    tasks[name] = asyncio.create_task(job(), name=f"eafc:{name}")
                except Exception:
                    log.warning("failed to schedule startup job %s", name, exc_info=True)
            if tasks:
                log.info("managed startup jobs scheduled: %s", sorted(tasks))
            try:
                yield
            finally:
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
                if tasks:
                    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                    for name, result in zip(tasks, results):
                        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                            log.warning("managed startup job %s ended with error: %r", name, result)

    app.router.lifespan_context = managed_lifespan
    _installed = True
    log.info("managed startup lifespan wrapper installed (%d jobs)", len(_jobs))
