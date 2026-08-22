"""
Phase 5 -- parses fc26_chart_capture_fixtures and loads the real,
queryable historical dataset. Runs the same way as the other
research_fc26_* scripts (temporary Coolify command override on the
PlayerSync image + manual "Run now").

Design, per explicit instruction: futgg_players is the CANONICAL FC26
card metadata source (name/rating/position/rarity/league/pace/shooting/
height/body_type/skill_moves/etc. -- source_card_id is already its
PRIMARY KEY). Nothing here duplicates any of that. This script produces
exactly two tables, both joining to futgg_players.source_card_id via a
real FK (never hard-deleted in production -- confirmed before adding
this):

  fc26_historical_daily_prices  -- the actual time series: card_id,
      observed_date, price, source, extraction_method, confidence,
      parser_version, scraped_at, loaded_at. PRIMARY KEY (card_id,
      observed_date) -- doubles as the index a card_id join/lookup
      needs, no separate index required for that. One extra index on
      observed_date alone supports Phase 8's other main access pattern:
      cross-sectional queries across many cards for one launch-phase
      date range, which the composite PK (card_id-leading) doesn't serve.

  fc26_historical_extraction_runs  -- ONE row per card_id (every card
      fc26_chart_capture_fixtures has a row for, success or failure),
      holding ONLY historical-extraction metadata: point count, date
      range, Y-axis calibration residual, any tick anomalies
      encountered, error reason, parser_version, when the source chart
      was captured vs. when this row was extracted. Explicitly NOT a
      second player-metadata table -- see module docstring above.

Created the same way every other futgg_*/fc26_* table in this codebase
is: CREATE TABLE IF NOT EXISTS inside the owning script (ensure_schema),
not a backend/migrations entry -- that's the dominant, consistent
convention across auto_sync (every worker owns its own tables;
backend/migrations only ever reads them via requires-table: to build
derived views on top, e.g. migration 038's futgg_market_snapshot).

Idempotent: reprocessing a card DELETEs its existing
fc26_historical_daily_prices rows and its fc26_historical_extraction_runs
row, then re-inserts fresh ones from the current chart_html + current
parser logic -- safe to rerun the whole script any number of times
(e.g. after a parser fix), always converges to "what the current parser
says about the current captured HTML," never leaves stale rows behind
from a card that used to succeed and now fails (or vice versa).

Quality validation: prints a summary at the end -- load success rate,
total price rows, date range coverage, cards with high calibration
residual (>20%, i.e. the extreme-price-range edge case Mbappe/Vinicius
surfaced in Phase 4) or tick anomalies, so problems are visible rather
than silently trusted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg
from db_connect import connect_with_retry

from research_fc26_chart_report import (
    ChartParseError,
    extract_series,
    parse_captured_date,
    parse_current_price_badge,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("research_fc26_historical_loader")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

# Bump this whenever extract_series()'s algorithm changes materially --
# lets a future query or manual audit distinguish rows produced by
# different parser logic (e.g. before/after the year-anchoring or
# outlier-tick-recovery fixes from this same session).
PARSER_VERSION = "fc26-extractor-v1"

HIGH_CALIBRATION_ERR_PCT = 20.0  # flag threshold for the summary, not a hard failure


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fc26_historical_daily_prices (
            card_id             BIGINT NOT NULL REFERENCES futgg_players(source_card_id),
            observed_date       DATE NOT NULL,
            price               INTEGER NOT NULL CHECK (price >= 0),
            source              TEXT NOT NULL DEFAULT 'futgg_chart',
            extraction_method   TEXT NOT NULL DEFAULT 'svg_reconstructed',
            confidence          TEXT NOT NULL DEFAULT 'reconstructed'
                                 CHECK (confidence IN ('exact', 'reconstructed')),
            parser_version      TEXT NOT NULL,
            scraped_at          TIMESTAMPTZ NOT NULL,
            loaded_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (card_id, observed_date)
        )
        """
    )
    # Composite PK above is already an index leading with card_id -- covers
    # both "join to futgg_players.source_card_id" and "WHERE card_id = X".
    # This second index is the one thing the PK doesn't serve: a
    # cross-sectional scan over many cards for one date/date-range (Phase
    # 8's launch-phase segmentation queries), which needs date as the
    # leading column.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fc26_hist_prices_date "
        "ON fc26_historical_daily_prices (observed_date)"
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fc26_historical_extraction_runs (
            card_id                  BIGINT PRIMARY KEY REFERENCES futgg_players(source_card_id),
            status                   TEXT NOT NULL,
            num_points               INTEGER NOT NULL DEFAULT 0,
            first_observed_date      DATE,
            last_observed_date       DATE,
            calibration_max_pct_err  NUMERIC(8,3),
            tick_warnings            TEXT[] NOT NULL DEFAULT '{}',
            error_reason             TEXT,
            parser_version           TEXT NOT NULL,
            source_captured_at       TIMESTAMPTZ,
            extracted_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


async def _process_card(conn: asyncpg.Connection, row: dict) -> dict:
    """Returns a small result dict for the summary pass -- never raises;
    a parse failure is a recorded outcome, not a crash."""
    card_id = row["card_id"]
    captured_at = row["captured_at"]

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM fc26_historical_daily_prices WHERE card_id = $1", card_id
        )

        if row["status"] != "success" or not row["chart_html"]:
            error_reason = f"capture failed: {row['error_reason'] or 'unknown'}"
            await _upsert_run(conn, card_id, "failed", 0, None, None, None, [],
                               error_reason, captured_at)
            return {"card_id": card_id, "status": "failed", "reason": error_reason,
                    "num_points": 0, "calib": None}

        html = row["chart_html"]
        captured_date = parse_captured_date(html) or (
            captured_at.date() if captured_at else None
        )
        try:
            points, tick_warnings, calib_max_pct_err = extract_series(html, captured_date)
        except ChartParseError as exc:
            error_reason = f"extraction failed: {exc}"
            await _upsert_run(conn, card_id, "failed", 0, None, None, None, [],
                               error_reason, captured_at)
            return {"card_id": card_id, "status": "failed", "reason": error_reason,
                    "num_points": 0, "calib": None}

        if not points:
            error_reason = "extraction produced zero points"
            await _upsert_run(conn, card_id, "failed", 0, None, None, None, [],
                               error_reason, captured_at)
            return {"card_id": card_id, "status": "failed", "reason": error_reason,
                    "num_points": 0, "calib": None}

        now = datetime.now(timezone.utc)
        records = [
            (card_id, obs_date, price, "futgg_chart", "svg_reconstructed",
             "reconstructed", PARSER_VERSION, captured_at, now)
            for obs_date, price in points
        ]
        await conn.copy_records_to_table(
            "fc26_historical_daily_prices",
            records=records,
            columns=["card_id", "observed_date", "price", "source",
                     "extraction_method", "confidence", "parser_version",
                     "scraped_at", "loaded_at"],
        )

        first_date, last_date = points[0][0], points[-1][0]
        await _upsert_run(conn, card_id, "success", len(points), first_date,
                           last_date, calib_max_pct_err, tick_warnings, None, captured_at)
        return {"card_id": card_id, "status": "success", "reason": None,
                "num_points": len(points), "calib": calib_max_pct_err,
                "tick_warnings": tick_warnings}


