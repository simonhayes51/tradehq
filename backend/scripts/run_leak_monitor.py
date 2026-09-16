from __future__ import annotations

import asyncio
import logging

from app.services.leak_monitor import LeakMonitor


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    monitor = LeakMonitor()
    await monitor.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
