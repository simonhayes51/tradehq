# app/services/investment_engine/replay/fixtures.py
"""
Two ways to get a ReplayDataProvider:

  load_from_postgres_rows() - the PRODUCTION path. Feed it the real rows
      from `fc26_historical_daily_prices` JOINed to `futgg_players`
      (query shape identical to backtest.py's existing run_backtest()),
      and it returns the same ReplayDataProvider the synthetic path
      below does. Use this in any environment that has DATABASE_URL/
      PLAYER_DATABASE_URL pointed at the database holding the real,
      reconstructed ~2,617-card/~720k-row FC26 dataset (see
      research/fc26_launch_market and auto_sync/research_fc26_historical_
      loader.py for how that table was populated).

  build_synthetic_fixture() - a CLEARLY LABELLED STAND-IN, used only
      because this development session has no live database connection
      (no DATABASE_URL, no asyncpg/psycopg2 installed, no local export of
      the real table anywhere in this repo - confirmed by direct
      inspection before writing this module). It is NOT the real FC26
      dataset and must never be reported as if it were. It exists solely
      to let the replay harness itself be built, exercised, and checked
      for genuine implementation bugs (leakage, bootstrap behaviour,
      idempotency, ledger correctness) before the real run happens in an
      environment with DB access. Deterministic (fixed seed) so repeated
      runs are directly comparable.

Both return the exact same ReplayDataProvider shape, so engine.py never
knows or cares which one produced its input.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from app.services.investment_engine.cohorts import POSITION_GROUPS
from app.services.investment_engine.replay.data_provider import CardMeta, ReplayDataProvider

# The real, previously-established FC26 launch calendar (see
# auto_sync/research_fc26_phase8_investment_analysis.py) - used here ONLY
# as calendar labels for FR-offset reporting, never as a threshold inside
# any engine decision.
WEB_APP_LAUNCH = date(2025, 9, 17)
EARLY_ACCESS = date(2025, 9, 19)
FULL_RELEASE = date(2025, 9, 26)
DEFAULT_REPLAY_END = FULL_RELEASE + timedelta(days=21)  # FR+21, the preferred upper bound

SYNTHETIC_FIXTURE_LABEL = (
    "SYNTHETIC FIXTURE (not real FC26 data - built because no live "
    "database connection was available in this session; see fixtures.py "
    "module docstring)"
)


def load_from_postgres_rows(
    price_rows, meta_rows, *, label: str = "fc26_historical_daily_prices (production database)",
) -> ReplayDataProvider:
    """`price_rows`: iterable of (card_id, observed_date, price).
    `meta_rows`: iterable of (source_card_id, name, primary_position).
    Matches the real query shape:

        SELECT card_id, observed_date, price FROM fc26_historical_daily_prices
        SELECT source_card_id, name, primary_position FROM futgg_players
            WHERE price_tier IN ('gold_common', 'gold_rare')
    """
    prices: Dict[int, Dict[date, float]] = {}
    for card_id, observed_date, price in price_rows:
        prices.setdefault(card_id, {})[observed_date] = float(price)

    meta: Dict[int, CardMeta] = {}
    for source_card_id, name, primary_position in meta_rows:
        if source_card_id in prices:
            meta[source_card_id] = CardMeta(card_id=source_card_id, name=name, primary_position=primary_position)

    # Cards with prices but no matching (filtered) metadata row are
    # dropped - mirrors backtest.py's existing `if cid in meta` filter.
    prices = {cid: series for cid, series in prices.items() if cid in meta}
    return ReplayDataProvider(prices=prices, meta=meta, label=label)


# ---------------------------------------------------------------------
# Synthetic fixture - archetypes shaped after the FC26 Phase 8/8B/8C
# qualitative findings (pump+crash+decelerate+short-rebound for premium;
# steady early climb with no crash for desirable starters; flat/noisy
# for ordinary/fodder). Magnitudes are randomised per card, not copied
# from the research's specific numbers - the point is to exercise the
# engine's SHAPE-sensitive logic (drawdown/deceleration/state machine),
# not to hand it the exact answer.
# ---------------------------------------------------------------------

ARCHETYPE_WEIGHTS = {
    "premium_meta": 0.10,
    "mid_tier_meta": 0.20,
    "desirable_starter": 0.25,
    "ordinary": 0.25,
    "likely_fodder": 0.20,
}


def _series_for_archetype(rng: random.Random, archetype: str, n_days: int, base_price: float) -> List[float]:
    prices = [base_price]
    if archetype == "premium_meta":
        # Early-Access pump (days 0-5), deep decelerating crash (6-16),
        # short partial rebound (17-20), fade (21+).
        pump_days = min(5, n_days - 1)
        for i in range(1, pump_days + 1):
            prices.append(prices[-1] * (1 + rng.uniform(0.05, 0.14)))
        crash_days = min(11, max(0, n_days - 1 - pump_days))
        # Decelerating negative returns: start harsh, ease off.
        for i in range(crash_days):
            severity = max(0.02, 0.13 * (1 - i / max(crash_days - 1, 1)))
            prices.append(prices[-1] * (1 - severity - rng.uniform(-0.01, 0.02)))
        remaining = n_days - len(prices)
        for i in range(max(0, remaining)):
            if i < remaining * 0.4:
                prices.append(prices[-1] * (1 + rng.uniform(0.0, 0.05)))  # partial rebound
            else:
                prices.append(prices[-1] * (1 + rng.uniform(-0.04, 0.01)))  # fade
    elif archetype == "mid_tier_meta":
        pump_days = min(3, n_days - 1)
        for i in range(1, pump_days + 1):
            prices.append(prices[-1] * (1 + rng.uniform(0.02, 0.08)))
        crash_days = min(9, max(0, n_days - 1 - pump_days))
        for i in range(crash_days):
            severity = max(0.01, 0.08 * (1 - i / max(crash_days - 1, 1)))
            prices.append(prices[-1] * (1 - severity - rng.uniform(-0.01, 0.015)))
        remaining = n_days - len(prices)
        for i in range(max(0, remaining)):
            prices.append(prices[-1] * (1 + rng.uniform(-0.02, 0.03)))
    elif archetype == "desirable_starter":
        # Early steady climb, no deep crash - FC26's finding for this cohort.
        for i in range(1, n_days):
            drift = 0.015 if i < n_days * 0.5 else 0.0
            prices.append(prices[-1] * (1 + drift + rng.uniform(-0.02, 0.025)))
    elif archetype == "ordinary":
        for i in range(1, n_days):
            prices.append(prices[-1] * (1 + rng.uniform(-0.02, 0.02)))
    else:  # likely_fodder
        for i in range(1, n_days):
            prices.append(prices[-1] * (1 + rng.uniform(-0.015, 0.01)))
    return [max(150.0, p) for p in prices]


def build_synthetic_fixture(
    *,
    seed: int = 20260821,
    n_cards: int = 320,
    start: date = WEB_APP_LAUNCH,
    end: date = DEFAULT_REPLAY_END,
    stagger_fraction: float = 0.15,
    stagger_max_days: int = 8,
) -> ReplayDataProvider:
    """Deterministic synthetic FC26-shaped fixture. See module docstring
    - this is explicitly NOT the real dataset."""
    rng = random.Random(seed)
    n_days = (end - start).days + 1
    position_codes = sorted(set(POSITION_GROUPS.keys()))

    archetypes = list(ARCHETYPE_WEIGHTS.keys())
    weights = list(ARCHETYPE_WEIGHTS.values())

    prices: Dict[int, Dict[date, float]] = {}
    meta: Dict[int, CardMeta] = {}

    for card_id in range(1, n_cards + 1):
        position = position_codes[card_id % len(position_codes)]
        archetype = rng.choices(archetypes, weights=weights, k=1)[0]
        base_price = {
            "premium_meta": rng.uniform(1_200_000, 4_500_000),
            "mid_tier_meta": rng.uniform(150_000, 700_000),
            "desirable_starter": rng.uniform(15_000, 60_000),
            "ordinary": rng.uniform(2_000, 12_000),
            "likely_fodder": rng.uniform(200, 1_500),
        }[archetype]

        # Bootstrap realism: most cards appear day 0, a minority arrive
        # over the following days (late scrape coverage / newly captured
        # cards) - exercises the cohort-bootstrap requirement directly.
        if rng.random() < stagger_fraction:
            offset = rng.randint(1, stagger_max_days)
        else:
            offset = 0
        first_date = start + timedelta(days=offset)
        card_days = n_days - offset
        if card_days < 2:
            continue

        series_prices = _series_for_archetype(rng, archetype, card_days, base_price)
        series = {first_date + timedelta(days=i): round(p, 0) for i, p in enumerate(series_prices)}
        prices[card_id] = series
        meta[card_id] = CardMeta(card_id=card_id, name=f"Synthetic Card {card_id} ({archetype})", primary_position=position)

    return ReplayDataProvider(prices=prices, meta=meta, label=SYNTHETIC_FIXTURE_LABEL)
