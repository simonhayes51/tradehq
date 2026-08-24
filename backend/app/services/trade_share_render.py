# app/services/trade_share_render.py
#
# Screenshots the hidden /internal/render/trade-share/:tradeId frontend
# route with headless Chromium - same pattern as player_card_render.py,
# but a single one-off render per call (no shared browser/page pool):
# this is a low-volume, user-triggered action (clicking Share), not a
# bulk backfill job, so the simplicity of "launch, render, close" wins
# over the added complexity of a reusable browser instance.
from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass

from playwright.async_api import async_playwright

from app.services.trade_share_token import make_render_token

logger = logging.getLogger("trade_share_render")

# A wide social-card ratio (close to Discord/OG's 1200x630) rather than
# the tall player-card ratio - this is a shareable summary card, not a
# card-art export.
EXPORT_WIDTH = 900
EXPORT_HEIGHT = 470
EXPORT_DEVICE_SCALE = 2

_FRONTEND_URL = os.getenv("FRONTEND_URL", "https://app.futhub.co.uk").rstrip("/")
_NAV_TIMEOUT_MS = int(os.getenv("TRADE_SHARE_RENDER_TIMEOUT_MS", "20000"))
_READY_TIMEOUT_MS = int(os.getenv("TRADE_SHARE_READY_TIMEOUT_MS", "10000"))
_EXPORT_SELECTOR = "[data-trade-share-export]"


class TradeShareRenderError(RuntimeError):
    pass


@dataclass
class RenderedTradeShare:
    png_bytes: bytes
    width: int
    height: int


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise TradeShareRenderError("Screenshot buffer is not a valid PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


async def render_trade_share_png(user_id: str, trade_id: int) -> RenderedTradeShare:
    token = make_render_token(user_id, trade_id)
    url = f"{_FRONTEND_URL}/#/internal/render/trade-share/{trade_id}?token={token}"

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
                        "document.documentElement.dataset.tradeShareReady === 'true'",
                        timeout=_READY_TIMEOUT_MS,
                    )
                except Exception as exc:
                    raise TradeShareRenderError(
                        f"Trade {trade_id} never signalled ready within {_READY_TIMEOUT_MS}ms"
                    ) from exc

                error_marker = await card.get_attribute("data-trade-share-error")
                if error_marker:
                    raise TradeShareRenderError(f"Trade {trade_id} export marked itself failed: {error_marker}")

                png_bytes = await card.screenshot(type="png")
                if not png_bytes:
                    raise TradeShareRenderError(f"Trade {trade_id} screenshot returned an empty buffer")

                width, height = _png_dimensions(png_bytes)
                return RenderedTradeShare(png_bytes=png_bytes, width=width, height=height)
            finally:
                await context.close()
        finally:
            await browser.close()
