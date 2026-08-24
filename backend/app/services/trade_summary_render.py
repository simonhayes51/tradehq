# app/services/trade_summary_render.py
#
# Screenshots the hidden /internal/render/trade-summary/:period route -
# same one-off launch/render/close pattern as trade_share_render.py.
from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass

from playwright.async_api import async_playwright

from app.services.trade_summary_token import make_render_token

logger = logging.getLogger("trade_summary_render")

EXPORT_WIDTH = 900
EXPORT_HEIGHT = 470
EXPORT_DEVICE_SCALE = 2

_FRONTEND_URL = os.getenv("FRONTEND_URL", "https://app.futhub.co.uk").rstrip("/")
_NAV_TIMEOUT_MS = int(os.getenv("TRADE_SHARE_RENDER_TIMEOUT_MS", "20000"))
_READY_TIMEOUT_MS = int(os.getenv("TRADE_SHARE_READY_TIMEOUT_MS", "10000"))
_EXPORT_SELECTOR = "[data-trade-summary-export]"


class TradeSummaryRenderError(RuntimeError):
    pass


@dataclass
class RenderedTradeSummary:
    png_bytes: bytes
    width: int
    height: int


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise TradeSummaryRenderError("Screenshot buffer is not a valid PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


async def render_trade_summary_png(user_id: str, period: str) -> RenderedTradeSummary:
    token = make_render_token(user_id, period)
    url = f"{_FRONTEND_URL}/#/internal/render/trade-summary/{period}?token={token}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            context = await browser.new_context(
                viewport={"width": EXPORT_WIDTH, "height": EXPORT_HEIGHT},
                device_scale_factor=EXPORT_DEVICE_SCALE,
            )
            try:
                page = await context.new_page()
                page.set_default_timeout(_NAV_TIMEOUT_MS)
                await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)

                card = page.locator(_EXPORT_SELECTOR)
                await card.wait_for(state="attached", timeout=_NAV_TIMEOUT_MS)

                try:
                    await page.wait_for_function(
                        "document.documentElement.dataset.tradeSummaryReady === 'true'",
                        timeout=_READY_TIMEOUT_MS,
                    )
                except Exception as exc:
                    raise TradeSummaryRenderError(
                        f"Summary {period} never signalled ready within {_READY_TIMEOUT_MS}ms"
                    ) from exc

                error_marker = await card.get_attribute("data-trade-summary-error")
                if error_marker:
                    raise TradeSummaryRenderError(f"Summary {period} export marked itself failed: {error_marker}")

                png_bytes = await card.screenshot(type="png")
                if not png_bytes:
                    raise TradeSummaryRenderError(f"Summary {period} screenshot returned an empty buffer")

                width, height = _png_dimensions(png_bytes)
                return RenderedTradeSummary(png_bytes=png_bytes, width=width, height=height)
            finally:
                await context.close()
        finally:
            await browser.close()
