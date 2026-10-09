#!/usr/bin/env python
"""FC27 FUT.GG content collector: SBCs, exact solutions, and Gallery.

This replaces ``easysbc_sbc_sync.py`` as the canonical SBC scheduled task.
FUT.GG's SBC listing is server-rendered; each set page exposes its EA challenge
ids through the challenge artwork URLs, and the public challenge JSON endpoint
returns the authoritative set/challenge metadata.  Cheapest-squad UUIDs are
then resolved through FUT.GG's public squad endpoint.

The same run also snapshots the two FC27 Gallery JSON resources.  Raw payloads
are retained for forward compatibility, while the fields the product needs are
normalised for fast list/detail pages and demand analysis.

One invocation performs one bounded crawl and exits. Deploy it as the existing
Coolify SBC scheduled task. It never touches the price or completed-sales
collectors.
"""
from __future__ import annotations

from playwright_stealth import Stealth

import asyncio
import html as html_lib
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

import asyncpg

from db_connect import connect_with_retry
from monitoring import alert, heartbeat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("futgg_content_sync")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
INGEST_URL = (os.getenv("FUTGG_CONTENT_INGEST_URL") or "").strip()
INGEST_TOKEN = (os.getenv("FUTGG_CONTENT_SYNC_TOKEN") or "").strip()
GAME_YEAR = int(os.getenv("EAFC_GAME_YEAR") or os.getenv("FUTGG_GAME_YEAR") or "27")
SITE_ROOT = "https://www.fut.gg"
SBC_LIST_URL = f"{SITE_ROOT}/sbc/"
SBC_API_ROOT = f"{SITE_ROOT}/api/fut/sbc/{GAME_YEAR}"
GALLERY_GAME = f"fc{GAME_YEAR}"
GALLERY_URL = f"{SITE_ROOT}/api/fut/gallery/{GALLERY_GAME}/"
GALLERY_SOLUTIONS_URL = f"{GALLERY_URL}solutions/"
TIMEOUT_SECONDS = max(10, int(os.getenv("FUTGG_CONTENT_TIMEOUT_SECONDS", "30")))
CONCURRENCY = max(1, min(4, int(os.getenv("FUTGG_CONTENT_CONCURRENCY", "2"))))
MAX_RETRIES = max(1, int(os.getenv("FUTGG_CONTENT_MAX_RETRIES", "3")))
DIAGNOSTIC = (os.getenv("FUTGG_CONTENT_DIAGNOSTIC") or "").lower() in {"1", "true", "yes"}
WORKER = "futgg_content_sync"
LOCK_KEY = 2_702_701

HEADERS = {
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}
HEADLESS = (os.getenv("PLAYWRIGHT_HEADLESS") or "false").lower() in {"1", "true", "yes"}

SBC_LINK_RE = re.compile(
    rf'href=["\'](?P<path>/sbc/(?:[^/]+)/{GAME_YEAR}-(?:\d+)-[^"\'#?]+/)["\']',
    re.IGNORECASE,
)
CHALLENGE_IMAGE_RE = re.compile(
    rf"20{GAME_YEAR}/sbcs/challenges/(?P<id>\d+)\.png", re.IGNORECASE
)
CHALLENGE_IDS_RE = re.compile(r'"challengeEaIds"\s*:\s*\[(?P<ids>[\d,\s]+)\]')
SQUAD_UUID_RE = re.compile(r"/squad-builder/(?P<uuid>[0-9a-f]{8}-[0-9a-f-]{27})", re.I)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or None


def discover_sbc_links(page_html: str) -> list[str]:
    """Return unique current set URLs from FUT.GG's canonical SBC page."""
    seen: set[str] = set()
    links: list[str] = []
    for match in SBC_LINK_RE.finditer(html_lib.unescape(page_html)):
        url = urljoin(SITE_ROOT, match.group("path"))
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def discover_challenge_ids(detail_html: str) -> list[int]:
    """Extract the EA challenge ids FUT.GG embeds in detail-page assets."""
    ids = {int(match.group("id")) for match in CHALLENGE_IMAGE_RE.finditer(detail_html)}
    for match in CHALLENGE_IDS_RE.finditer(detail_html):
        ids.update(int(value) for value in re.findall(r"\d+", match.group("ids")))
    return sorted(ids)


