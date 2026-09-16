from __future__ import annotations

import asyncio
import logging
import time

from app.services.leak_accuracy import auto_confirm_shipped_cards
from app.services.leak_monitor import LeakMonitor
from app.services.leak_pipeline_bridge import sync_leak_context


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("leak_monitor_runner")
    monitor = LeakMonitor()
    monitor.validate()
    await monitor.ensure_sources()
    last_discovery = 0.0

    while True:
        started = time.monotonic()
        try:
            processed = await monitor.poll_once()
            confirmed = await auto_confirm_shipped_cards()
            bridged = await sync_leak_context()
            if processed or confirmed or bridged:
                log.info(
                    "leak cycle processed=%s confirmed=%s bridged=%s",
                    processed,
                    confirmed,
                    bridged,
                )

            if (
                monitor.discovery_enabled
                and started - last_discovery >= monitor.discovery_interval_seconds
            ):
                discovered = await monitor.run_discovery()
                last_discovery = started
                if discovered:
                    log.info("leak discovery candidates=%s", discovered)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("leak monitor cycle failed")

        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1, monitor.poll_seconds - elapsed))


if __name__ == "__main__":
    asyncio.run(main())
