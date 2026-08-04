"""
SBC collector sourced from EasySBC's public JSON API, replacing the
futbin_sbc_sync.py (headed-Chromium) approach for this data.

futbin_sbc_sync.py is left in the repo but is confirmed NOT reliably usable
right now: FUTBIN's Cloudflare hard-blocked every network origin tested -
Railway's datacentre IP got an immediate 403 "Just a moment..." on the
listing page itself; a real home/residential connection got the listing
page fine but still 403'd on the very first detail page (a distinct WAF
message, not fixed by a correct Referer header). See that file's own
docstring and README's "SBC collector" section for the full history -
this file exists because that dead end led to checking whether an
alternative source had the same data without the same problem.

EasySBC.io's frontend is a client-rendered SPA backed by a plain,
unauthenticated JSON API - confirmed by inspecting real DevTools Network
requests against real loaded pages (both the /sbcs listing and an
individual /sbc-solution/{slug}/{id} detail page), not guessed:

  GET https://api-fc26.easysbc.io/sbc-sets?page=N&limit=M
      Paginated listing of SBC sets. Envelope shape not confirmed beyond
      "an array of set objects somewhere in the response" - fetch_listing()
      below handles a bare array or a wrapped {"data": [...]}-style object
      defensively and stops on the first page that yields no new ids.

  GET https://api-fc26.easysbc.io/sbc-sets/{id}
      One set's full metadata - confirmed real response for id 1293
      ("1 of 4 95+ FOF or FUTTIES T1 Player Pick"): name, description,
      repeatable (real bool, not scraped text), startTime/endTime (Unix
      seconds), psPrice/pcPrice (real ints, not "37.3K" text), rewards.
      Used as a fallback only if a listing-page item is missing fields
      (see parse_set_fields) - the listing items look MongoDB-document-
      shaped (a raw "_id" field) so they likely already carry everything,
      but that isn't confirmed for every field on every item.

  GET https://api-fc26.easysbc.io/sbcs?setId={id}
      That set's challenge breakdown - confirmed real response for
      setId 1288 ("Barbara Lopez"): a plain array, each entry with a
      `requirements` field that is ALREADY a clean array of human-readable
      strings (e.g. "Team Rating: Min. 89", "Min. 1 Players Any
      TOTW/TOTS/FOF") - no HTML parsing needed at all.

No browser, no Cloudflare challenge, no Xvfb - plain aiohttp GETs, same
shape as bin_sales_history_sync.py's approach to futbin.com's HTML pages.

Same one-shot-per-invocation Cron Job design as futbin_sbc_sync.py /
bin_sales_history_sync.py (not a permanent worker, no Procfile entry).
Writes into the same backend migrations 018/019 schema
(market_events/sbc_details/sbc_challenges), with source='easysbc' instead
of 'futbin' - the two sources coexist (UNIQUE (kind, source, external_id)),
they are not deduplicated against each other.

Still genuinely open, because this sandbox has no live network access to
verify further:
  - The exact envelope shape of GET /sbc-sets (list vs wrapped object) -
    only a single already-fetched-by-id set object and a single
    already-fetched-by-setId challenge array were confirmed directly; the
    paginated listing endpoint's own response body was never seen.
  - Whether large-volume sequential requests (dozens of sets/run) trip any
    rate limit - only a handful of manual one-off requests were confirmed
    working. _get_json_with_retry backs off on 429/5xx the same way
    bin_sales_history_sync.py does for futbin.com, but that's untested
    against this specific API's actual limits.
  - categoryId (e.g. 2) has no confirmed name mapping, so sbc_details.
    category is left NULL rather than guessed.
  - Reward resourceId/assetId are EA's own definition-id namespace, not
    this codebase's fut_players.card_id (a FUTBIN-derived id scheme used
    throughout, e.g. futbin_card_art_backfill.py) - no mapping between the
    two is known, so reward_card_id is left NULL rather than guessed.

Do one supervised manual run and read the log/heartbeat output before
scheduling this as a real Cron Job - same discipline as
futbin_sbc_sync.py and futbin_card_art_backfill.py.

NO API AUTHENTICATION NEEDED - api-fc26.easysbc.io is a plain,
unauthenticated public JSON API (confirmed above); the only required
credential-shaped config this script needs at all is DATABASE_URL, and it
fails fast with a clear message if that's missing.

DIAGNOSTIC MODE - set EASYSBC_DIAGNOSTIC=true to run a read-only check of
the listing/detail/challenge endpoints' current response shape (logs raw
structure, touches no database table) instead of a real sync. Use this
before trusting a real run if EasySBC's API shape is ever suspected to
have changed - see run_diagnostic() below - rather than guessing at a
payload-shape fix blind.
"""
import os
import re
import sys
import json
import random
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

