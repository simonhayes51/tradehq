"""Matches live SBC rewards to FUT.GG cards, and refreshes the watch
table that lets the buy-signal engine see when a card's live SBC is
still producing new copies of it.

WHY THIS EXISTS
----------------
A card's price on the market and a live SBC that ALSO produces the same
card are two different sources of the same item. Every SBC completion
mints another copy into a market that already has one - so a BIN sitting
below the recent-sales median can be ordinary mispricing, or it can be
supply pressure from an SBC nobody told the engine about. Confirmed on a
real card (Lamine Yamal, 99 RW, "Summer Stars Tournament Winners" SBC,
~1.7m to complete): the engine read "23% below median, broadly flat" as
a buy, while the SBC was actively flooding the market with fresh copies.

WHY THIS IS A SEPARATE TABLE, NOT A JOIN
------------------------------------------
market_events/sbc_details (migration 018) live on the PLAYER database;
futgg_players/futgg_market_snapshot (migration 038) live on the CORE
database. Different pools, different connections - no SQL JOIN can span
them. app/services/analytics_engine.py's run_pass(core_pool, player_pool)
is the existing precedent for a job that reads one and writes the other;
this follows the same shape.

The result also does NOT get joined into futgg_market_snapshot itself.
That view is DROP+CREATE on every migration (Postgres has no CREATE OR
REPLACE MATERIALIZED VIEW), and the last two times its column set
changed, a name silently missing from
app/services/market_data_provider.py's _SNAPSHOT_COLUMNS list broke the
engine in production with no error anywhere (see that constant's own
comment). Keeping this in its own small table, merged onto snapshot rows
in Python by market_data_provider._attach_sbc_reward_watch(), keeps the
match logic - inherently fuzzy, see below - entirely out of that blast
radius.

WHY THE MATCH IS NAME-BASED AND DELIBERATELY CONSERVATIVE
------------------------------------------------------------
sbc_details.reward_card_id is NULL for every row easysbc_sbc_sync.py
writes - its own module docstring says why: EA's reward item ids have no
known mapping to this codebase's card id scheme, so it declined to guess
rather than write a wrong number silently. The only signal that survives
is text: the SBC's own title/set_name (often just the player's name for
a player-specific SBC - "Lamine Yamal" in the production screenshot that
motivated this) and reward_description (EasySBC's own reward item name,
sometimes more specific).

A missed match silently leaves a card ungated - exactly the state before
this feature existed, so it is a safe failure. A WRONG match silently
downgrades a good, unrelated buy signal to a watch, which is worse and
erodes trust in every other call the engine makes. So match_sbc_reward()
never guesses: it requires an exact (case/accent/punctuation-normalised)
full-name match that is UNIQUE among the active-card candidates it is
given, and rejects generic, non-player reward text (packs, player picks,
rating-range rewards) outright rather than trying to parse a player out
of it.

This has not been run against a live EasySBC payload in this
environment (see easysbc_sbc_sync.py's own "genuinely open" section) -
the shapes handled here are inferred from that collector's own parsing
code, not confirmed against real production title/reward_description
text. Expect to tune _looks_like_generic_reward and the matching rules
once real data is seen; FUTGG_SBC_REWARD_GUARD (see futgg_config.py)
exists specifically so the downstream gate can be switched off without a
deploy if the match rate turns out to be poor.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import asyncpg

log = logging.getLogger("sbc_reward_match")

# Distinct from every other REFRESH_LOCK_KEY in this codebase - see
# app/services/held_position_refresher.py's own comment for the running
# list (fair-value 7741002, migration runner 7741003, analytics-engine
# 7741004, event-impact 7741005, recommendation-engine 7741006,
# ml-feature-pipeline snapshot 7741009, held-position-refresher 7741010,
# market_data_provider refresh 7741011, priority_recompute 7741012).
REFRESH_LOCK_KEY = 7741013

REFRESH_INTERVAL_SECONDS = int(os.environ.get("FUTGG_SBC_REWARD_REFRESH_SECONDS", str(30 * 60)))

# Reward/set text that names an item, not a specific player - matching
# these to a card would be a guess, not a read. Checked as substrings of
# the lowercased text.
_GENERIC_REWARD_MARKERS = (
    "pack", "player pick", "choice", "x1", "x3", "x5", "x10", "x25",
    "coins", "draft token", "draft entry", "evolution", "loan",
)

_RATING_RE = re.compile(r"\b(\d{2,3})\+?\b")


def normalize_player_name(text: Optional[str]) -> str:
    """Case, accent and punctuation folding for name comparison.

    NFKD decomposition strips accents (a rendering FUT.GG and EasySBC
    are not guaranteed to punctuate identically), and only
    letters/digits/spaces survive - "N'Golo Kante" and "N Golo Kante"
    compare equal, which is the point: two independently scraped copies
    of the same player name need not match byte-for-byte to be the same
    player.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = re.sub(r"[^a-z0-9 ]", " ", stripped.lower())
    return re.sub(r"\s+", " ", folded).strip()