def squad_uuid(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = SQUAD_UUID_RE.search(value)
    return match.group("uuid") if match else None


class BrowserTransport:
    """Small organic-navigation pool for FUT.GG's Cloudflare-protected pages.

    Plain aiohttp receives an immediate 403 from production. These requests use
    normal headed Chromium navigations instead: the same transport already
    proven by the sales collector and the same path a user follows in Chrome.
    """

    def __init__(self, pages: list[Any]):
        self.pages: asyncio.Queue[Any] = asyncio.Queue()
        for page in pages:
            self.pages.put_nowait(page)

    async def request(self, url: str, *, expect_json: bool) -> Any:
        page = await self.pages.get()
        try:
            last_error: Optional[Exception] = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if expect_json:
                        # FUT.GG's JSON routes are XHR/fetch resources. Directly
                        # navigating Chromium to /api/... changes the browser's
                        # request context to a top-level document navigation and
                        # Cloudflare now rejects that shape with 403 (observed in
                        # production on the FC27 Gallery endpoint). Request the
                        # resource from the already-warmed fut.gg page instead,
                        # matching how FUT.GG's own frontend calls it. This is
                        # only two Gallery calls plus bounded SBC detail calls;
                        # the existing retry/concurrency limits are unchanged.
                        result = await page.evaluate(
                            """async (url) => {
                                const response = await fetch(url, {
                                    method: 'GET',
                                    credentials: 'include',
                                    headers: {'Accept': 'application/json'}
                                });
                                return {status: response.status, body: await response.text()};
                            }""",
                            url,
                        )
                        status = result.get("status") if isinstance(result, dict) else None
                        body = result.get("body", "") if isinstance(result, dict) else ""
                        if status == 200:
                            try:
                                return json.loads(body)
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(f"non-JSON browser response from {url}") from exc
                    else:
                        response = await page.goto(
                            url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000
                        )
                        status = response.status if response else None
                        if status == 200:
                            return await page.content()

                    if status not in {403, 429, 500, 502, 503, 504}:
                        raise RuntimeError(f"GET {url} returned HTTP {status}")
                    raise RuntimeError(f"browser GET {url} returned HTTP {status}")
                except Exception as exc:
                    last_error = exc
                    if attempt >= MAX_RETRIES:
                        break
                    delay = min(8.0, (2 ** (attempt - 1)) + random.random())
                    log.warning("browser request failed for %s: %s; retrying in %.1fs", url, exc, delay)
                    await asyncio.sleep(delay)
            raise RuntimeError(
                f"browser request failed for {url}: {last_error or 'unknown error'}"
            )
        finally:
            self.pages.put_nowait(page)


async def _request(
    session: BrowserTransport, url: str, *, expect_json: bool
) -> Any:
    return await session.request(url, expect_json=expect_json)


async def _get_text(session: BrowserTransport, url: str) -> str:
    return str(await _request(session, url, expect_json=False))


async def _get_json(session: BrowserTransport, url: str) -> dict[str, Any]:
    payload = await _request(session, url, expect_json=True)
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected JSON shape from {url}: {type(payload).__name__}")
    return payload


def _set_payload(api_payload: dict[str, Any]) -> dict[str, Any]:
    """Find the SBC object without coupling the worker to envelope nesting."""
    queue = [api_payload]
    fallback: Optional[dict[str, Any]] = None
    while queue:
        candidate = queue.pop(0)
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("challenges"), list):
            return candidate
        if fallback is None and (candidate.get("eaId") is not None or candidate.get("eald") is not None):
            fallback = candidate
        queue.extend(value for value in candidate.values() if isinstance(value, dict))
    if fallback is not None:
        return fallback
    raise RuntimeError("FUT.GG SBC response has no recognisable set object")


def _reward_label(rewards: Iterable[Any]) -> Optional[str]:
    labels: list[str] = []
    for raw in rewards:
        reward = _as_dict(raw)
        count = _int(reward.get("count")) or 1
        label = reward.get("pack") or reward.get("evolutionName")
        player = _as_dict(reward.get("player"))
        label = label or player.get("displayName") or player.get("name")
        coins = _int(reward.get("coins"))
        if label:
            labels.append(f"{count}x {label}" if count > 1 else str(label))
        elif coins:
            labels.append(f"{coins:,} coins")
    return " · ".join(dict.fromkeys(labels)) or None


def _asset_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://game-assets.fut.gg/cdn-cgi/image/quality=85,format=auto,width=600/{value.lstrip('/')}"


