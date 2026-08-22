# app/services/market_event_reconstruction.py
"""
Reconstructs the content-event timeline (content_release_events / content_release_event_cards,
migration 064) from FUT.GG card release timestamps.

Pure-core / thin-IO split, matching the investment_engine package's own
convention: `group_release_events()` is a pure function over card rows so
the grouping rules are directly unit-testable with no database, and
`reconstruct_events()` is the only part that talks to Postgres.

DERIVED FROM released_at, NEVER first_seen_at. first_seen_at records when
our scraper noticed a card - for historical FC26 cards that can be weeks
after the real release, which would produce a fictional timeline.

GROUPING: (game_year, squad, release CLUSTER). Rarity is the promo FAMILY
("Team of the Week"); squad is the specific release ("TOTW 27"). Grouping
by rarity would collapse a whole season of weekly TOTW squads into one
meaningless event.

The cluster - rather than an exact timestamp match - is what real data
forced. FUT.GG's own ingestion stamps a single promo drop across several
adjacent minutes: a live FUTTIES 2 sample landed cards at 17:01, 17:02
and 17:03 UTC. Exact-equality grouping shattered that one drop into three
events, two of which were then mislabelled "mini" because each fragment
held only part of the squad.

So cards in the same squad within RELEASE_CLUSTER_TOLERANCE of the
previous card form ONE event, stamped with the EARLIEST timestamp in the
cluster (the actual drop moment). A card genuinely added to a promo days
later - or a later wave of a multi-wave promo like TOTY - falls outside
the tolerance and forms its own separate event, which is what happened in
the market and must stay visible.

MAX_CLUSTER_SPAN additionally caps a cluster's total span. It is purely
defensive and does not fire on FC26 data; see its own comment.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("market_event_reconstruction")

EVENT_PROMO_RELEASE = "promo_release"
EVENT_TOTW_RELEASE = "totw_release"

SCALE_BROAD = "broad"
SCALE_MINI = "mini"
SCALE_ISOLATED = "isolated"

# Card-count boundaries between an isolated release, a small batch, and a
# full promo team. A real FUT promo team is typically well over a dozen
# cards; a single card carrying a squad label is almost always an
# SBC/objective rather than a promo.
MINI_RELEASE_MAX_CARDS = 5
BROAD_RELEASE_MIN_CARDS = 6

# Rarities that are weekly squad content rather than a promo. Kept
# deliberately small - the instruction was not to over-classify, so
# anything not recognised here is simply 'promo_release'.
_TOTW_RARITY_PATTERN = re.compile(r"team of the week|\btotw\b", re.IGNORECASE)

# How far apart two cards in the same squad can be and still count as one
# release. Sized from observed FUT.GG behaviour: a single drop is stamped
# across a few adjacent minutes, and a slow import could plausibly take
# longer - but a card genuinely added to a promo later arrives DAYS
# afterwards, so hours cleanly separates the two cases.
#
# Measured as the gap from the PREVIOUS card, not from the cluster start,
# so a drop that trickles in over a while still groups.
RELEASE_CLUSTER_TOLERANCE = timedelta(hours=6)

# Hard cap on how long one cluster may span in total.
#
# DEFENSIVE, not corrective: as of FC26 this never fires. Gap-based
# linkage alone would chain only if cards arrived in a CONTINUOUS drip
# with every individual gap under the tolerance while the total ran past
# it (e.g. a batch every 2h for 12h). No FC26 release has that shape -
# multi-wave promos like TOTY space their waves DAYS apart, which the gap
# check already separates on its own.
#
# It is kept because the failure mode is real and silent if it ever
# happens: a chained cluster becomes one fictional multi-day "event", and
# an event study built on it would compare the wrong days. Cheap
# insurance against a shape FUT.GG could plausibly adopt.
#
# Sized against observed real drops: a single release spans seconds to
# minutes, and the slowest genuine one measured (Joga Bonito, 39 cards)
# spanned 1h47m - so 6h leaves every real drop intact.
MAX_CLUSTER_SPAN = timedelta(hours=6)


@dataclass(frozen=True)
class CardRelease:
    """One card's release facts - the only inputs event grouping needs."""
    source_card_id: int
    source_player_id: Optional[int]
    game_year: int
    rarity: Optional[str]
    squad: Optional[str]
    released_at: Optional[datetime]