def _looks_like_generic_reward(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _GENERIC_REWARD_MARKERS)


@dataclass(frozen=True)
class RewardMatch:
    futgg_source_card_id: int
    match_method: str  # "exact_name" | "exact_name_rating"


def match_sbc_reward(
    reward_text: str,
    candidates: Sequence[Dict[str, Any]],
) -> Optional[RewardMatch]:
    """`candidates` is a sequence of {"source_card_id", "name",
    "display_name", "rating"} rows - active futgg_players cards only (see
    refresh_sbc_reward_watch).

    Returns None whenever the match is not unique or the text does not
    look like a specific player - see the module docstring for why that
    is the safe direction to fail in.
    """
    if not reward_text or _looks_like_generic_reward(reward_text):
        return None

    target_rating_match = _RATING_RE.search(reward_text)
    target_rating = int(target_rating_match.group(1)) if target_rating_match else None
    # A rating token under 40 is never a card rating in this game - far
    # more likely picked up from something like "SBC ends in 3 days" or
    # a cost figure. Treat it as noise rather than a disambiguator.
    if target_rating is not None and target_rating < 40:
        target_rating = None

    # Strip the matched rating token out before normalising for the name
    # comparison - "Lamine Yamal 99" must compare equal to the candidate
    # name "Lamine Yamal", not fail to match because "99" is still part
    # of the string.
    name_only_text = reward_text
    if target_rating_match is not None and target_rating is not None:
        name_only_text = (
            reward_text[:target_rating_match.start()] + reward_text[target_rating_match.end():]
        )
    target_name = normalize_player_name(name_only_text)
    if not target_name:
        return None

    name_hits = [
        c for c in candidates
        if normalize_player_name(c.get("name")) == target_name
        or normalize_player_name(c.get("display_name")) == target_name
    ]
    if not name_hits:
        return None

    if len(name_hits) == 1:
        return RewardMatch(int(name_hits[0]["source_card_id"]), "exact_name")

    # Multiple cards share this name (common - most players have a base
    # gold card plus every special version). Only a rating parsed from
    # the reward text can disambiguate; without one, guessing which
    # version is the SBC's actual reward is exactly the kind of wrong
    # match this function exists to refuse.
    if target_rating is None:
        return None

    rating_hits = [c for c in name_hits if c.get("rating") == target_rating]
    if len(rating_hits) == 1:
        return RewardMatch(int(rating_hits[0]["source_card_id"]), "exact_name_rating")

    return None


