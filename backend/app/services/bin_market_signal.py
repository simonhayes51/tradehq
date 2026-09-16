# app/services/bin_market_signal.py
"""
Market-state estimation from bulk BIN history.

WHY THIS EXISTS
---------------
futgg_bulk_price_sync.py already writes a lowest-BIN observation for the
entire ~10k catalogue every FUTGG_BULK_INTERVAL_SECONDS (default 300s)
from a single page load. That stream is effectively free. The sales
stream is not: futgg_sales_sync.py drives a headed Chromium navigation
per card, and it is what the hosting bill is actually being spent on.

So the cheap stream has to carry the monitoring, and the expensive one
has to become a validation step that a card must earn. This module is the
first half of that: everything that can honestly be said about a card's
market from asking prices alone.

WHAT A BIN IS, AND IS NOT
-------------------------
A BIN is an ASKING price. It is not proof that anything sold. Nothing in
this module returns a "fair value", and none of its outputs may be
substituted for the sales-derived anchor in futgg_intelligence.py - see
the EvidenceType enum in sales_validation_gate.py for how the two are
kept distinct downstream. What this module produces is a hypothesis
("this looks like a card whose asking price has moved") that is only ever
worth acting on after transaction evidence agrees.

The current FC26 market makes that distinction concrete rather than
academic: it is thin, and it is full of cards parked at absurd asks with
no trading behind them - a lowest BIN of 200,000 on a card nothing has
bought in days. Treating those asks as truth is precisely the failure
mode this module has to avoid, and it is why every statistic here is
robust rather than a mean.

ROBUSTNESS
----------
Every central estimate is a median and every dispersion estimate is
MAD-based. The requirement is that

    42000, 42000, 41750, 42250, 200000, 42000

produces a market level of 42,000 and flags the 200,000 as an anomaly -
not a mean of ~68,000 that no participant would recognise. A single
absurd observation must never move the baseline, because a baseline that
can be moved by one observation is a baseline an anomaly detector will
then compare against, and the whole chain becomes self-poisoning.

This is market-state estimation, not forecasting. There is no model here
and there should not be one.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.game_cycle import active_game_year

# =============================================================================
# Market states
# =============================================================================
STABLE = "STABLE"
MOVING_UP = "MOVING_UP"
MOVING_DOWN = "MOVING_DOWN"
VOLATILE = "VOLATILE"
POSSIBLE_UNDERCUT = "POSSIBLE_UNDERCUT"
POSSIBLE_SPIKE = "POSSIBLE_SPIKE"
INSUFFICIENT_BIN_HISTORY = "INSUFFICIENT_BIN_HISTORY"

MARKET_STATES = (
    STABLE, MOVING_UP, MOVING_DOWN, VOLATILE,
    POSSIBLE_UNDERCUT, POSSIBLE_SPIKE, INSUFFICIENT_BIN_HISTORY,
)

#: Plain-English per state. The operator-facing report renders these, so a
#: raw token never has to be interpreted from memory.
STATE_DESCRIPTIONS: Dict[str, str] = {
    STABLE: "Asking prices have held a consistent level.",
    MOVING_UP: "Asking prices have established a higher level and held it.",
    MOVING_DOWN: "Asking prices have established a lower level and held it.",
    VOLATILE: "Asking prices are swinging widely with no settled level.",
    POSSIBLE_UNDERCUT: "The current ask sits materially below a level that has otherwise held.",
    POSSIBLE_SPIKE: "The current ask sits materially above a level that has otherwise held.",
    INSUFFICIENT_BIN_HISTORY: "Not enough BIN observations to say anything about this market.",
}

#: MAD -> standard-deviation-equivalent for a normal distribution. Used
#: only to put the anomaly score on a familiar scale; nothing here assumes
#: the underlying distribution actually is normal.
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class BinMarketConfig:
    """Thresholds for BIN-derived market-state estimation.

    Every one of these is a documented guess, exactly as futgg_config.py's
    are. They are deliberately conservative: this module gates spending
    real money on sales requests, so the cost of being too twitchy is a
    bill, and the cost of being too quiet is a missed validation that the
    discovery sample (see sales_validation_gate.py) is there to measure.
    """

    version: str = "bin-signal-1.0"

    # ---- Sample sufficiency -------------------------------------------
    #: Below this many observations in the 24h window nothing is claimed
    #: at all. At a 5-minute bulk cadence 24h yields ~288 observations, so
    #: this is a very low bar that only excludes genuinely broken coverage.
    min_observations_for_signal: int = 6
    #: A baseline this thin can still be reported, but may not by itself
    #: justify spending a sales request (see the gate's own checks).
    min_observations_for_anomaly: int = 12

    # ---- Movement thresholds ------------------------------------------
    #: Both must be cleared. A percentage alone fires constantly on cheap
    #: cards (200 coins on a 1,000-coin card is 20%); an absolute alone
    #: never fires on expensive ones.
    min_move_pct: float = 0.06
    min_move_coins: int = 750

    #: An undercut/spike has to be further out than ordinary movement,
    #: because it is a claim about a single current observation rather
    #: than about an established level.
    undercut_pct: float = 0.08
    spike_pct: float = 0.12

    # ---- Robust anomaly ------------------------------------------------
    #: |current - median| / (1.4826 * MAD). Above this the current ask is
    #: not explainable as ordinary noise around the recent level.
    anomaly_sigma: float = 4.0
    #: MAD collapses to 0 on a perfectly flat series, which would make
    #: every trivial deviation infinitely anomalous. Floor the scale at a
    #: fraction of the level itself.
    min_dispersion_floor_pct: float = 0.005

    # ---- Volatility ----------------------------------------------------
    #: MAD/median. Above this the card has no settled level to undercut,
    #: so undercut/spike claims are suppressed in favour of VOLATILE.
    volatile_dispersion: float = 0.10

    # ---- Persistence ---------------------------------------------------
    #: Fraction of RECENT (1h) observations that must sit within
    #: persistence_band_pct of the current level for it to count as "held"
    #: rather than a single print.
    persistence_band_pct: float = 0.03
    min_persistence_for_established_move: float = 0.60
    #: Used when the 1h window is too thin to measure against (slow bulk
    #: cadence, or a coverage gap). Positional rather than time-based so
    #: the measure does not silently change meaning with the cadence.
    fallback_recent_observations: int = 12
    #: An undercut is interesting precisely because it has NOT persisted -
    #: it is one cheap listing against a level that held without it. Above
    #: this the cheap price IS the market now and there is nothing to
    #: validate: paying for sales evidence to confirm an "undercut" against
    #: a level that no longer exists is exactly the wasted request this
    #: whole module is here to stop.
    max_persistence_for_undercut: float = 0.40

    # ---- Thin-market handling ------------------------------------------
    #: FC26 is full of cards parked at an ask nothing trades at. Where the
    #: 7d level and the 24h level disagree by more than this the card is
    #: treated as thin: still reported, but anomalies from it are marked
    #: low quality so the gate can demand more before paying.
    thin_market_7d_divergence: float = 0.35

    def as_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


BIN_MARKET_CONFIG = BinMarketConfig()


@dataclass
class BinMarketSignal:
    """Everything derivable about a card's market from asking prices.

    Fields are Optional wherever the data may not support them: a card
    with four observations genuinely has no 7d median, and reporting a
    fabricated one is how a monitoring layer starts lying.
    """

    card_id: int
    as_of: datetime

    current_bin: Optional[int] = None
    observed_at: Optional[datetime] = None

    median_30m: Optional[float] = None
    median_1h: Optional[float] = None
    median_6h: Optional[float] = None
    median_24h: Optional[float] = None
    median_7d: Optional[float] = None

    change_1h_pct: Optional[float] = None
    change_24h_pct: Optional[float] = None

    volatility_1h: Optional[float] = None
    volatility_24h: Optional[float] = None

    observation_count_1h: int = 0
    observation_count_24h: int = 0
    observation_count_7d: int = 0

    #: Fraction of RECENT (1h) observations within persistence_band_pct of
    #: the current BIN. High = the current price is the market now; low =
    #: the current price is an outlier against a level that held without
    #: it. Windowed to 1h rather than 24h on purpose - see the computation
    #: for why a 24h measure cannot distinguish those two cases.
    price_persistence: Optional[float] = None

    #: Robust z-score of the current BIN against the 24h level.
    anomaly_score: Optional[float] = None

    market_state: str = INSUFFICIENT_BIN_HISTORY

    #: True when the 7d and 24h levels disagree badly enough that the
    #: card's "market" is probably a parked ask rather than trading.
    thin_market: bool = False

    #: Machine-readable notes; the shadow report groups on these.
    notes: List[str] = field(default_factory=list)

    config_version: str = BIN_MARKET_CONFIG.version

    @property
    def baseline(self) -> Optional[float]:
        """The level the current ask should be judged against.

        24h preferred, falling back to progressively shorter windows. Not
        7d: a week-old level on a market that has genuinely re-rated is
        exactly the stale anchor that produced bad advice from the sales
        side, and repeating the mistake with asking prices would be worse,
        not better.
        """
        for value in (self.median_24h, self.median_6h, self.median_1h, self.median_30m):
            if value is not None and value > 0:
                return value
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "card_id": self.card_id,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "current_bin": self.current_bin,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "median_30m": self.median_30m, "median_1h": self.median_1h,
            "median_6h": self.median_6h, "median_24h": self.median_24h,
            "median_7d": self.median_7d,
            "change_1h_pct": self.change_1h_pct, "change_24h_pct": self.change_24h_pct,
            "volatility_1h": self.volatility_1h, "volatility_24h": self.volatility_24h,
            "observation_count_1h": self.observation_count_1h,
            "observation_count_24h": self.observation_count_24h,
            "observation_count_7d": self.observation_count_7d,
            "price_persistence": self.price_persistence,
            "anomaly_score": self.anomaly_score,
            "market_state": self.market_state,
            "thin_market": self.thin_market,
            "notes": list(self.notes),
            "config_version": self.config_version,
        }


# =============================================================================
# Robust primitives
# =============================================================================
def _median(values: Sequence[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _mad(values: Sequence[float], centre: Optional[float] = None) -> Optional[float]:
    """Median absolute deviation. Immune to a minority of absurd values in
    a way stddev is not - which is the entire reason it is used here."""
    if not values:
        return None
    mid = centre if centre is not None else statistics.median(values)
    return float(statistics.median([abs(v - mid) for v in values]))


def _dispersion(values: Sequence[float]) -> Optional[float]:
    """MAD/median - a robust coefficient of variation. None when there is
    no meaningful level to divide by."""
    if len(values) < 2:
        return None
    mid = statistics.median(values)
    if mid <= 0:
        return None
    mad = _mad(values, mid)
    return None if mad is None else round(mad / mid, 4)


def _window(
    observations: Sequence[Tuple[int, datetime]], as_of: datetime, delta: timedelta,
) -> List[float]:
    cutoff = as_of - delta
    return [float(price) for price, at in observations if at >= cutoff and price > 0]


def _pct_change(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline is None or baseline <= 0:
        return None
    return round((current - baseline) / baseline, 4)


def _clears_move_thresholds(
    current: float, baseline: float, config: BinMarketConfig,
) -> bool:
    """Both the relative AND absolute floors, deliberately.

    Either alone produces a cost explosion at one end of the price range:
    a 6% rule fires on every 60-coin wobble of a 1,000-coin card, and a
    750-coin rule fires on every rounding error of a 400,000-coin card.
    """
    move = abs(current - baseline)
    return move >= config.min_move_coins and (move / baseline) >= config.min_move_pct


# =============================================================================
# Signal computation
# =============================================================================
def compute_bin_market_signal(
    card_id: int,
    observations: Sequence[Tuple[int, datetime]],
    *,
    as_of: Optional[datetime] = None,
    config: BinMarketConfig = BIN_MARKET_CONFIG,
) -> BinMarketSignal:
    """Pure: (card_id, [(lowest_bin, captured_at), ...]) -> BinMarketSignal.

    `observations` need not be sorted. No I/O - load_bin_observations()
    below does the reading, so this whole function is unit-testable
    against constructed series, including the pathological ones that
    actually matter.
    """
    as_of = as_of or datetime.now(timezone.utc)
    signal = BinMarketSignal(card_id=card_id, as_of=as_of, config_version=config.version)

    clean: List[Tuple[int, datetime]] = []
    for price, at in observations or []:
        if price is None or at is None or int(price) <= 0:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        clean.append((int(price), at))
    if not clean:
        signal.notes.append("no_bin_observations")
        return signal

    clean.sort(key=lambda pair: pair[1])
    signal.current_bin, signal.observed_at = clean[-1]

    w30m = _window(clean, as_of, timedelta(minutes=30))
    w1h = _window(clean, as_of, timedelta(hours=1))
    w6h = _window(clean, as_of, timedelta(hours=6))
    w24h = _window(clean, as_of, timedelta(hours=24))
    w7d = _window(clean, as_of, timedelta(days=7))

    signal.median_30m = _median(w30m)
    signal.median_1h = _median(w1h)
    signal.median_6h = _median(w6h)
    signal.median_24h = _median(w24h)
    signal.median_7d = _median(w7d)

    signal.observation_count_1h = len(w1h)
    signal.observation_count_24h = len(w24h)
    signal.observation_count_7d = len(w7d)

    signal.volatility_1h = _dispersion(w1h)
    signal.volatility_24h = _dispersion(w24h)

    current = float(signal.current_bin)
    signal.change_1h_pct = _pct_change(current, signal.median_1h)
    signal.change_24h_pct = _pct_change(current, signal.median_24h)

    if signal.observation_count_24h < config.min_observations_for_signal:
        signal.market_state = INSUFFICIENT_BIN_HISTORY
        signal.notes.append("too_few_observations")
        return signal

    # ---- Persistence --------------------------------------------------
    # Measured against the CURRENT price over the RECENT window, and both
    # halves of that matter.
    #
    # Current-anchored, because the question an undercut asks is "is this
    # cheap print a one-off, or has the market moved down to it", and only
    # a current-anchored measure answers it.
    #
    # Recent-windowed, because over 24h the measure cannot separate the
    # two cases at all: a median absorbs whatever the majority of the
    # window is, so once a move has been in place for a few hours the
    # current price and the baseline converge and NOTHING is detectable.
    # Against the last hour the distinction is sharp - a lone cheap print
    # scores ~1/13 at a 5-minute cadence, an hour-old new level scores 1.0.
    # When the 1h window is too thin to be meaningful - a slower bulk
    # cadence, or a gap in coverage - fall back to the most recent N
    # observations by position rather than to the 24h window. Falling back
    # to 24h would reintroduce precisely the absorption problem this
    # measure exists to avoid, and it does so silently: a genuine
    # established move gets scored as a lone print and is then paid for as
    # an "undercut" against a level that no longer exists.
    if len(w1h) >= 3:
        persistence_window = w1h
    else:
        recent_n = min(len(clean), config.fallback_recent_observations)
        persistence_window = [float(p) for p, _ in clean[-recent_n:] if p > 0]
    band = current * config.persistence_band_pct
    within = sum(1 for price in persistence_window if abs(price - current) <= band)
    signal.price_persistence = (
        round(within / len(persistence_window), 4) if persistence_window else None
    )

    # ---- Robust anomaly score -----------------------------------------
    baseline = signal.baseline
    if baseline and baseline > 0:
        mad = _mad(w24h, baseline) or 0.0
        scale = max(mad * _MAD_TO_SIGMA, baseline * config.min_dispersion_floor_pct)
        if scale > 0:
            signal.anomaly_score = round(abs(current - baseline) / scale, 3)

    # ---- Thin-market detection ----------------------------------------
    # A card whose weekly and daily levels disagree wildly is usually one
    # nobody is trading - the ask has drifted because a listing expired,
    # not because value changed. Reported, never silently trusted.
    if signal.median_7d and signal.median_24h and signal.median_7d > 0:
        divergence = abs(signal.median_24h - signal.median_7d) / signal.median_7d
        if divergence >= config.thin_market_7d_divergence:
            signal.thin_market = True
            signal.notes.append("thin_market_7d_divergence")

    signal.market_state = _classify(signal, config)
    return signal


def _classify(signal: BinMarketSignal, config: BinMarketConfig) -> str:
    """Ordered, first-match-wins - the same explicit-rule approach
    priority_engine.py uses, for the same reason: every state has to be
    explainable after the fact from stored fields alone."""
    baseline = signal.baseline
    current = float(signal.current_bin or 0)
    if not baseline or current <= 0:
        return INSUFFICIENT_BIN_HISTORY

    volatility = signal.volatility_24h
    persistence = signal.price_persistence or 0.0
    moved = _clears_move_thresholds(current, baseline, config)
    delta_pct = (current - baseline) / baseline

    # Volatility first: on a card with no settled level, "undercut" is
    # meaningless - there is nothing to be under. Claiming one here is how
    # a detector generates endless expensive validations of pure noise.
    if volatility is not None and volatility >= config.volatile_dispersion:
        signal.notes.append("dispersion_above_volatile_threshold")
        return VOLATILE

    enough_for_anomaly = signal.observation_count_24h >= config.min_observations_for_anomaly
    anomalous = (signal.anomaly_score or 0.0) >= config.anomaly_sigma

    if enough_for_anomaly and moved and anomalous:
        # An undercut is a CHEAP OUTLIER against a level that held without
        # it. If the new price has itself persisted, the market moved - a
        # different, less urgent thing, handled below.
        if delta_pct <= -config.undercut_pct and persistence <= config.max_persistence_for_undercut:
            signal.notes.append("current_below_persistent_baseline")
            return POSSIBLE_UNDERCUT
        if delta_pct >= config.spike_pct and persistence <= config.max_persistence_for_undercut:
            signal.notes.append("current_above_persistent_baseline")
            return POSSIBLE_SPIKE

    # An established move: the new level is where most recent observations
    # actually sit, so this is a re-rating rather than a single odd print.
    if moved and persistence >= config.min_persistence_for_established_move:
        signal.notes.append("move_established_by_persistence")
        return MOVING_UP if delta_pct > 0 else MOVING_DOWN

    return STABLE


# =============================================================================
# I/O
# =============================================================================
#: One query, one round trip, bounded per card by the LATERAL - the same
#: shape market_data_provider.get_sales_by_ids() uses, and for the same
#: reason: a per-card query across a 10k catalogue is 10k round trips.
#:
#: Ordered DESC and capped so a card whose bulk observations are dense
#: (288/day at the default cadence, ~2,000 over 7 days) cannot drag the
#: whole scan. The cap is applied newest-first so the recent windows -
#: the ones every threshold actually reads - are always complete.
_OBSERVATIONS_QUERY = """
    SELECT h.source_card_id, h.lowest_bin, h.captured_at
    FROM unnest($1::bigint[]) AS ids(card_id)
    CROSS JOIN LATERAL (
        SELECT source_card_id, lowest_bin, captured_at
        FROM futgg_bin_history
        WHERE source_card_id = ids.card_id
          AND captured_at >= now() - make_interval(days => $2)
          -- A source_card_id can collide across game years (e.g. an FC26
          -- and FC27 card sharing the same numeric id) and platform column
          -- carries console vs PC feeds separately. Without these two
          -- filters this batch loader silently mixed an unrelated card's
          -- (or platform's) history into evaluate_card()'s fair-value
          -- computation for the card actually being scored.
          AND game_year = $4
          AND platform = $5
        ORDER BY captured_at DESC
        LIMIT $3
    ) h
"""

#: 7 days at a 5-minute cadence is ~2,016 rows/card. 2,500 leaves headroom
#: for a faster cadence without silently truncating the 7d window.
MAX_OBSERVATIONS_PER_CARD = 2500
OBSERVATION_WINDOW_DAYS = 7


async def load_bin_observations(
    pool,
    card_ids: Sequence[int],
    *,
    days: int = OBSERVATION_WINDOW_DAYS,
    limit_per_card: int = MAX_OBSERVATIONS_PER_CARD,
) -> Dict[int, List[Tuple[int, datetime]]]:
    """Batch-load BIN observations keyed by card id."""
    ids = [int(c) for c in card_ids]
    if not ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _OBSERVATIONS_QUERY, ids, int(days), int(limit_per_card),
            # Pinned to console, mirroring futgg_market_snapshot (migration
            # 094/095) - the live product is console-priced; PC capture is
            # not yet live, so pinning here keeps this consistent rather
            # than silently picking whichever platform happened to write
            # last.
            active_game_year(), "ps5",
        )
    out: Dict[int, List[Tuple[int, datetime]]] = {}
    for row in rows:
        out.setdefault(int(row["source_card_id"]), []).append(
            (int(row["lowest_bin"]), row["captured_at"])
        )
    return out


async def compute_signals_for_cards(
    pool,
    card_ids: Sequence[int],
    *,
    as_of: Optional[datetime] = None,
    config: BinMarketConfig = BIN_MARKET_CONFIG,
) -> Dict[int, BinMarketSignal]:
    """Convenience wrapper: load + compute for a batch of cards."""
    as_of = as_of or datetime.now(timezone.utc)
    observations = await load_bin_observations(pool, card_ids)
    return {
        int(card_id): compute_bin_market_signal(
            int(card_id), observations.get(int(card_id), []), as_of=as_of, config=config,
        )
        for card_id in card_ids
    }