@dataclass
class DerivedEvent:
    game_year: int
    event_type: str
    event_key: str
    title: str
    rarity: Optional[str]
    squad: Optional[str]
    released_at: datetime
    # How long the cluster's timestamps spanned, in seconds. Recorded so
    # the FUT.GG ingestion jitter this clustering exists to absorb stays
    # visible and auditable rather than being silently smoothed away.
    span_seconds: int = 0
    card_ids: List[Tuple[int, Optional[int]]] = field(default_factory=list)

    @property
    def card_count(self) -> int:
        return len(self.card_ids)

    @property
    def event_scale(self) -> str:
        if self.card_count >= BROAD_RELEASE_MIN_CARDS:
            return SCALE_BROAD
        if self.card_count > 1:
            return SCALE_MINI
        return SCALE_ISOLATED

    @property
    def confidence(self) -> str:
        """Confidence that this is a real, coherent content EVENT - not
        confidence about any market effect. A broad batch landing in one
        tight time cluster is strong evidence of a single release; a lone
        card carrying a squad label is weak."""
        if self.event_scale == SCALE_BROAD:
            return "high"
        if self.event_scale == SCALE_MINI:
            return "medium"
        return "low"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def classify_event_type(rarity: Optional[str]) -> str:
    """Deliberately minimal. Only TOTW is separated out, because weekly
    squad content behaves differently from a promo drop and is trivially
    identifiable from its rarity. Everything else is `promo_release` -
    over-classifying without evidence would bake guesses into the data."""
    if rarity and _TOTW_RARITY_PATTERN.search(rarity):
        return EVENT_TOTW_RELEASE
    return EVENT_PROMO_RELEASE


def group_release_events(cards: Iterable[CardRelease]) -> List[DerivedEvent]:
    """Pure grouping over card rows -> events.

    Cards with no released_at are skipped entirely: an event timeline
    built on a missing timestamp would be a fabrication, and the whole
    point of this layer is that release dates are sourced, not guessed.

    Cards with no squad are also skipped. Without a squad there is no
    reliable way to tell one release from another sharing a rarity, and
    grouping every squad-less card of a rarity together would invent
    events that never happened. They remain queryable in futgg_players;
    they simply do not form events.
    """
    # Step 1: bucket by (game_year, squad). Timestamp clustering happens
    # WITHIN a squad, never across squads - two different promos dropping
    # in the same hour must stay separate events.
    by_squad: Dict[Tuple[int, str], List[CardRelease]] = {}
    for card in cards:
        if card.released_at is None or not card.squad:
            continue
        squad = card.squad.strip()
        if not squad:
            continue
        by_squad.setdefault((card.game_year, squad.casefold()), []).append(card)

    events: List[DerivedEvent] = []
    for (game_year, _squad_key), squad_cards in by_squad.items():
        squad_cards.sort(key=lambda c: c.released_at)

        # Step 2: single-linkage cluster on the gap from the PREVIOUS
        # card. FUT.GG stamps one drop across adjacent minutes, so exact
        # equality fragments a promo; a genuine later addition arrives
        # days afterwards and still separates cleanly.
        cluster: List[CardRelease] = []
        for card in squad_cards:
            if cluster:
                gap = card.released_at - cluster[-1].released_at
                span_if_added = card.released_at - cluster[0].released_at
                # Break on EITHER a long gap (a clearly separate later
                # addition) OR an over-long total span (a multi-wave
                # release chaining through the gap check - see
                # MAX_CLUSTER_SPAN).
                if gap > RELEASE_CLUSTER_TOLERANCE or span_if_added > MAX_CLUSTER_SPAN:
                    events.append(_event_from_cluster(game_year, cluster))
                    cluster = []
            cluster.append(card)
        if cluster:
            events.append(_event_from_cluster(game_year, cluster))

    return sorted(events, key=lambda e: (e.released_at, e.event_key))