async def _fetch_active_sbc_events(player_pool: asyncpg.Pool) -> List[Dict[str, Any]]:
    async with player_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id AS market_event_id, e.title, e.ends_at,
                   d.reward_description
            FROM market_events e
            LEFT JOIN sbc_details d ON d.event_id = e.id
            WHERE e.kind = 'sbc'
              AND e.ends_at IS NOT NULL
              AND e.ends_at > now()
            """
        )
    return [dict(r) for r in rows]


async def _fetch_candidate_cards(core_pool: asyncpg.Pool) -> List[Dict[str, Any]]:
    async with core_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_card_id, name, display_name, rating
            FROM futgg_players
            WHERE is_active AND name IS NOT NULL
            """
        )
    return [dict(r) for r in rows]


async def refresh_sbc_reward_watch(
    core_pool: asyncpg.Pool, player_pool: asyncpg.Pool,
) -> Dict[str, int]:
    """One pass: read live SBCs from the player DB, match rewards against
    active cards on the core DB, and make futgg_sbc_reward_watch (core
    DB) match reality - upsert current matches, remove anything that no
    longer matches a live SBC (ended, or the SBC's reward text/window
    changed since the last pass and no longer matches).

    Advisory-lock guarded like every other refresher in this codebase
    (see REFRESH_LOCK_KEY) - concurrent runs would just duplicate work,
    not corrupt anything, but there is no reason to allow it.
    """
    stats = {"events_seen": 0, "matched": 0, "unmatched": 0, "removed": 0}

    async with core_pool.acquire() as lock_conn:
        got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", REFRESH_LOCK_KEY)
        if not got_lock:
            log.info("sbc reward refresh already running elsewhere - skipping this pass")
            return stats
        try:
            events = await _fetch_active_sbc_events(player_pool)
            stats["events_seen"] = len(events)
            candidates = await _fetch_candidate_cards(core_pool)

            matches: List[Tuple[Any, ...]] = []
            for event in events:
                reward_text = event.get("reward_description") or event.get("title") or ""
                match = match_sbc_reward(reward_text, candidates)
                if match is None:
                    stats["unmatched"] += 1
                    continue
                stats["matched"] += 1
                matches.append((
                    match.futgg_source_card_id,
                    reward_text[:200],
                    match.match_method,
                    event["market_event_id"],
                    event["ends_at"],
                ))

            matched_ids = [m[0] for m in matches]
            async with core_pool.acquire() as conn, conn.transaction():
                if matches:
                    await conn.executemany(
                        """
                        INSERT INTO futgg_sbc_reward_watch (
                            futgg_source_card_id, reward_title, match_method,
                            market_event_id, sbc_ends_at, matched_at, updated_at
                        )
                        VALUES ($1, $2, $3, $4, $5, now(), now())
                        ON CONFLICT (futgg_source_card_id) DO UPDATE SET
                            reward_title = EXCLUDED.reward_title,
                            match_method = EXCLUDED.match_method,
                            market_event_id = EXCLUDED.market_event_id,
                            sbc_ends_at = EXCLUDED.sbc_ends_at,
                            updated_at = now()
                        """,
                        matches,
                    )
                removed = await conn.fetch(
                    """
                    DELETE FROM futgg_sbc_reward_watch
                    WHERE NOT (futgg_source_card_id = ANY($1::bigint[]))
                    RETURNING futgg_source_card_id
                    """,
                    matched_ids,
                )
                stats["removed"] = len(removed)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", REFRESH_LOCK_KEY)

    log.info(
        "sbc reward refresh: %d live SBC(s), %d matched, %d unmatched, %d stale row(s) removed",
        stats["events_seen"], stats["matched"], stats["unmatched"], stats["removed"],
    )
    return stats


async def refresher_loop(
    core_pool: asyncpg.Pool, player_pool: asyncpg.Pool,
    interval_seconds: int = REFRESH_INTERVAL_SECONDS,
) -> None:
    """Runs forever. Best-effort: one failed pass must never take down
    the loop - the watch table simply keeps its last-known-good state
    until the next pass succeeds, same degrade-safe shape as every other
    refresher in this codebase."""
    while True:
        try:
            await refresh_sbc_reward_watch(core_pool, player_pool)
        except Exception:
            log.warning("sbc reward refresh pass failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
