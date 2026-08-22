# backend/scripts/run_investment_engine_replay.py
"""
CLI entry point for the FC26 Investment Engine replay validation harness
(app/services/investment_engine/replay/). Mirrors the existing one-off
script pattern used throughout auto_sync/research_fc26_*.py and
app/services/investment_engine/backtest.py's own __main__ block.

Usage:

    # Production path - requires a database with fc26_historical_daily_
    # prices + futgg_players populated (see research/fc26_launch_market
    # and auto_sync/research_fc26_historical_loader.py). Defaults to just
    # the launch window (real Web App Launch through Full Release +21) -
    # pass --full-history to walk every day the table has instead:
    PLAYER_DATABASE_URL=postgres://... python -m scripts.run_investment_engine_replay --source postgres

    # Synthetic-fixture path - no database required. Used in development/
    # CI, and in any environment (like this one) with no live database
    # connection. NOT the real FC26 dataset - see
    # app/services/investment_engine/replay/fixtures.py's module
    # docstring.
    python -m scripts.run_investment_engine_replay --source synthetic

Writes replay_result.json, daily_states.csv, signals.csv and
REPLAY_REPORT.md to --out-dir (default: backend/replay_output/).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("run_investment_engine_replay")

# The loader's own quality bar (auto_sync/research_fc26_historical_
# loader.py's HIGH_CALIBRATION_ERR_PCT) - a card whose chart-reconstructed
# prices exceed this residual is already flagged there as needing manual
# review. Excluded from the replay by default so an isolated calibration
# glitch (a sudden reconstructed price spike/drop that never really
# happened) doesn't show up as a dramatic ROI outcome for that card.
DEFAULT_CALIBRATION_THRESHOLD_PCT = 20.0


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["synthetic", "postgres"], default="synthetic")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (defaults to PLAYER_DATABASE_URL/DATABASE_URL env)")
    parser.add_argument("--out-dir", default="replay_output")
    parser.add_argument("--n-cards", type=int, default=320, help="synthetic source only")
    parser.add_argument("--seed", type=int, default=20260821, help="synthetic source only")
    parser.add_argument("--start", type=_parse_date, default=None,
                         help="YYYY-MM-DD - defaults to the real FC26 Web App Launch date for --source postgres")
    parser.add_argument("--end", type=_parse_date, default=None,
                         help="YYYY-MM-DD - defaults to Full Release +21 for --source postgres")
    parser.add_argument("--full-history", action="store_true",
                         help="postgres source only: walk every day the table has, ignoring --start/--end defaults")
    parser.add_argument("--include-low-quality-cards", action="store_true",
                         help="postgres source only: do NOT exclude cards fc26_historical_extraction_runs flags "
                              "as high chart-reconstruction error or failed extraction (excluded by default)")
    parser.add_argument("--calibration-threshold-pct", type=float, default=DEFAULT_CALIBRATION_THRESHOLD_PCT)
    return parser.parse_args()


async def _load_postgres_provider(dsn: str, *, exclude_low_quality: bool, calibration_threshold_pct: float):
    import asyncpg  # local import: only needed for this path
    from app.services.investment_engine.replay.fixtures import load_from_postgres_rows

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            bad_card_ids: set = set()
            if exclude_low_quality:
                bad_rows = await conn.fetch(
                    "SELECT card_id FROM fc26_historical_extraction_runs "
                    "WHERE status != 'success' OR calibration_max_pct_err > $1",
                    calibration_threshold_pct,
                )
                bad_card_ids = {r["card_id"] for r in bad_rows}
                if bad_card_ids:
                    log.warning(
                        "excluding %d card(s) flagged by fc26_historical_extraction_runs "
                        "(status != success OR calibration residual > %.0f%%) - pass "
                        "--include-low-quality-cards to keep them",
                        len(bad_card_ids), calibration_threshold_pct,
                    )

            price_rows = await conn.fetch(
                "SELECT card_id, observed_date, price FROM fc26_historical_daily_prices "
                "ORDER BY card_id, observed_date"
            )
            meta_rows = await conn.fetch(
                "SELECT source_card_id, name, primary_position FROM futgg_players "
                "WHERE price_tier IN ('gold_common', 'gold_rare')"
            )
        if bad_card_ids:
            price_rows = [r for r in price_rows if r["card_id"] not in bad_card_ids]
            meta_rows = [r for r in meta_rows if r["source_card_id"] not in bad_card_ids]
        return load_from_postgres_rows(
            [(r["card_id"], r["observed_date"], r["price"]) for r in price_rows],
            [(r["source_card_id"], r["name"], r["primary_position"]) for r in meta_rows],
        )
    finally:
        await pool.close()


def main() -> int:
    args = _parse_args()

    from app.services.investment_engine.config import InvestmentEngineConfig
    from app.services.investment_engine.replay.engine import run_full_replay
    from app.services.investment_engine.replay.fixtures import DEFAULT_REPLAY_END, WEB_APP_LAUNCH
    from app.services.investment_engine.replay.report import write_all_artifacts

    if args.source == "postgres":
        dsn = args.dsn or os.getenv("PLAYER_DATABASE_URL") or os.getenv("DATABASE_URL")
        if not dsn:
            log.error("--source postgres requires --dsn or PLAYER_DATABASE_URL/DATABASE_URL")
            return 1
        provider = asyncio.run(_load_postgres_provider(
            dsn, exclude_low_quality=not args.include_low_quality_cards,
            calibration_threshold_pct=args.calibration_threshold_pct,
        ))
        # Default to the real launch window (Web App Launch -> Full
        # Release +21), NOT every day the table has - a bare run against
        # this table otherwise silently walks the WHOLE season (the
        # table keeps accumulating price history well past launch),
        # which answers a different, much bigger question than "how did
        # the engine handle the launch". --full-history opts back in.
        start = args.start
        end = args.end
        if not args.full_history:
            start = start or WEB_APP_LAUNCH
            end = end or DEFAULT_REPLAY_END
    else:
        from app.services.investment_engine.replay.fixtures import build_synthetic_fixture
        log.warning(
            "Using the SYNTHETIC fixture - this is NOT the real FC26 dataset. "
            "Re-run with --source postgres against a database holding the real "
            "fc26_historical_daily_prices/futgg_players tables for a genuine validation run."
        )
        provider = build_synthetic_fixture(n_cards=args.n_cards, seed=args.seed)
        start, end = args.start, args.end

    log.info("Loaded provider label=%r cards=%d dates=%d..%d",
              provider.label, len(provider.all_card_ids),
              provider.all_dates[0].toordinal() if provider.all_dates else 0,
              provider.all_dates[-1].toordinal() if provider.all_dates else 0)
    if start or end:
        log.info("Replay window: %s .. %s", start or "(earliest)", end or "(latest)")

    config = InvestmentEngineConfig()
    result = run_full_replay(provider, config=config, start=start, end=end)

    log.info("Replay complete: %d days walked, %d signals opened, %d closed",
              len(result.daily_states), len(result.ledger.all_signals()), len(result.ledger.closed_signals()))

    out_dir = Path(args.out_dir)
    paths = write_all_artifacts(result, out_dir)
    for name, path in paths.items():
        log.info("wrote %s -> %s", name, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