def _event_from_cluster(game_year: int, cluster: List[CardRelease]) -> DerivedEvent:
    """Build one event from a time-adjacent group of same-squad cards.

    released_at is the EARLIEST timestamp in the cluster - the moment the
    drop actually began. Using the latest, or a mean, would place the
    event after part of the market had already reacted.
    """
    first = cluster[0]
    squad = (first.squad or "").strip()
    # Rarity is taken from the first card that HAS one: a cluster can
    # contain a card whose rarity failed to parse, and that must not
    # blank out the event's promo family.
    rarity = next((c.rarity for c in cluster if c.rarity), None)
    event = DerivedEvent(
        game_year=game_year,
        event_type=classify_event_type(rarity),
        event_key=_slugify(squad),
        title=squad,
        rarity=rarity,
        squad=squad,
        released_at=first.released_at,
        span_seconds=int((cluster[-1].released_at - first.released_at).total_seconds()),
    )
    event.card_ids = [(c.source_card_id, c.source_player_id) for c in cluster]
    return event


# ---------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------

_SELECT_CARDS = """
SELECT source_card_id, source_player_id, game_year, rarity, squad, released_at
FROM futgg_players
WHERE released_at IS NOT NULL
  AND squad IS NOT NULL
  {game_year_clause}
ORDER BY released_at, squad
"""

_UPSERT_EVENT = """
INSERT INTO content_release_events (
    game_year, event_type, event_key, title, rarity, squad, released_at,
    card_count, event_scale, source, confidence, metadata, updated_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'derived_futgg_release',$10,$11,now())
ON CONFLICT (game_year, event_key, released_at) DO UPDATE SET
    event_type = EXCLUDED.event_type,
    title = EXCLUDED.title,
    rarity = COALESCE(EXCLUDED.rarity, content_release_events.rarity),
    squad = COALESCE(EXCLUDED.squad, content_release_events.squad),
    card_count = EXCLUDED.card_count,
    event_scale = EXCLUDED.event_scale,
    confidence = EXCLUDED.confidence,
    metadata = EXCLUDED.metadata,
    updated_at = now()
RETURNING id
"""


async def reconstruct_events(
    pool, *, game_year: Optional[int] = None, dry_run: bool = False,
) -> Dict[str, Any]:
    """Rebuild the event timeline from current futgg_players data.

    Idempotent: re-running updates existing events in place via
    migration 064's (game_year, event_key, released_at) unique index
    rather than duplicating them, and card links are re-asserted with
    ON CONFLICT DO NOTHING. Safe to run repeatedly as the backfill
    progresses and more release timestamps become known.
    """
    clause = "AND game_year = $1" if game_year is not None else ""
    args = [game_year] if game_year is not None else []

    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_CARDS.format(game_year_clause=clause), *args)

    cards = [
        CardRelease(
            source_card_id=r["source_card_id"],
            source_player_id=r["source_player_id"],
            game_year=r["game_year"],
            rarity=r["rarity"],
            squad=r["squad"],
            released_at=r["released_at"],
        )
        for r in rows
    ]
    events = group_release_events(cards)

    summary: Dict[str, Any] = {
        "cards_considered": len(cards),
        "events_derived": len(events),
        "by_scale": {},
        "by_type": {},
        "written": 0,
        "dry_run": dry_run,
    }
    for event in events:
        summary["by_scale"][event.event_scale] = summary["by_scale"].get(event.event_scale, 0) + 1
        summary["by_type"][event.event_type] = summary["by_type"].get(event.event_type, 0) + 1

    if dry_run:
        return summary

    import json

    async with pool.acquire() as conn:
        for event in events:
            async with conn.transaction():
                event_id = await conn.fetchval(
                    _UPSERT_EVENT,
                    event.game_year, event.event_type, event.event_key, event.title,
                    event.rarity, event.squad, event.released_at,
                    event.card_count, event.event_scale, event.confidence,
                    json.dumps({
                        "derived_from": "futgg_players.released_at",
                        # The observed FUT.GG ingestion spread for this
                        # drop. A large span on a supposedly single
                        # release is worth eyeballing.
                        "release_span_seconds": event.span_seconds,
                        "cluster_tolerance_seconds": int(RELEASE_CLUSTER_TOLERANCE.total_seconds()),
                    }),
                )
                await conn.executemany(
                    "INSERT INTO content_release_event_cards (event_id, source_card_id, source_player_id) "
                    "VALUES ($1,$2,$3) ON CONFLICT (event_id, source_card_id) DO NOTHING",
                    [(event_id, cid, pid) for cid, pid in event.card_ids],
                )
                summary["written"] += 1

    return summary