import asyncpg
import aiohttp

from monitoring import heartbeat, alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("easysbc_sbc_sync")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is required (the same shared database backend/"
        "PriceSync use - see auto_sync/COOLIFY.md). No other credential "
        "is needed: api-fc26.easysbc.io takes no API key/auth."
    )

API_BASE = "https://api-fc26.easysbc.io"
LISTING_PAGE_SIZE = int(os.getenv("EASYSBC_LISTING_PAGE_SIZE", "50"))
MAX_LISTING_PAGES = int(os.getenv("EASYSBC_MAX_LISTING_PAGES", "50"))

DETAIL_STALE_HOURS = int(os.getenv("SBC_DETAIL_STALE_HOURS", "20"))
MAX_DETAIL_PAGES = int(os.getenv("SBC_MAX_DETAIL_PAGES", "0"))
REQUEST_DELAY_SECONDS = float(os.getenv("SBC_REQUEST_DELAY_SECONDS", "1.5"))
# Explicit connect/read sub-timeouts (not just one blanket `total`), so a
# hung TCP handshake and a slow-trickling response are distinguished in
# whichever one actually fires - both still bounded by `total` overall.
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20, sock_connect=10, sock_read=15)
MAX_RETRIES = int(os.getenv("SBC_MAX_RETRIES", "3"))

# Guards against two Coolify Scheduled Task runs overlapping (a run that
# takes longer than the cron interval, or a manual trigger while the
# scheduled one is still going) - same pg_try_advisory_lock idiom
# futgg_player_sync.py uses for the same reason. Distinct key from every
# other advisory lock in this repo (futgg_player_sync's 7741021, the
# migration runner's 7741003).
EASYSBC_LOCK_KEY = int(os.getenv("EASYSBC_LOCK_KEY", "7741042"))

# Read-only diagnostic pass instead of a real sync - see module docstring.
DIAGNOSTIC_MODE = os.getenv("EASYSBC_DIAGNOSTIC", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# Same UA convention already used for futbin.com elsewhere in this repo
# (bin_sales_history_sync.py) - kept consistent rather than inventing a
# second identifier.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SBCSolver/1.5)"}

Diagnostics = DefaultDict[str, int]


# =============================================================================
# HTTP
# =============================================================================

async def polite_delay() -> None:
    base = max(0.0, REQUEST_DELAY_SECONDS)
    jitter = random.uniform(0.0, max(0.5, base * 0.5))
    await asyncio.sleep(base + jitter)


async def _get_json_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    diag: Diagnostics,
) -> Optional[Any]:
    """GET with 429/5xx-aware backoff retry, same shape as
    bin_sales_history_sync.py's _get_with_retry but for a JSON API."""

    backoff = 1.0

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with session.get(
                url, headers=HEADERS, timeout=HTTP_TIMEOUT
            ) as r:
                if r.status == 429:
                    diag["http_429"] += 1
                    if attempt < MAX_RETRIES:
                        retry_after = r.headers.get("Retry-After")
                        wait = (
                            float(retry_after)
                            if retry_after
                            and retry_after.replace(".", "", 1).isdigit()
                            else backoff
                        )
                        await asyncio.sleep(wait)
                        backoff *= 2
                        continue
                    return None

                if r.status >= 500:
                    diag["http_5xx"] += 1
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    return None

                if r.status != 200:
                    diag["http_non_200"] += 1
                    return None

                try:
                    return await r.json()
                except Exception:
                    diag["json_decode_errors"] += 1
                    return None

        except Exception as exc:
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            diag["http_exceptions"] += 1
            log.warning(
                "Request failed after retries: url=%s error=%s",
                url,
                type(exc).__name__,
            )
            return None

    return None


def _extract_list(payload: Any) -> Optional[List[Dict[str, Any]]]:
    """EasySBC's /sbc-sets listing envelope shape was never directly
    observed (only single-item /sbc-sets/{id} and /sbcs?setId= responses
    were confirmed). Accept either a bare array or a wrapped object with
    the array under a common key, rather than assuming one specific
    shape."""

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "sets", "items", "results", "docs"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return None