async def _upsert_run(conn: asyncpg.Connection, card_id: int, status: str,
                       num_points: int, first_date, last_date,
                       calib_max_pct_err: float | None, tick_warnings: list[str],
                       error_reason: str | None, source_captured_at) -> None:
    await conn.execute(
        """
        INSERT INTO fc26_historical_extraction_runs (
            card_id, status, num_points, first_observed_date, last_observed_date,
            calibration_max_pct_err, tick_warnings, error_reason, parser_version,
            source_captured_at, extracted_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (card_id) DO UPDATE SET
            status = EXCLUDED.status,
            num_points = EXCLUDED.num_points,
            first_observed_date = EXCLUDED.first_observed_date,
            last_observed_date = EXCLUDED.last_observed_date,
            calibration_max_pct_err = EXCLUDED.calibration_max_pct_err,
            tick_warnings = EXCLUDED.tick_warnings,
            error_reason = EXCLUDED.error_reason,
            parser_version = EXCLUDED.parser_version,
            source_captured_at = EXCLUDED.source_captured_at,
            extracted_at = EXCLUDED.extracted_at
        """,
        card_id, status, num_points, first_date, last_date, calib_max_pct_err,
        tick_warnings, error_reason, PARSER_VERSION, source_captured_at,
        datetime.now(timezone.utc),
    )


async def main() -> int:
    conn = await connect_with_retry(DATABASE_URL)
    try:
        await ensure_schema(conn)

        rows = await conn.fetch(
            """
            SELECT card_id, status, chart_html, error_reason, captured_at
            FROM fc26_chart_capture_fixtures
            ORDER BY card_id
            """
        )
        if not rows:
            log.error("No rows in fc26_chart_capture_fixtures -- has capture run yet?")
            return 1

        log.info("Loading %d captured cards (parser_version=%s)...", len(rows), PARSER_VERSION)

        results = []
        for i, row in enumerate(rows, 1):
            result = await _process_card(conn, dict(row))
            results.append(result)
            if result["status"] == "success":
                log.info("[%d/%d] card_id=%s OK: %d points loaded", i, len(rows),
                          result["card_id"], result["num_points"])
            else:
                log.warning("[%d/%d] card_id=%s FAILED: %s", i, len(rows),
                            result["card_id"], result["reason"])

        # --- Quality validation summary ---
        succeeded = [r for r in results if r["status"] == "success"]
        failed = [r for r in results if r["status"] == "failed"]
        total_points = sum(r["num_points"] for r in succeeded)

        log.info("=" * 100)
        log.info("PHASE 5 LOAD SUMMARY")
        log.info("=" * 100)
        log.info("cards processed: %d (%d success, %d failed)", len(results),
                  len(succeeded), len(failed))
        log.info("total price rows loaded: %d", total_points)

        if succeeded:
            date_range = await conn.fetchrow(
                "SELECT min(observed_date) AS min_d, max(observed_date) AS max_d "
                "FROM fc26_historical_daily_prices"
            )
            log.info("date range covered: %s .. %s", date_range["min_d"], date_range["max_d"])

            high_calib = [r for r in succeeded
                          if r["calib"] is not None and r["calib"] > HIGH_CALIBRATION_ERR_PCT]
            log.info("cards with calibration residual > %.0f%% (needs manual review): %d -> %s",
                      HIGH_CALIBRATION_ERR_PCT, len(high_calib),
                      [r["card_id"] for r in high_calib])

            warned = [r for r in succeeded if r.get("tick_warnings")]
            log.info("cards with Y-axis tick anomalies (recovered, not fatal): %d -> %s",
                      len(warned), [r["card_id"] for r in warned])

            low_points = [r for r in succeeded if r["num_points"] < 20]
            log.info("cards with <20 points (recently-added or still-suspect): %d -> %s",
                      len(low_points), [r["card_id"] for r in low_points])

        log.info("=" * 100)
        if failed:
            log.info("FAILURES (%d) -- not silently skipped:", len(failed))
            for r in failed:
                log.info("  card_id=%s reason=%s", r["card_id"], r["reason"])

        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