def normalise_sbc(raw: dict[str, Any]) -> dict[str, Any]:
    category = _as_dict(raw.get("category"))
    challenges: list[dict[str, Any]] = []
    for order, challenge_raw in enumerate(_as_list(raw.get("challenges"))):
        challenge = _as_dict(challenge_raw)
        requirements = [str(item) for item in _as_list(challenge.get("requirementsText")) if item]
        console_path = challenge.get("cheapestSolutionUrl")
        pc_path = challenge.get("cheapestSolutionPcUrl")
        console_url = urljoin(SITE_ROOT, console_path) if isinstance(console_path, str) else None
        pc_url = urljoin(SITE_ROOT, pc_path) if isinstance(pc_path, str) else None
        challenges.append(
            {
                "external_challenge_id": _int(challenge.get("eaId") or challenge.get("eald")),
                "name": challenge.get("name") or raw.get("name") or "Challenge",
                "description": challenge.get("description"),
                "requirements": {f"requirement_{idx + 1}": value for idx, value in enumerate(requirements)},
                "cost_console": _int(challenge.get("cheapestSolutionPrice")),
                "cost_pc": _int(challenge.get("cheapestSolutionPricePc")),
                "solution_console_url": console_url,
                "solution_pc_url": pc_url,
                "solution_console_uuid": squad_uuid(console_path),
                "solution_pc_uuid": squad_uuid(pc_path),
                "image_url": _asset_url(challenge.get("imageUrl") or challenge.get("imagePath")),
                "reward": _as_list(challenge.get("awards")),
                "display_order": order,
            }
        )

    rewards = _as_list(raw.get("allAwards")) or _as_list(raw.get("awards"))
    return {
        "external_id": str(raw.get("eaId") or raw.get("eald") or raw.get("id")),
        "name": raw.get("name") or "Unnamed SBC",
        "description": raw.get("description"),
        "starts_at": _iso(raw.get("startTime") or raw.get("createdAt")),
        "ends_at": _iso(raw.get("endTime")),
        "category": category.get("slug") or category.get("name"),
        "cost_console": _int(raw.get("cost")),
        "cost_pc": _int(raw.get("costPc")),
        "repeatable": bool(raw.get("isRepeatable")),
        "repeat_count": _int(raw.get("numberOfRepeats")),
        "repeat_refresh_seconds": _int(raw.get("repeatRefreshInterval")),
        "reward_label": _reward_label(rewards),
        "image_url": _asset_url(raw.get("imageUrl") or raw.get("imagePath")),
        "futgg_url": urljoin(SITE_ROOT, str(raw.get("url") or "")),
        "challenges": challenges,
        "raw": raw,
    }


def build_fingerprint(sbc: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value)
        for challenge in sbc["challenges"]
        for value in challenge["requirements"].values()
    ).lower()
    tags: list[str] = []
    for needle, tag in (
        ("team of the week", "requires_totw"), ("totw", "requires_totw"),
        ("icon", "requires_icon"), ("hero", "requires_hero"),
        ("rare", "requires_rare"), ("chemistry", "requires_chemistry"),
    ):
        if needle in text and tag not in tags:
            tags.append(tag)
    cost = sbc.get("cost_console")
    if cost is not None:
        tags.append("high_cost" if cost >= 500_000 else "low_cost" if cost <= 50_000 else "mid_cost")
    return tags


def normalise_squad(payload: dict[str, Any]) -> list[dict[str, int]]:
    envelope = _as_dict(payload.get("data"))
    squad = _as_dict(envelope.get("data")) or envelope
    positions = _as_list(squad.get("activeGroupPositions"))
    result: list[dict[str, int]] = []
    for fallback, raw in enumerate(positions):
        item = _as_dict(raw)
        player_id = _int(item.get("playerEaId") or item.get("playerEald"))
        if player_id is not None:
            position_idx = _int(item.get("positionIdx"))
            result.append({"position_idx": fallback if position_idx is None else position_idx, "player_ea_id": player_id})
    return result


async def collect_sbc_data(session: BrowserTransport) -> list[dict[str, Any]]:
    listing = await _get_text(session, SBC_LIST_URL)
    links = discover_sbc_links(listing)
    if not links:
        raise RuntimeError("FUT.GG SBC listing returned no set links")
    log.info("discovered %d current FUT.GG SBC sets", len(links))
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def collect_one(url: str) -> Optional[dict[str, Any]]:
        async with semaphore:
            detail_html = await _get_text(session, url)
            challenge_ids = discover_challenge_ids(detail_html)
            if not challenge_ids:
                log.warning("no challenge id found on %s", url)
                return None
            payload = await _get_json(session, f"{SBC_API_ROOT}/challenge/{challenge_ids[0]}/")
            sbc = normalise_sbc(_set_payload(payload))
            sbc["futgg_url"] = url
            sbc["squads"] = {}
            uuids = {
                uuid
                for challenge in sbc["challenges"]
                for uuid in (challenge.get("solution_console_uuid"), challenge.get("solution_pc_uuid"))
                if uuid
            }
            for uuid in uuids:
                try:
                    sbc["squads"][uuid] = normalise_squad(
                        await _get_json(session, f"{SITE_ROOT}/api/squads/{uuid}/")
                    )
                except Exception as exc:  # solution enrichment must not drop the SBC
                    log.warning("could not fetch squad %s: %s", uuid, exc)
            return sbc

    results = await asyncio.gather(*(collect_one(url) for url in links), return_exceptions=True)
    sbcs: list[dict[str, Any]] = []
    for link, result in zip(links, results):
        if isinstance(result, Exception):
            log.error("SBC collection failed for %s: %s", link, result)
        elif result is not None:
            sbcs.append(result)
    if not sbcs:
        raise RuntimeError("no FUT.GG SBC details could be collected")
    return sbcs