async def fetch_listing(
    session: aiohttp.ClientSession,
    diag: Diagnostics,
) -> List[Dict[str, Any]]:
    all_sets: List[Dict[str, Any]] = []
    seen_ids: Set[Any] = set()

    for page in range(1, MAX_LISTING_PAGES + 1):
        url = f"{API_BASE}/sbc-sets?page={page}&limit={LISTING_PAGE_SIZE}"
        payload = await _get_json_with_retry(session, url, diag)

        if payload is None:
            log.warning("Listing page %d failed, stopping pagination", page)
            break

        items = _extract_list(payload)

        if items is None:
            log.warning(
                "Listing page %d: response shape not recognised "
                "(not a list, no data/sets/items/results/docs key)",
                page,
            )
            diag["listing_shape_unrecognised"] += 1
            break

        if not items:
            break

        new_count = 0
        for item in items:
            item_id = item.get("id") if isinstance(item, dict) else None
            if item_id is None or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            all_sets.append(item)
            new_count += 1

        if new_count == 0:
            # Every item on this page was already seen - either the API
            # ignores an out-of-range page and re-serves the last page, or
            # pagination is 0-indexed and we're off by one. Either way,
            # continuing would loop until MAX_LISTING_PAGES for nothing.
            break

        if len(items) < LISTING_PAGE_SIZE:
            break

        await polite_delay()

    if page >= MAX_LISTING_PAGES:
        log.warning(
            "Hit EASYSBC_MAX_LISTING_PAGES=%d safety cap - there may be "
            "more sets than this run collected",
            MAX_LISTING_PAGES,
        )

    return all_sets


async def fetch_set_detail(
    session: aiohttp.ClientSession,
    set_id: Any,
    diag: Diagnostics,
) -> Optional[Dict[str, Any]]:
    url = f"{API_BASE}/sbc-sets/{set_id}"
    payload = await _get_json_with_retry(session, url, diag)
    return payload if isinstance(payload, dict) else None


async def fetch_challenges(
    session: aiohttp.ClientSession,
    set_id: Any,
    diag: Diagnostics,
) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/sbcs?setId={set_id}"
    payload = await _get_json_with_retry(session, url, diag)

    if isinstance(payload, list):
        return payload

    extracted = _extract_list(payload)
    return extracted or []


# =============================================================================
# Parsing / normalisation
# =============================================================================

def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def epoch_to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def pick_cost(raw: Dict[str, Any]) -> Optional[int]:
    """Both platforms' prices are already real ints from the API (no
    "37.3K"-style text to parse). PC is used as the canonical single value
    the schema's one total_cost_coins column stores, falling back to PS if
    PC is missing - PC is generally the cheaper/more liquid market and is
    already the convention this codebase leans on elsewhere for a single
    reference price."""

    for key in ("pcPrice", "psPrice"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def requirement_key(
    text: str,
    index: int,
    existing: Dict[str, str],
) -> str:
    """Same slugify-the-full-text convention as futbin_sbc_sync.py's
    requirement_key(), kept identical so the shape written to
    sbc_challenges.requirements matches what the frontend already renders
    (ChallengeBreakdownSection.jsx does
    Object.entries(c.requirements).map(...) - it expects an object, not a
    bare array, even though EasySBC's own API already gives us a clean
    array directly)."""

    key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]

    if not key:
        key = f"requirement_{index + 1}"

    original_key = key
    duplicate_index = 2
    while key in existing:
        key = f"{original_key}_{duplicate_index}"
        duplicate_index += 1

    return key


