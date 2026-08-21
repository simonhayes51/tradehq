"""
Phase 4 diagnostic -- prints evidence about WHY chart captures are landing
on a suspiciously identical circle count, straight to stdout/logs (no file
export, no local Python needed -- run this the same way as
research_fc26_chart_capture.py: temporarily override this Coolify
resource's start command to `python research_fc26_chart_diagnose.py`,
trigger "Run now", read the logs, then revert the command).

Reads rows ALREADY captured into fc26_chart_capture_fixtures by a
previous research_fc26_chart_capture.py run -- makes no network requests
of its own, just SQL + text inspection. For each of up to
FUTGG_CHART_DIAGNOSE_COUNT (default 3) captured rows with a saved
full_page_html, reports:

  - whether Cloudflare/anti-bot challenge markers appear in the full page
    (a real 403/challenge page looks nothing like a normal fut.gg page,
    so this also rules that scenario in or out directly)
  - every button whose text mentions "year" (case-insensitive) anywhere
    on the page, and how many distinct .recharts-responsive-container
    elements exist -- checks whether the button-text assumption from
    Phase 1 (exact "Year") still holds, or whether there's more than one
    plausible chart on the page
  - the raw stored chart_html for that row (what got captured as "the
    chart"), so its actual circle cx/cy values, surrounding markup, and
    any visible loading/skeleton indicators can be inspected directly

Env vars:
  DATABASE_URL                    required
  FUTGG_CHART_DIAGNOSE_COUNT      default 3
  FUTGG_CHART_DIAGNOSE_CARD_ID    optional -- inspect one specific card_id
                                   instead of "most recent N with a saved
                                   full page"
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s INFO %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("research_fc26_chart_diagnose")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

COUNT = int(os.getenv("FUTGG_CHART_DIAGNOSE_COUNT", "3"))
CARD_ID = os.getenv("FUTGG_CHART_DIAGNOSE_CARD_ID", "").strip()

CHALLENGE_MARKERS = (
    "just a moment", "cf-mitigated", "attention required", "cf-chl",
    "checking your browser", "enable javascript and cookies", "cf-turnstile",
    "__cf_chl", "cloudflare ray id",
)

BUTTON_RE = re.compile(r"<button\b[^>]*>(.*?)</button>", re.IGNORECASE | re.DOTALL)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
CIRCLE_RE = re.compile(r'<circle\b[^>]*\bcx="([\d.]+)"[^>]*\bcy="([\d.]+)"[^>]*>')
RESPONSIVE_CONTAINER_RE = re.compile(r'class="[^"]*recharts-responsive-container[^"]*"')


def clean(text: str) -> str:
    return TAG_STRIP_RE.sub("", text).strip()


async def diagnose_row(row: dict) -> None:
    card_id = row["card_id"]
    name = row["name"]
    url = row["url"]
    chart_html = row["chart_html"] or ""
    full_page_html = row["full_page_html"] or ""

    log.info("=" * 78)
    log.info("card_id=%s name=%r url=%s", card_id, name, url)
    log.info("stored num_circles=%s status=%s error_reason=%r",
              row["num_circles"], row["status"], row["error_reason"])

    if not full_page_html:
        log.info("(no full_page_html saved for this row -- only chart_html available)")
    else:
        page_lower = full_page_html.lower()
        hits = [m for m in CHALLENGE_MARKERS if m in page_lower]
        log.info("full_page_html length: %d chars", len(full_page_html))
        log.info("Cloudflare/challenge markers found: %s", hits or "NONE")

        buttons = [clean(b) for b in BUTTON_RE.findall(full_page_html)]
        buttons = [b for b in buttons if b]
        year_like = [b for b in buttons if "year" in b.lower()]
        log.info("all button labels on page (%d total): %s", len(buttons), buttons[:40])
        log.info("buttons mentioning 'year': %s", year_like)

        container_count = len(RESPONSIVE_CONTAINER_RE.findall(full_page_html))
        log.info("count of .recharts-responsive-container elements on full page: %d", container_count)

    log.info("--- stored chart_html (what was captured as 'the chart') ---")
    if chart_html:
        circles = CIRCLE_RE.findall(chart_html)
        log.info("chart_html length: %d chars, circle count found in it: %d", len(chart_html), len(circles))
        if circles:
            log.info("all (cx, cy) pairs: %s", circles)
        # Print in chunks so nothing gets truncated by a log line limit.
        chunk_size = 3000
        for i in range(0, len(chart_html), chunk_size):
            log.info("chart_html[%d:%d]: %s", i, i + chunk_size, chart_html[i:i + chunk_size])
    else:
        log.info("(no chart_html stored for this row)")
    log.info("=" * 78)


async def main() -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if CARD_ID:
            rows = await conn.fetch(
                "SELECT * FROM fc26_chart_capture_fixtures WHERE card_id = $1",
                int(CARD_ID),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM fc26_chart_capture_fixtures
                WHERE full_page_html IS NOT NULL
                ORDER BY captured_at DESC
                LIMIT $1
                """,
                COUNT,
            )

        if not rows:
            log.info(
                "No rows found (CARD_ID=%r). Has research_fc26_chart_capture.py "
                "run and saved at least one full_page_html yet?", CARD_ID or None,
            )
            return 1

        log.info("Diagnosing %d row(s)...", len(rows))
        for row in rows:
            await diagnose_row(dict(row))

        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