def _category_map(base: dict[str, Any]) -> dict[int, tuple[Optional[str], Optional[str]]]:
    mapping: dict[int, tuple[Optional[str], Optional[str]]] = {}
    data = _as_dict(base.get("data")) or base
    for category_raw in _as_list(data.get("categories")):
        category = _as_dict(category_raw)
        name = category.get("name")
        slug = category.get("slug") or _slug(name)
        for set_raw in _as_list(category.get("sets")):
            set_obj = _as_dict(set_raw)
            set_id = _int(set_obj.get("setId") or set_obj.get("id") or set_raw)
            if set_id is not None:
                mapping[set_id] = (name, slug)
    return mapping


def _solution_items(solution: dict[str, Any]) -> list[dict[str, Any]]:
    items = _as_list(solution.get("items")) or _as_list(solution.get("players"))
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in items:
        item = _as_dict(raw)
        card_ea_id = _int(item.get("eaId") or item.get("eald"))
        player_ea_id = _int(item.get("playerEaId") or item.get("playerEald"))
        # FUT.GG can repeat the identical card entry in the recommendation
        # payload. Different versions of one player have different eaIds and
        # remain distinct; only an exact card duplicate is discarded.
        identity = (
            card_ea_id,
            player_ea_id,
            _int(item.get("overall")),
            _int(item.get("rarityEaId") or item.get("rarityEald")),
            _int(item.get("clubEaId") or item.get("clubEald")),
            _int(item.get("nationEaId") or item.get("nationEald")),
            _int(item.get("price")),
            _int(item.get("score")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            {
                "position_idx": len(result),
                "card_ea_id": card_ea_id,
                "player_ea_id": player_ea_id,
                "price": _int(item.get("price")),
                "score": _int(item.get("score")),
                "overall": _int(item.get("overall")),
                "club_ea_id": _int(item.get("clubEaId") or item.get("clubEald")),
                "nation_ea_id": _int(item.get("nationEaId") or item.get("nationEald")),
                "rarity_ea_id": _int(item.get("rarityEaId") or item.get("rarityEald")),
            }
        )
    return result


def _gallery_tier(
    raw: dict[str, Any], fallback_grade: Any = None, *, include_items: bool = False
) -> dict[str, Any]:
    # costTiers describes grade/cost targets. Only the solution-set root has
    # FUT.GG's chosen collection. Treating nested tier data as a second lineup
    # duplicates the same player/card candidates in the UI.
    items = _solution_items(raw) if include_items else []
    captured_cost = _int(raw.get("cost"))
    item_cost = sum(
        value for item in items
        if (value := _int(item.get("price"))) is not None and value > 0
    )
    return {
        "grade": str(raw.get("grade") or fallback_grade or "").upper(),
        "cost": captured_cost if captured_cost is not None else item_cost,
        "tokens": _int(raw.get("tokens")),
        "score": _int(raw.get("score") or raw.get("baseGrade")),
        "items": items,
    }


def normalise_gallery(base: dict[str, Any], solutions: dict[str, Any]) -> dict[str, Any]:
    base_data = _as_dict(base.get("data"))
    solution_data = _as_dict(solutions.get("data"))
    if not base_data:
        base_data = base
    if not solution_data:
        solution_data = solutions
    categories = _category_map(base)
    sets: list[dict[str, Any]] = []
    solution_sets = _as_list(solution_data.get("sets")) or _as_list(solution_data.get("solutions"))
    if not solution_sets and isinstance(solutions.get("data"), list):
        solution_sets = solutions["data"]
    for fallback, raw in enumerate(solution_sets):
        solution = _as_dict(raw)
        set_id = _int(solution.get("setId") or solution.get("id"))
        if set_id is None:
            set_id = fallback + 1
        category_name, category_slug = categories.get(set_id, (None, None))
        cost_tiers = _as_list(solution.get("costTiers"))
        tiers = [
            _gallery_tier(_as_dict(tier), solution.get("grade"))
            for tier in cost_tiers
        ]
        top_tier = _gallery_tier(solution, solution.get("grade"), include_items=True)
        same_grade = next(
            (tier for tier in tiers if tier["grade"] == top_tier["grade"]), None
        )
        recommendation_cost = top_tier["cost"]
        if same_grade is not None:
            # The root `items` list is the authoritative recommendation.
            same_grade["items"] = top_tier["items"]
            if same_grade["cost"] in (None, 0):
                same_grade["cost"] = recommendation_cost
            if same_grade["tokens"] is None:
                same_grade["tokens"] = top_tier["tokens"]
            if same_grade["score"] is None:
                same_grade["score"] = top_tier["score"]
        elif top_tier["items"]:
            tiers.append(top_tier)
        else:
            recommendation_cost = None
        if not tiers:
            tiers = [top_tier]
        tiers.sort(key=lambda tier: (tier["cost"] is None, tier["cost"] or 0, tier["grade"]))
        selected_items = top_tier["items"]
        token_values = [tier["tokens"] for tier in tiers if tier["tokens"] is not None]
        sets.append(
            {
                "set_id": set_id,
                "name": solution.get("name") or f"Gallery set {set_id}",
                "category_name": category_name,
                "category_slug": category_slug,
                "item_count": len(selected_items) or _int(solution.get("poolSize")),
                "best_grade": solution.get("grade"),
                "total_cost_coins": recommendation_cost,
                "total_score": _int(solution.get("score") or solution.get("baseGrade")),
                "max_tokens": max(token_values) if token_values else _int(solution.get("tokens")),
                "status": solution.get("status"),
                "items": selected_items,
                "tiers": tiers,
                "raw": solution,
            }
        )
    return {
        "tags": [_as_dict(item) for item in _as_list(base_data.get("tags"))],
        "sets": sets,
        "base": base_data,
        "solutions": solution_data,
        "captured_at": datetime.now(timezone.utc),
    }


async def collect_gallery_data(session: BrowserTransport) -> dict[str, Any]:
    # Keep a failed gallery request from leaving its paired request in
    # flight while the owning browser context is being torn down.
    base, solutions = await _gather_and_cancel_on_error(
        _get_json(session, GALLERY_URL), _get_json(session, GALLERY_SOLUTIONS_URL)
    )
    gallery = normalise_gallery(base, solutions)
    if not gallery["sets"]:
        raise RuntimeError("FUT.GG Gallery response returned no sets")
    return gallery


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
            external_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
            starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
            fingerprint TEXT[] NOT NULL DEFAULT '{}', payload JSONB NOT NULL DEFAULT '{}',
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (kind, source, external_id)
        );
        CREATE TABLE IF NOT EXISTS sbc_details (
            event_id BIGINT PRIMARY KEY REFERENCES market_events(id) ON DELETE CASCADE,
            set_name TEXT NOT NULL, category TEXT, total_cost_coins BIGINT,
            repeatable BOOLEAN NOT NULL DEFAULT false, reward_card_id BIGINT,
            reward_description TEXT, expires_at TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS sbc_challenges (
            id BIGSERIAL PRIMARY KEY, event_id BIGINT NOT NULL REFERENCES market_events(id) ON DELETE CASCADE,
            challenge_name TEXT NOT NULL, requirements JSONB NOT NULL DEFAULT '{}',
            estimated_cost_coins BIGINT, display_order INTEGER NOT NULL DEFAULT 0
        );
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS total_cost_pc BIGINT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS repeat_count INTEGER;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS repeat_refresh_seconds INTEGER;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS image_url TEXT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS futgg_url TEXT;
        ALTER TABLE sbc_details ADD COLUMN IF NOT EXISTS detail_scraped_at TIMESTAMPTZ;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS external_challenge_id INTEGER;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS estimated_cost_pc BIGINT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS image_url TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS reward JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_console_uuid TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_pc_uuid TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_console_url TEXT;
        ALTER TABLE sbc_challenges ADD COLUMN IF NOT EXISTS solution_pc_url TEXT;
        CREATE TABLE IF NOT EXISTS sbc_solution_items (
            challenge_id BIGINT NOT NULL REFERENCES sbc_challenges(id) ON DELETE CASCADE,
            platform TEXT NOT NULL, squad_uuid TEXT NOT NULL, position_idx SMALLINT NOT NULL,
            player_ea_id BIGINT NOT NULL, source_card_id BIGINT,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (challenge_id, platform, position_idx)
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_tags (
            tag_id INTEGER PRIMARY KEY, game_year SMALLINT NOT NULL DEFAULT 27,
            name TEXT NOT NULL, colour TEXT, description TEXT, priority INTEGER,
            payload JSONB NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_sets (
            set_id INTEGER PRIMARY KEY, game_year SMALLINT NOT NULL DEFAULT 27,
            name TEXT NOT NULL, category_name TEXT, category_slug TEXT, item_count INTEGER,
            best_grade TEXT, total_cost_coins BIGINT, total_score BIGINT, max_tokens INTEGER,
            status TEXT, payload JSONB NOT NULL DEFAULT '{}', solution_payload JSONB NOT NULL DEFAULT '{}',
            source_updated_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS futgg_gallery_solution_items (
            set_id INTEGER NOT NULL REFERENCES futgg_gallery_sets(set_id) ON DELETE CASCADE,
            grade TEXT NOT NULL DEFAULT '', position_idx SMALLINT NOT NULL,
            card_ea_id BIGINT, player_ea_id BIGINT, source_card_id BIGINT,
            price BIGINT, score INTEGER, overall SMALLINT, club_ea_id INTEGER,
            nation_ea_id INTEGER, rarity_ea_id INTEGER,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (set_id, grade, position_idx)
        );
        """
    )


async def _resolve_card_id(
    conn: asyncpg.Connection,
    player_ea_id: int,
    *,
    overall: Optional[int] = None,
    club_ea_id: Optional[int] = None,
    nation_ea_id: Optional[int] = None,
    captured_price: Optional[int] = None,
) -> Optional[int]:
    try:
        value = await conn.fetchval(
            """
            SELECT p.source_card_id FROM futgg_players p
            LEFT JOIN LATERAL (
              SELECT h.lowest_bin FROM futgg_bin_history h
              WHERE h.source_card_id=p.source_card_id
              ORDER BY h.captured_at DESC LIMIT 1
            ) market ON true
            WHERE p.game_year = $1 AND p.is_active AND p.source_player_id = $2
            ORDER BY (p.rating=$3) DESC NULLS LAST,
                     (p.club_source_id=$4) DESC NULLS LAST,
                     (p.nation_source_id=$5) DESC NULLS LAST,
                     abs(coalesce(market.lowest_bin,$6)-$6) ASC NULLS LAST,
                     p.is_tradeable DESC NULLS LAST,p.rating ASC NULLS LAST,p.source_card_id
            LIMIT 1
            """,
            GAME_YEAR, player_ea_id, overall, club_ea_id, nation_ea_id, captured_price,
        )
        return int(value) if value is not None else None
    except asyncpg.exceptions.UndefinedTableError:
        return None


async def write_sbc(conn: asyncpg.Connection, sbc: dict[str, Any]) -> tuple[int, int]:
    row = await conn.fetchrow(
        """
        INSERT INTO market_events (
            kind, source, external_id, title, description, starts_at, ends_at, fingerprint, payload
        ) VALUES ('sbc', 'futgg', $1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (kind, source, external_id) DO UPDATE SET
            title=EXCLUDED.title, description=EXCLUDED.description,
            starts_at=COALESCE(EXCLUDED.starts_at, market_events.starts_at),
            ends_at=EXCLUDED.ends_at, fingerprint=EXCLUDED.fingerprint,
            payload=EXCLUDED.payload, updated_at=now()
        RETURNING id
        """,
        sbc["external_id"], sbc["name"], sbc.get("description"),
        sbc.get("starts_at"), sbc.get("ends_at"), build_fingerprint(sbc),
        json.dumps(sbc["raw"], default=str),
    )
    event_id = int(row["id"])
    await conn.execute(
        """
        INSERT INTO sbc_details (
            event_id, set_name, category, total_cost_coins, total_cost_pc,
            repeatable, repeat_count, repeat_refresh_seconds, reward_card_id,
            reward_description, expires_at, image_url, futgg_url, detail_scraped_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,$12,now())
        ON CONFLICT (event_id) DO UPDATE SET
            set_name=EXCLUDED.set_name, category=EXCLUDED.category,
            total_cost_coins=EXCLUDED.total_cost_coins, total_cost_pc=EXCLUDED.total_cost_pc,
            repeatable=EXCLUDED.repeatable, repeat_count=EXCLUDED.repeat_count,
            repeat_refresh_seconds=EXCLUDED.repeat_refresh_seconds,
            reward_description=EXCLUDED.reward_description, expires_at=EXCLUDED.expires_at,
            image_url=EXCLUDED.image_url, futgg_url=EXCLUDED.futgg_url, detail_scraped_at=now()
        """,
        event_id, sbc["name"], sbc.get("category"), sbc.get("cost_console"), sbc.get("cost_pc"),
        sbc.get("repeatable", False), sbc.get("repeat_count"), sbc.get("repeat_refresh_seconds"),
        sbc.get("reward_label"), sbc.get("ends_at"), sbc.get("image_url"), sbc.get("futgg_url"),
    )
    await conn.execute("DELETE FROM sbc_challenges WHERE event_id=$1", event_id)
    item_count = 0
    for challenge in sbc["challenges"]:
        challenge_id = await conn.fetchval(
            """
            INSERT INTO sbc_challenges (
                event_id, challenge_name, requirements, estimated_cost_coins,
                estimated_cost_pc, display_order, external_challenge_id, image_url,
                reward, solution_console_uuid, solution_pc_uuid,
                solution_console_url, solution_pc_url
            ) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13)
            RETURNING id
            """,
            event_id, challenge["name"], json.dumps(challenge["requirements"]),
            challenge.get("cost_console"), challenge.get("cost_pc"), challenge["display_order"],
            challenge.get("external_challenge_id"), challenge.get("image_url"),
            json.dumps(challenge.get("reward") or []), challenge.get("solution_console_uuid"),
            challenge.get("solution_pc_uuid"), challenge.get("solution_console_url"),
            challenge.get("solution_pc_url"),
        )
        for platform in ("console", "pc"):
            uuid = challenge.get(f"solution_{platform}_uuid")
            if not uuid:
                continue
            for item in sbc.get("squads", {}).get(uuid, []):
                source_card_id = await _resolve_card_id(conn, item["player_ea_id"])
                await conn.execute(
                    """
                    INSERT INTO sbc_solution_items (
                        challenge_id, platform, squad_uuid, position_idx,
                        player_ea_id, source_card_id, captured_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,now())
                    """,
                    challenge_id, platform, uuid, item["position_idx"],
                    item["player_ea_id"], source_card_id,
                )
                item_count += 1
    return event_id, item_count


async def write_gallery(conn: asyncpg.Connection, gallery: dict[str, Any]) -> int:
    for tag in gallery["tags"]:
        tag_id = _int(tag.get("id") or tag.get("tagId"))
        if tag_id is None:
            continue
        await conn.execute(
            """
            INSERT INTO futgg_gallery_tags (tag_id,game_year,name,colour,description,priority,payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
            ON CONFLICT (tag_id) DO UPDATE SET name=EXCLUDED.name, colour=EXCLUDED.colour,
                description=EXCLUDED.description, priority=EXCLUDED.priority,
                payload=EXCLUDED.payload, updated_at=now()
            """,
            tag_id, GAME_YEAR, tag.get("name") or f"Tag {tag_id}", tag.get("color"),
            tag.get("description"), _int(tag.get("priority")), json.dumps(tag),
        )
    active_ids: list[int] = []
    for gallery_set in gallery["sets"]:
        active_ids.append(gallery_set["set_id"])
        await conn.execute(
            """
            INSERT INTO futgg_gallery_sets (
                set_id,game_year,name,category_name,category_slug,item_count,best_grade,
                total_cost_coins,total_score,max_tokens,status,payload,solution_payload,
                source_updated_at,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,now())
            ON CONFLICT (set_id) DO UPDATE SET game_year=EXCLUDED.game_year,name=EXCLUDED.name,
                category_name=EXCLUDED.category_name,category_slug=EXCLUDED.category_slug,
                item_count=EXCLUDED.item_count,best_grade=EXCLUDED.best_grade,
                total_cost_coins=EXCLUDED.total_cost_coins,total_score=EXCLUDED.total_score,
                max_tokens=EXCLUDED.max_tokens,status=EXCLUDED.status,payload=EXCLUDED.payload,
                solution_payload=EXCLUDED.solution_payload,source_updated_at=EXCLUDED.source_updated_at,
                updated_at=now()
            """,
            gallery_set["set_id"], GAME_YEAR, gallery_set["name"],
            gallery_set.get("category_name"), gallery_set.get("category_slug"),
            gallery_set.get("item_count"), gallery_set.get("best_grade"),
            gallery_set.get("total_cost_coins"), gallery_set.get("total_score"),
            gallery_set.get("max_tokens"), gallery_set.get("status"),
            json.dumps(gallery["base"], default=str), json.dumps(gallery_set["raw"], default=str),
            gallery["captured_at"],
        )
        await conn.execute("DELETE FROM futgg_gallery_solution_items WHERE set_id=$1", gallery_set["set_id"])
        for tier in gallery_set.get("tiers") or [{
            "grade": gallery_set.get("best_grade"), "items": gallery_set.get("items") or []
        }]:
            grade = str(tier.get("grade") or "")
            for item in tier.get("items") or []:
                player_ea_id = _int(item.get("player_ea_id"))
                source_card_id = await _resolve_card_id(
                    conn, player_ea_id,
                    overall=_int(item.get("overall")),
                    club_ea_id=_int(item.get("club_ea_id")),
                    nation_ea_id=_int(item.get("nation_ea_id")),
                    captured_price=_int(item.get("price")),
                ) if player_ea_id else None
                await conn.execute(
                """
                INSERT INTO futgg_gallery_solution_items (
                    set_id,grade,position_idx,card_ea_id,player_ea_id,source_card_id,
                    price,score,overall,club_ea_id,nation_ea_id,rarity_ea_id,captured_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
                """,
                    gallery_set["set_id"], grade, item["position_idx"], item.get("card_ea_id"),
                    item.get("player_ea_id"), source_card_id, item.get("price"), item.get("score"),
                    item.get("overall"), item.get("club_ea_id"), item.get("nation_ea_id"),
                    item.get("rarity_ea_id"),
                )
    return len(active_ids)


async def _gather_and_cancel_on_error(*awaitables: Any) -> list[Any]:
    """Gather concurrent work and fully stop siblings before propagating errors.

    `asyncio.gather` returns on the first exception but does not cancel its
    other awaitables. That is unsafe when the caller owns a browser and closes
    it in `finally`: those requests keep running against a closed context.
    """
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _collect_remote_content() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from playwright.async_api import async_playwright

    async with Stealth().use_async(async_playwright()) as patched_playwright:
        playwright = patched_playwright
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
        try:
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                locale='en-GB',
            )
            warmup = await context.new_page()
            response = await warmup.goto(
                SITE_ROOT, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000
            )
            if not response or response.status != 200:
                raise RuntimeError(
                    f"FUT.GG browser warm-up returned HTTP {response.status if response else 'none'}"
                )
            await warmup.wait_for_timeout(1500)
            pages = [warmup] + [await context.new_page() for _ in range(CONCURRENCY + 1)]
            session = BrowserTransport(pages)
            # If either collection branch fails, asyncio.gather() propagates
            # the exception but leaves its sibling running. Closing Chromium in
            # the `finally` block then aborts those in-flight requests, creating
            # misleading "browser/page was closed" errors and detached navigations.
            # Cancel and drain both branches before browser.close() can run.
            sbcs, gallery = await _gather_and_cancel_on_error(
                collect_sbc_data(session), collect_gallery_data(session)
            )
        finally:
            await browser.close()
    return sbcs, gallery


async def _push_to_backend(
    sbcs: list[dict[str, Any]], gallery: dict[str, Any]
) -> dict[str, Any]:
    if not INGEST_URL or not INGEST_TOKEN:
        raise RuntimeError(
            "FUTGG_CONTENT_INGEST_URL and FUTGG_CONTENT_SYNC_TOKEN must both be configured"
        )
    import aiohttp

    body = json.dumps(
        {"game_year": GAME_YEAR, "sbcs": sbcs, "gallery": gallery}, default=str
    )
    timeout = aiohttp.ClientTimeout(total=max(60, TIMEOUT_SECONDS * 4))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            INGEST_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Content-Sync-Token": INGEST_TOKEN,
                "User-Agent": "EAFC.Live-FUTGG-ContentSync/1.0",
            },
        ) as response:
            response_text = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"backend ingest returned HTTP {response.status}: {response_text[:300]}"
                )
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("backend ingest returned invalid JSON") from exc
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError("backend ingest did not confirm success")
            return result


async def crawl_once() -> bool:
    if DIAGNOSTIC:
        sbcs, gallery = await _collect_remote_content()
        log.info("diagnostic success: sbcs=%d gallery_sets=%d tags=%d", len(sbcs), len(gallery["sets"]), len(gallery["tags"]))
        return True
    if INGEST_URL or INGEST_TOKEN:
        sbcs, gallery = await _collect_remote_content()
        result = await _push_to_backend(sbcs, gallery)
        detail = (
            f"sbcs={result.get('sbcs', 0)} solution_items={result.get('solution_items', 0)} "
            f"gallery_sets={result.get('gallery_sets', 0)} gallery_tags={result.get('gallery_tags', 0)}"
        )
        # In backend-ingest mode we used to return here without recording a
        # successful heartbeat. Failures *did* record one in _main(), which
        # made Collector Status sticky-red forever after any transient 403/500
        # even when a later crawl succeeded. Clear that stale failure on every
        # successful ingest, just like direct-DB mode does below.
        if DATABASE_URL:
            await heartbeat(DATABASE_URL, WORKER, True, detail)
        log.info("FUT.GG content sync complete via backend: %s", detail)
        return True
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")
    # The shared Railway/Postgres endpoint occasionally accepts TCP and then
    # drops the connection during asyncpg's TLS upgrade.  Other workers use
    # this bounded retry helper for that exact transient failure; ContentSync
    # must do the same instead of turning a one-second database blip into a
    # failed 15-minute cycle.
    conn = await connect_with_retry(DATABASE_URL, command_timeout=120)
    locked = False
    try:
        locked = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY))
        if not locked:
            log.info("another FUT.GG content sync is already running; exiting cleanly")
            return True
        # Initialise the read schema before network collection. Even if FUT.GG
        # is temporarily unavailable, the site returns a clean empty state
        # instead of falling back to stale EasySBC rows or raising table errors.
        await ensure_schema(conn)
        sbcs, gallery = await _collect_remote_content()
        solution_items = 0
        async with conn.transaction():
            for sbc in sbcs:
                _, count = await write_sbc(conn, sbc)
                solution_items += count
            await conn.execute(
                """
                UPDATE market_events SET ends_at=now(),updated_at=now()
                WHERE kind='sbc' AND source='futgg'
                  AND NOT (external_id=ANY($1::text[]))
                  AND (ends_at IS NULL OR ends_at>now())
                """,
                [sbc["external_id"] for sbc in sbcs],
            )
            gallery_count = await write_gallery(conn, gallery)
        detail = (
            f"sbcs={len(sbcs)} solution_items={solution_items} "
            f"gallery_sets={gallery_count} gallery_tags={len(gallery['tags'])}"
        )
        await heartbeat(conn, WORKER, True, detail)
        log.info("FUT.GG content sync complete: %s", detail)
        return True
    except Exception as exc:
        await heartbeat(conn, WORKER, False, str(exc))
        raise
    finally:
        if locked:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        await conn.close()


async def _main() -> int:
    try:
        return 0 if await crawl_once() else 1
    except Exception as exc:
        log.exception("FUT.GG content sync failed")
        if DATABASE_URL:
            await heartbeat(DATABASE_URL, WORKER, False, str(exc))
        await alert(f"FUT.GG content sync failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