def parse_set_fields(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    set_id = raw.get("id")
    name = clean_text(raw.get("name"))

    if set_id is None or not name:
        return None

    rewards = raw.get("rewards")
    reward_description = None
    if isinstance(rewards, list) and rewards:
        first_reward = rewards[0]
        if isinstance(first_reward, dict):
            reward_description = clean_text(first_reward.get("name"))
    if not reward_description:
        reward_description = clean_text(raw.get("description"))

    return {
        "external_id": str(set_id),
        "set_id": set_id,
        "title": name,
        "description": clean_text(raw.get("description")),
        "reward_description": reward_description,
        "total_cost_coins": pick_cost(raw),
        "repeatable": bool(raw.get("repeatable", False)),
        "starts_at": epoch_to_datetime(raw.get("startTime")),
        "ends_at": epoch_to_datetime(raw.get("endTime")),
        # categoryId (e.g. 2) has no confirmed name mapping - see module
        # docstring. Left NULL rather than guessed.
        "category": None,
    }


def parse_challenge_fields(
    raw: Dict[str, Any],
    fallback_index: int,
) -> Dict[str, Any]:
    name = clean_text(raw.get("name")) or f"Challenge {fallback_index + 1}"

    requirements: Dict[str, str] = {}
    raw_requirements = raw.get("requirements")

    if isinstance(raw_requirements, list):
        for req_index, req_text in enumerate(raw_requirements):
            text = clean_text(req_text)
            if not text:
                continue
            key = requirement_key(text, req_index, requirements)
            requirements[key] = text

    sort_priority = raw.get("sortPriority")
    display_order = (
        int(sort_priority)
        if isinstance(sort_priority, (int, float))
        else fallback_index
    )

    return {
        "challenge_name": name,
        "requirements": requirements,
        "estimated_cost_coins": pick_cost(raw),
        "display_order": display_order,
    }


# Generalises futbin_sbc_sync.py's totw/tots/toty-only detection to also
# catch FOF (Festival of Football), per a real requirement string seen
# during EasySBC verification: "Min. 1 Players Any TOTW/TOTS/FOF". More
# promo keywords can be added here as they're confirmed in real
# requirement text - this sandbox has no live network access to enumerate
# every FC26 promo up front.
_FINGERPRINT_KEYWORDS: List[Tuple[str, str]] = [
    ("team of the week", "requires_totw"),
    ("totw", "requires_totw"),
    ("team of the season", "requires_tots"),
    ("tots", "requires_tots"),
    ("team of the year", "requires_toty"),
    ("toty", "requires_toty"),
    ("festival of football", "requires_fof"),
    ("fof", "requires_fof"),
]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def fetch_reference_names(
    conn: asyncpg.Connection,
    column: str,
) -> List[str]:
    """Real nation/league names already scraped into fut_players by
    futbin_full_sync.py - queried at runtime rather than hardcoding a
    guessed list, so "Spain" or "Premier League" in a requirement string
    can be recognised against what's actually in this database, not a
    static list that could drift from it. Sorted longest-first so e.g.
    "Korea Republic" is checked before a shorter name that might also
    appear as a substring of it."""

    column_ident = {"nation": "nation", "league": "league"}[column]

    rows = await conn.fetch(
        f"""
        SELECT DISTINCT {column_ident} AS value
        FROM fut_players
        WHERE {column_ident} IS NOT NULL AND {column_ident} <> ''
        """
    )

    names = [row["value"] for row in rows if row["value"]]
    names.sort(key=len, reverse=True)
    return names


def classify_requirement_tags(
    text: str,
    nations: List[str],
    leagues: List[str],
) -> List[str]:
    """Matches requirement text ("Min. 1 Player Spain", "Min. 1 Player
    Premier League") against real nation/league names via whole-phrase,
    word-boundary regex - not naive substring `in` checks, which would
    false-positive (e.g. "Mali" inside "Somalia"). Longest names are
    checked first (see fetch_reference_names) so a multi-word league/
    nation name isn't shadowed by a shorter one contained within it."""

    tags: List[str] = []

    for nation in nations:
        if re.search(rf"\b{re.escape(nation)}\b", text, re.IGNORECASE):
            tags.append(f"nation_{slugify(nation)}")
            break

    for league in leagues:
        if re.search(rf"\b{re.escape(league)}\b", text, re.IGNORECASE):
            tags.append(f"league_{slugify(league)}")
            break

    return tags


def build_fingerprint(
    set_fields: Dict[str, Any],
    challenges: List[Dict[str, Any]],
    nations: Optional[List[str]] = None,
    leagues: Optional[List[str]] = None,
) -> List[str]:
    tags: List[str] = []

    if set_fields.get("repeatable"):
        tags.append("repeatable")

    title = (set_fields.get("title") or "").lower()
    if "icon" in title:
        tags.append("icon_reward")
    if "hero" in title:
        tags.append("hero_reward")

    all_requirements = [
        value
        for challenge in challenges
        for value in (challenge.get("requirements") or {}).values()
    ]
    all_requirement_text = " ".join(all_requirements).lower()

    for keyword, tag in _FINGERPRINT_KEYWORDS:
        if keyword in all_requirement_text:
            tags.append(tag)

    if nations or leagues:
        for requirement_text in all_requirements:
            tags.extend(
                classify_requirement_tags(
                    requirement_text,
                    nations or [],
                    leagues or [],
                )
            )

    total_cost = set_fields.get("total_cost_coins")
    if total_cost is not None:
        if total_cost >= 500_000:
            tags.append("high_cost")
        elif total_cost <= 50_000:
            tags.append("low_cost")

    return list(dict.fromkeys(tags))


# =============================================================================
# Database
# =============================================================================

async def ensure_tables(conn: asyncpg.Connection) -> None:
    """Same DDL as futbin_sbc_sync.py - each worker independently ensures
    its own tables exist rather than assuming backend migration 018 has
    already run, matching the established convention in this repo."""

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            id            BIGSERIAL PRIMARY KEY,
            kind          TEXT NOT NULL,
            source        TEXT NOT NULL,
            external_id   TEXT NOT NULL,
            title         TEXT NOT NULL,
            description   TEXT,
            starts_at     TIMESTAMPTZ,
            ends_at       TIMESTAMPTZ,
            fingerprint   TEXT[] NOT NULL DEFAULT '{}',
            payload       JSONB NOT NULL DEFAULT '{}',
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (kind, source, external_id)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sbc_details (
            event_id            BIGINT PRIMARY KEY
                REFERENCES market_events(id) ON DELETE CASCADE,
            set_name            TEXT NOT NULL,
            category            TEXT,
            total_cost_coins    BIGINT,
            repeatable          BOOLEAN NOT NULL DEFAULT false,
            reward_card_id      BIGINT REFERENCES fut_players(card_id),
            reward_description  TEXT,
            expires_at          TIMESTAMPTZ
        )
        """
    )
    # detail_scraped_at is written by futbin_sbc_sync.py's write_detail but
    # isn't in the original 018 migration's CREATE TABLE above (it relies
    # on ALTER having already run in production) - add it defensively so
    # this file works even against a database that only ever ran the raw
    # 018 migration.
    await conn.execute(
        """
        ALTER TABLE sbc_details
        ADD COLUMN IF NOT EXISTS detail_scraped_at TIMESTAMPTZ
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sbc_challenges (
            id                    BIGSERIAL PRIMARY KEY,
            event_id              BIGINT NOT NULL
                REFERENCES market_events(id) ON DELETE CASCADE,
            challenge_name        TEXT NOT NULL,
            requirements          JSONB NOT NULL DEFAULT '{}',
            estimated_cost_coins  BIGINT,
            display_order         INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sbc_challenges_event
        ON sbc_challenges (event_id)
        """
    )


async def upsert_event(
    conn: asyncpg.Connection,
    set_fields: Dict[str, Any],
) -> Tuple[int, bool]:
    """Returns (event_id, was_inserted). `xmax = 0` is the standard
    Postgres idiom for telling an ON CONFLICT DO UPDATE apart from a
    fresh INSERT within the same statement - xmax is the deleting
    transaction id on a row version, which is unset (0) only for a row
    version just created by this INSERT, never for one touched by the
    DO UPDATE branch. Used to report real inserted/updated counts rather
    than only a total "sets_found"."""
    row = await conn.fetchrow(
        """
        INSERT INTO market_events (
            kind, source, external_id, title, description, payload
        )
        VALUES ('sbc', 'easysbc', $1, $2, $3, $4::jsonb)
        ON CONFLICT (kind, source, external_id)
        DO UPDATE SET
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            payload = EXCLUDED.payload,
            updated_at = now()
        RETURNING id, (xmax = 0) AS inserted
        """,
        set_fields["external_id"],
        set_fields["title"],
        set_fields.get("description"),
        json.dumps({"set_id": set_fields["set_id"]}),
    )

    if row is None:
        raise RuntimeError(
            f"Could not upsert listing: {set_fields['external_id']}"
        )

    return int(row["id"]), bool(row["inserted"])


async def get_due_external_ids(
    conn: asyncpg.Connection,
    event_ids: Dict[str, int],
) -> Set[str]:
    if not event_ids:
        return set()

    rows = await conn.fetch(
        """
        SELECT e.external_id
        FROM market_events e
        LEFT JOIN sbc_details d ON d.event_id = e.id
        WHERE
            e.id = ANY($1::bigint[])
            AND e.kind = 'sbc'
            AND e.source = 'easysbc'
            AND (
                d.event_id IS NULL
                OR d.detail_scraped_at IS NULL
                OR d.detail_scraped_at < now() - make_interval(hours => $2)
            )
        """,
        list(event_ids.values()),
        DETAIL_STALE_HOURS,
    )

    return {str(row["external_id"]) for row in rows}


async def write_detail(
    conn: asyncpg.Connection,
    event_id: int,
    set_fields: Dict[str, Any],
    challenges: List[Dict[str, Any]],
    nations: Optional[List[str]] = None,
    leagues: Optional[List[str]] = None,
) -> None:
    fingerprint = build_fingerprint(set_fields, challenges, nations, leagues)

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE market_events
            SET fingerprint = $2, ends_at = COALESCE($3, ends_at), updated_at = now()
            WHERE id = $1
            """,
            event_id,
            fingerprint,
            set_fields.get("ends_at"),
        )

        await conn.execute(
            """
            INSERT INTO sbc_details (
                event_id, set_name, category, total_cost_coins,
                repeatable, reward_card_id, reward_description,
                expires_at, detail_scraped_at
            )
            VALUES ($1, $2, $3, $4, $5, NULL, $6, $7, now())
            ON CONFLICT (event_id) DO UPDATE SET
                set_name = EXCLUDED.set_name,
                category = EXCLUDED.category,
                total_cost_coins = EXCLUDED.total_cost_coins,
                repeatable = EXCLUDED.repeatable,
                reward_description = EXCLUDED.reward_description,
                expires_at = EXCLUDED.expires_at,
                detail_scraped_at = now()
            """,
            event_id,
            set_fields["title"],
            set_fields.get("category"),
            set_fields.get("total_cost_coins"),
            set_fields.get("repeatable", False),
            set_fields.get("reward_description"),
            set_fields.get("ends_at"),
        )

        await conn.execute(
            "DELETE FROM sbc_challenges WHERE event_id = $1",
            event_id,
        )

        for challenge in challenges:
            await conn.execute(
                """
                INSERT INTO sbc_challenges (
                    event_id, challenge_name, requirements,
                    estimated_cost_coins, display_order
                )
                VALUES ($1, $2, $3::jsonb, $4, $5)
                """,
                event_id,
                challenge["challenge_name"],
                json.dumps(challenge.get("requirements") or {}),
                challenge.get("estimated_cost_coins"),
                challenge.get("display_order", 0),
            )


# =============================================================================
# Main crawl
# =============================================================================

async def run_diagnostic(session: aiohttp.ClientSession, diag: Diagnostics) -> bool:
    """Read-only check of the current EasySBC API response shape - no
    database writes, no advisory lock needed. Use this (EASYSBC_DIAGNOSTIC
    =true) to confirm the listing/detail/challenge endpoints still return
    what this script expects BEFORE trusting a real scheduled run, instead
    of guessing at a payload-shape fix from a production failure alone.
    Returns True if the listing endpoint returned at least one usable
    item; the caller uses this as crawl_once()'s success flag for this
    mode."""
    log.warning("=" * 72)
    log.warning("EASYSBC_DIAGNOSTIC MODE - read-only, no database writes")
    log.warning("=" * 72)

    url = f"{API_BASE}/sbc-sets?page=1&limit={LISTING_PAGE_SIZE}"
    log.info("GET %s", url)
    payload = await _get_json_with_retry(session, url, diag)
    if payload is None:
        log.error(
            "DIAGNOSTIC: listing request failed outright (see http_* "
            "counters below) - cannot say anything about response shape."
        )
        log.info("diagnostics: %s", dict(diag))
        return False

    log.info("DIAGNOSTIC: listing payload type=%s", type(payload).__name__)
    if isinstance(payload, dict):
        log.info("DIAGNOSTIC: listing top-level keys=%s", list(payload)[:20])

    items = _extract_list(payload)
    if items is None:
        log.error(
            "DIAGNOSTIC: listing response is neither a bare list nor a "
            "dict with a recognised data/sets/items/results/docs key - "
            "_extract_list() needs updating for the new shape. Raw "
            "payload preview: %s",
            json.dumps(payload, default=str)[:2000],
        )
        return False

    log.info("DIAGNOSTIC: listing yielded %d item(s)", len(items))
    if not items:
        log.warning("DIAGNOSTIC: listing returned zero items - nothing further to check.")
        return False

    first = items[0]
    log.info(
        "DIAGNOSTIC: first item type=%s keys=%s",
        type(first).__name__,
        list(first)[:30] if isinstance(first, dict) else "<not a dict>",
    )
    parsed = parse_set_fields(first) if isinstance(first, dict) else None
    log.info("DIAGNOSTIC: parse_set_fields() on first item -> %s", parsed)

    set_id = first.get("id") if isinstance(first, dict) else None
    if set_id is None:
        log.error("DIAGNOSTIC: first item has no 'id' field - parse_set_fields() will reject every item.")
        return parsed is not None

    detail_url = f"{API_BASE}/sbc-sets/{set_id}"
    log.info("GET %s", detail_url)
    detail_payload = await _get_json_with_retry(session, detail_url, diag)
    log.info(
        "DIAGNOSTIC: detail payload type=%s keys=%s",
        type(detail_payload).__name__,
        list(detail_payload)[:30] if isinstance(detail_payload, dict) else "<not a dict>",
    )

    challenges_url = f"{API_BASE}/sbcs?setId={set_id}"
    log.info("GET %s", challenges_url)
    challenges_payload = await _get_json_with_retry(session, challenges_url, diag)
    challenges_list = challenges_payload if isinstance(challenges_payload, list) else _extract_list(challenges_payload)
    log.info(
        "DIAGNOSTIC: challenges payload type=%s count=%s",
        type(challenges_payload).__name__,
        len(challenges_list) if challenges_list is not None else "?",
    )
    if challenges_list:
        log.info(
            "DIAGNOSTIC: first challenge keys=%s parsed=%s",
            list(challenges_list[0]) if isinstance(challenges_list[0], dict) else "<not a dict>",
            parse_challenge_fields(challenges_list[0], 0) if isinstance(challenges_list[0], dict) else None,
        )

    log.info("diagnostics: %s", dict(diag))
    log.warning("=" * 72)
    log.warning("DIAGNOSTIC MODE complete - no database writes were made")
    log.warning("=" * 72)
    return parsed is not None and bool(challenges_list)


async def crawl_once() -> bool:
    """Returns True on a successful run (or a benign no-op, e.g. skipped
    because another run already holds the advisory lock), False on a
    genuine failure. __main__ uses this to set the process exit code, so
    Coolify's own Scheduled Task run history shows failures distinctly -
    not just the heartbeat table."""
    started = time.perf_counter()
    log.info("crawl_once() starting")

    diag: Diagnostics = defaultdict(int)

    if DIAGNOSTIC_MODE:
        # Read-only and network-only - no database writes, so no need for
        # the advisory lock (safe to run alongside a real sync) or even a
        # DB pool at all.
        async with aiohttp.ClientSession() as session:
            return await run_diagnostic(session, diag)

    # Advisory lock, held for the whole run via a dedicated connection -
    # same pg_try_advisory_lock idiom futgg_player_sync.py uses to stop
    # two overlapping runs (a slow run still going when the next
    # scheduled trigger fires, or a manual trigger during a scheduled
    # one) from racing each other's writes. Non-blocking: a run that
    # can't get the lock skips immediately rather than queueing behind
    # the one already in progress.
    lock_conn = await asyncpg.connect(DATABASE_URL)
    got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", EASYSBC_LOCK_KEY)
    if not got_lock:
        log.warning("Another easysbc_sbc_sync run already holds the lock - skipping this run.")
        await lock_conn.close()
        return True

    try:
        log.info("Connecting to database...")
        try:
            pool = await asyncio.wait_for(
                asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4),
                timeout=30,
            )
        except asyncio.TimeoutError:
            log.error("Database pool creation timed out after 30s")
            raise
        log.info("Database pool ready")

        sets_found = 0
        sets_inserted = 0
        sets_updated = 0
        sets_due = 0
        sets_skipped_not_due = 0
        details_attempted = 0
        details_written = 0
        details_failed = 0

        try:
            async with pool.acquire() as conn:
                log.info("Ensuring tables exist...")
                await asyncio.wait_for(ensure_tables(conn), timeout=30)
                log.info("Tables ready")

            nations: List[str] = []
            leagues: List[str] = []
            try:
                async with pool.acquire() as conn:
                    nations = await fetch_reference_names(conn, "nation")
                    leagues = await fetch_reference_names(conn, "league")
                log.info(
                    "Loaded %d real nation names and %d real league names "
                    "from fut_players for requirement classification",
                    len(nations),
                    len(leagues),
                )
            except Exception as exc:
                # fut_players may not be populated yet on a fresh database -
                # degrade to no nation/league tagging rather than failing the
                # whole SBC crawl over it.
                log.warning(
                    "Could not load nation/league reference data: %s",
                    exc,
                )

            async with aiohttp.ClientSession() as session:
                log.info("Fetching SBC set listing from %s...", API_BASE)
                raw_sets = await fetch_listing(session, diag)
                sets_found = len(raw_sets)
                log.info("Listing: %d sets found", sets_found)

                if sets_found == 0:
                    detail = "Zero sets found on listing endpoint"
                    await heartbeat(pool, "easysbc_sbc_sync", ok=False, detail=detail)
                    await alert(
                        "easysbc_sbc_sync: zero SBC sets found on "
                        f"{API_BASE}/sbc-sets - check the listing response "
                        "shape hasn't changed (try EASYSBC_DIAGNOSTIC=true)."
                    )
                    return False

                parsed_sets: Dict[str, Dict[str, Any]] = {}
                for raw_set in raw_sets:
                    set_fields = parse_set_fields(raw_set)
                    if set_fields is None:
                        diag["set_parse_failed"] += 1
                        continue
                    parsed_sets[set_fields["external_id"]] = set_fields

                event_ids: Dict[str, int] = {}
                async with pool.acquire() as conn:
                    for external_id, set_fields in parsed_sets.items():
                        event_id, was_inserted = await upsert_event(conn, set_fields)
                        event_ids[external_id] = event_id
                        if was_inserted:
                            sets_inserted += 1
                        else:
                            sets_updated += 1

                    due_ids = await get_due_external_ids(conn, event_ids)

                sets_due = len(due_ids)
                sets_skipped_not_due = len(parsed_sets) - sets_due
                log.info(
                    "SBC details due: %d of %d (%d unchanged/not yet stale, skipped)",
                    sets_due, len(parsed_sets), sets_skipped_not_due,
                )

                due_list = [
                    parsed_sets[eid] for eid in due_ids if eid in parsed_sets
                ]

                if MAX_DETAIL_PAGES > 0:
                    due_list = due_list[:MAX_DETAIL_PAGES]

                for set_fields in due_list:
                    details_attempted += 1
                    external_id = set_fields["external_id"]

                    log.info(
                        "Loading SBC detail %d/%d: %s (id=%s)",
                        details_attempted,
                        len(due_list),
                        set_fields["title"],
                        external_id,
                    )

                    # Listing items look MongoDB-document-shaped and likely
                    # already carry everything, but that isn't confirmed for
                    # every field - fall back to the per-id endpoint only if
                    # something we actually need is missing.
                    if (
                        set_fields.get("total_cost_coins") is None
                        and set_fields.get("ends_at") is None
                    ):
                        detail_raw = await fetch_set_detail(
                            session, set_fields["set_id"], diag
                        )
                        if detail_raw is not None:
                            refreshed = parse_set_fields(detail_raw)
                            if refreshed is not None:
                                set_fields = refreshed

                    raw_challenges = await fetch_challenges(
                        session, set_fields["set_id"], diag
                    )

                    if not raw_challenges:
                        details_failed += 1
                        diag["zero_challenges"] += 1
                        log.warning(
                            "Zero challenges returned for set id=%s (%s)",
                            external_id,
                            set_fields["title"],
                        )
                        await polite_delay()
                        continue

                    challenges = [
                        parse_challenge_fields(raw, index)
                        for index, raw in enumerate(raw_challenges)
                    ]

                    async with pool.acquire() as conn:
                        await write_detail(
                            conn,
                            event_ids[external_id],
                            set_fields,
                            challenges,
                            nations,
                            leagues,
                        )

                    details_written += 1
                    await polite_delay()

            elapsed = time.perf_counter() - started
            summary = (
                f"elapsed={elapsed:.1f}s "
                f"sets_found={sets_found} sets_inserted={sets_inserted} "
                f"sets_updated={sets_updated} sets_due={sets_due} "
                f"sets_skipped_not_due={sets_skipped_not_due} "
                f"details_attempted={details_attempted} "
                f"details_written={details_written} "
                f"details_failed={details_failed} "
                f"http_429={diag['http_429']} http_5xx={diag['http_5xx']} "
                f"http_non_200={diag['http_non_200']} "
                f"http_exceptions={diag['http_exceptions']} "
                f"json_decode_errors={diag['json_decode_errors']} "
                f"set_parse_failed={diag['set_parse_failed']} "
                f"zero_challenges={diag['zero_challenges']}"
            )
            log.info("Run complete. %s", summary)

            ok = details_attempted == 0 or details_written > 0

            await heartbeat(pool, "easysbc_sbc_sync", ok=ok, detail=summary)

            if not ok:
                await alert(f"easysbc_sbc_sync: run failed. {summary}")

            return ok

        finally:
            await pool.close()
    finally:
        await lock_conn.execute("SELECT pg_advisory_unlock($1)", EASYSBC_LOCK_KEY)
        await lock_conn.close()


if __name__ == "__main__":
    log.info("SBC worker process started (easysbc.io API)")
    try:
        ok = asyncio.run(crawl_once())
    except KeyboardInterrupt:
        log.warning("SBC sync interrupted")
        sys.exit(130)
    except Exception as exc:
        log.exception("SBC sync crashed: %s", exc)
        sys.exit(1)
    sys.exit(0 if ok else 1)
