from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import aiohttp

from app.db import get_core_pool

log = logging.getLogger("leak_monitor")

X_API_BASE = "https://api.x.com/2"
CARD_KEYWORDS = (
    "totw", "team of the week", "icon", "hero", "trailblazers", "ultimate scream",
    "road to the knockout", "rttk", "road to the final", "rttf", "winter wildcards",
    "future stars", "team of the year", "toty", "team of the season", "tots",
    "futties", "flashback", "moments", "sbc", "objective", "evolution", "evo",
    "promo", "special card", "fc pro", "thunderstruck", "centurions", "foundations",
    "pack", "packs",
)

# Discord/internal publishing is intentionally stricter than the timeline read.
# We still read each tracked account once, but only content/leak signals are fanned out.
CONTENT_SIGNAL_KEYWORDS = (
    "totw", "team of the week", "icon", "hero", "rttk", "rttf", "toty", "tots",
    "futties", "future stars", "winter wildcards", "trailblazers", "thunderstruck",
    "centurions", "fc pro", "promo", "special card", "sbc", "objective", "evolution",
    "evo", "pack", "packs", "player", "card",
)
LEAK_INTENT_KEYWORDS = (
    "leak", "leaked", "leaks", "rumour", "rumor", "coming", "upcoming", "tonight",
    "tomorrow", "confirmed", "expected", "releasing", "release", "drops", "dropping",
    "will be", "set to", "next promo", "next team", "new sbc", "new objective",
    "new evolution", "new evo", "new pack",
)
ADVERTISING_PHRASES = (
    "join our", "join us", "our discord", "our group", "our server", "we offer",
    "limited spots", "sign up", "subscribe", "subscription", "membership", "premium group",
    "trading discord", "exclusive community", "giveaway", "giveaways", "max capacity",
    "for the same price", "we've helped", "we have helped", "our fut content alerts",
    "content alerts updates", "lists are ready", "make a million instead",
)
GENERAL_CHAT_PHRASES = (
    "if they patch", "generational game", "defending is", "best years for the fut market",
    "market inbound", "will rise like crazy", "champs", "rivals gameplay", "streamers = playing",
    "web app =", "pc users =", "xbox users", "ps users",
)

# Common leaker formats: "91 Mbappe - TOTY", "Mbappe 91 TOTY", "91-rated Mbappe Icon".
_RATING_NAME = re.compile(r"\b(?P<rating>[7-9]\d)\s*(?:rated|ovr)?\s*[-:|]?\s*(?P<name>[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,40})", re.I)
_NAME_RATING = re.compile(r"\b(?P<name>[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,40})\s*[-:|]?\s*(?P<rating>[7-9]\d)\s*(?:rated|ovr)?\b", re.I)


@dataclass
class ParsedLeak:
    player_name: Optional[str] = None
    rating: Optional[int] = None
    card_type: Optional[str] = None
    promo_name: Optional[str] = None
    parse_method: str = "regex"
    parse_confidence: float = 0.0

    @property
    def useful(self) -> bool:
        return bool(self.player_name or self.card_type or self.promo_name)


class LeakMonitor:
    """Single-read FC27 X leak monitor.

    Each timeline response is processed once. Only posts classified as useful
    FC content/leak signals are fanned out to Discord and the internal pipeline.
    Ordinary tweets, adverts, banter and generic market opinions are ignored.
    """

    def __init__(self) -> None:
        self.bearer = os.getenv("X_BEARER_TOKEN", "").strip()
        self.discord_webhook = os.getenv("LEAK_DISCORD_WEBHOOK_URL", "").strip()
        self.discord_username = os.getenv("LEAK_DISCORD_USERNAME", "EAFC.Live Leak Feed").strip()
        self.discord_avatar_url = os.getenv("LEAK_DISCORD_AVATAR_URL", "").strip()
        self.discord_role_mention = os.getenv("LEAK_DISCORD_ROLE_MENTION", "").strip()
        self.poll_seconds = max(60, int(os.getenv("LEAK_POLL_SECONDS", "180")))
        self.tracked_accounts = [x.strip().lstrip("@") for x in os.getenv("LEAK_X_ACCOUNTS", "").split(",") if x.strip()]
        self.discovery_queries = [x.strip() for x in os.getenv("LEAK_DISCOVERY_QUERIES", "FC27 leak,FUT leak").split(",") if x.strip()]
        self.discovery_enabled = os.getenv("LEAK_DISCOVERY_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.discovery_interval_seconds = max(3600, int(os.getenv("LEAK_DISCOVERY_INTERVAL_SECONDS", "14400")))
        self.use_openai = bool(os.getenv("OPENAI_API_KEY")) and os.getenv("LEAK_OPENAI_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.openai_model = os.getenv("LEAK_OPENAI_MODEL", "gpt-4o-mini")
        self._last_discovery = 0.0

    def validate(self) -> None:
        if not self.bearer:
            raise RuntimeError("X_BEARER_TOKEN is required")
        if not self.tracked_accounts:
            raise RuntimeError("LEAK_X_ACCOUNTS must contain at least one X username")

    async def run_forever(self) -> None:
        self.validate()
        await self.ensure_sources()
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.poll_once()
                if self.discovery_enabled and started - self._last_discovery >= self.discovery_interval_seconds:
                    await self.run_discovery()
                    self._last_discovery = started
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("leak poll failed")
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(1, self.poll_seconds - elapsed))

    async def _x_get(self, session: aiohttp.ClientSession, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.bearer}"}
        async with session.get(f"{X_API_BASE}{path}", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"X API {resp.status}: {body[:400]}")
            return json.loads(body)

    async def ensure_sources(self) -> None:
        pool = await get_core_pool()
        async with aiohttp.ClientSession() as session, pool.acquire() as conn:
            for username in self.tracked_accounts:
                row = await conn.fetchrow("SELECT id,x_user_id FROM leak_sources WHERE lower(username)=lower($1)", username)
                if row:
                    continue
                data = await self._x_get(
                    session,
                    f"/users/by/username/{username}",
                    {"user.fields": "id,name,username,public_metrics"},
                )
                user = data.get("data") or {}
                if not user.get("id"):
                    log.warning("could not resolve X account @%s", username)
                    continue
                followers = ((user.get("public_metrics") or {}).get("followers_count"))
                await conn.execute(
                    """INSERT INTO leak_sources(x_user_id,username,display_name,followers_count)
                       VALUES($1,$2,$3,$4)
                       ON CONFLICT(x_user_id) DO UPDATE SET username=EXCLUDED.username,
                         display_name=EXCLUDED.display_name,followers_count=EXCLUDED.followers_count,updated_at=now()""",
                    str(user["id"]), user.get("username") or username, user.get("name"), followers,
                )
                log.info("resolved leak source @%s -> %s", username, user["id"])

    async def poll_once(self) -> int:
        pool = await get_core_pool()
        processed = 0
        async with aiohttp.ClientSession() as session, pool.acquire() as conn:
            sources = await conn.fetch("SELECT * FROM leak_sources WHERE active=true ORDER BY id")
            for source in sources:
                params = {
                    "max_results": "25",
                    "exclude": "retweets,replies",
                    "tweet.fields": "id,text,created_at,public_metrics,attachments",
                    "expansions": "attachments.media_keys",
                    "media.fields": "media_key,type,url,preview_image_url",
                }
                if source["last_tweet_id"]:
                    params["since_id"] = str(source["last_tweet_id"])
                payload = await self._x_get(session, f"/users/{source['x_user_id']}/tweets", params)
                tweets = payload.get("data") or []
                if not tweets:
                    await conn.execute("UPDATE leak_sources SET last_polled_at=now(),updated_at=now() WHERE id=$1", source["id"])
                    continue

                newest = max(tweets, key=lambda t: int(t["id"]))["id"]
                if not source["last_tweet_id"]:
                    # First contact is a baseline only. Do not dump an account's historical feed.
                    await conn.execute(
                        "UPDATE leak_sources SET last_tweet_id=$1,last_polled_at=now(),updated_at=now() WHERE id=$2",
                        str(newest), source["id"],
                    )
                    log.info("baselined leak source @%s at tweet %s", source["username"], newest)
                    continue

                media_by_key = {m.get("media_key"): m for m in (payload.get("includes") or {}).get("media", [])}
                # Oldest first so Discord order and since_id progression are deterministic.
                tweets.sort(key=lambda t: int(t["id"]))
                for tweet in tweets:
                    media_urls = self._media_urls(tweet, media_by_key)
                    if await self._ingest_tweet(conn, session, source, tweet, media_urls):
                        processed += 1
                await conn.execute(
                    "UPDATE leak_sources SET last_tweet_id=$1,last_polled_at=now(),updated_at=now() WHERE id=$2",
                    str(newest), source["id"],
                )
        return processed

    @staticmethod
    def _media_urls(tweet: Dict[str, Any], media_by_key: Dict[str, Dict[str, Any]]) -> List[str]:
        urls: List[str] = []
        for key in (tweet.get("attachments") or {}).get("media_keys", []):
            media = media_by_key.get(key) or {}
            url = media.get("url") or media.get("preview_image_url")
            if url:
                urls.append(url)
        return urls

    async def _ingest_tweet(
        self,
        conn: Any,
        session: aiohttp.ClientSession,
        source: Any,
        tweet: Dict[str, Any],
        media_urls: List[str],
    ) -> bool:
        tweet_id = str(tweet["id"])
        username = str(source["username"])
        tweet_url = f"https://x.com/{username}/status/{tweet_id}"
        inserted = await conn.fetchval(
            """INSERT INTO leak_tweets(tweet_id,source_id,tweeted_at,text,tweet_url,media_urls,public_metrics)
               VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)
               ON CONFLICT(tweet_id) DO NOTHING RETURNING id""",
            tweet_id,
            source["id"],
            self._parse_dt(tweet.get("created_at")),
            tweet.get("text") or "",
            tweet_url,
            json.dumps(media_urls),
            json.dumps(tweet.get("public_metrics") or {}),
        )
        if not inserted:
            return False

        text = tweet.get("text") or ""
        ocr_text = await self._ocr_images(media_urls) if media_urls else ""
        combined = "\n".join(x for x in [text, ocr_text] if x).strip()
        parsed = self.parse_regex(combined)
        if (not parsed.useful or parsed.parse_confidence < 0.70) and self.use_openai:
            fallback = await self._parse_with_openai(combined, media_urls)
            if fallback and fallback.parse_confidence > parsed.parse_confidence:
                parsed = fallback

        await conn.execute("UPDATE leak_tweets SET ocr_text=$1,parsed_at=now() WHERE tweet_id=$2", ocr_text or None, tweet_id)
        relevant = self.is_relevant_content(combined, parsed)
        if not relevant:
            log.info("ignored non-leak tweet @%s/%s", username, tweet_id)
            return True

        if parsed.useful:
            reliability = self._reliability_score(source, tweet.get("public_metrics") or {}, parsed.parse_confidence)
            tag = "high" if reliability >= 0.78 else "medium" if reliability >= 0.55 else "rumor"
            matches = await self._match_player(conn, parsed)
            await conn.execute(
                """INSERT INTO leak_signals(
                       tweet_id,source_id,player_name,rating,card_type,promo_name,parse_method,
                       parse_confidence,reliability_score,confidence_tag,matched_source_card_id,
                       matched_source_player_id,metadata
                   ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
                   ON CONFLICT(tweet_id) DO UPDATE SET
                     player_name=EXCLUDED.player_name,rating=EXCLUDED.rating,card_type=EXCLUDED.card_type,
                     promo_name=EXCLUDED.promo_name,parse_method=EXCLUDED.parse_method,
                     parse_confidence=EXCLUDED.parse_confidence,reliability_score=EXCLUDED.reliability_score,
                     confidence_tag=EXCLUDED.confidence_tag,matched_source_card_id=EXCLUDED.matched_source_card_id,
                     matched_source_player_id=EXCLUDED.matched_source_player_id,metadata=EXCLUDED.metadata,updated_at=now()""",
                tweet_id, source["id"], parsed.player_name, parsed.rating, parsed.card_type, parsed.promo_name,
                parsed.parse_method, parsed.parse_confidence, reliability, tag,
                matches.get("source_card_id"), matches.get("source_player_id"),
                json.dumps({"engagement": tweet.get("public_metrics") or {}, "ocr_used": bool(ocr_text)}),
            )

        if self.discord_webhook:
            try:
                await self._post_discord(session, username, text, tweet_url, media_urls)
                await conn.execute("UPDATE leak_tweets SET discord_delivered_at=now() WHERE tweet_id=$1", tweet_id)
            except Exception:
                log.exception("Discord leak repost failed for %s", tweet_id)
        return True

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def parse_regex(cls, text: str) -> ParsedLeak:
        cleaned = re.sub(r"https?://\S+", " ", text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        lowered = cleaned.lower()
        card_type = next((k.upper() if len(k) <= 5 else k.title() for k in CARD_KEYWORDS if k in lowered), None)
        match = _RATING_NAME.search(cleaned) or _NAME_RATING.search(cleaned)
        player_name: Optional[str] = None
        rating: Optional[int] = None
        if match:
            rating = int(match.group("rating"))
            player_name = cls._clean_player_name(match.group("name"), card_type)
        confidence = 0.0
        if player_name:
            confidence += 0.45
        if rating:
            confidence += 0.25
        if card_type:
            confidence += 0.25
        if any(w in lowered for w in ("leak", "coming", "confirmed", "tonight", "tomorrow", "sbc", "objective")):
            confidence += 0.05
        return ParsedLeak(
            player_name=player_name,
            rating=rating,
            card_type=card_type,
            promo_name=card_type,
            parse_method="regex",
            parse_confidence=min(confidence, 1.0),
        )

    @classmethod
    def is_relevant_content(cls, text: str, parsed: ParsedLeak) -> bool:
        """Return True only for actionable FC content/leak posts.

        Tracked accounts often mix leaks with adverts, banter and opinions. Those
        posts are deliberately rejected before both Discord and internal signals.
        """
        cleaned = re.sub(r"https?://\S+", " ", text or "")
        lowered = re.sub(r"\s+", " ", cleaned).strip().lower()
        if not lowered:
            return False
        if any(phrase in lowered for phrase in ADVERTISING_PHRASES):
            return False
        if any(phrase in lowered for phrase in GENERAL_CHAT_PHRASES):
            return False

        has_content = bool(parsed.card_type) or any(k in lowered for k in CONTENT_SIGNAL_KEYWORDS)
        has_intent = any(k in lowered for k in LEAK_INTENT_KEYWORDS)
        concrete_player_card = bool(parsed.player_name and parsed.rating and parsed.card_type)

        # A concrete named/rated special card is strong enough on its own.
        if concrete_player_card:
            return True
        # Otherwise require both FC content and a forward-looking/leak/release cue.
        return has_content and has_intent

    @staticmethod
    def _clean_player_name(value: str, card_type: Optional[str]) -> str:
        value = re.sub(r"\b(rated|ovr|rating|leak|leaked|coming|confirmed|fc\s*27|fut)\b", " ", value, flags=re.I)
        if card_type:
            value = re.sub(re.escape(card_type), " ", value, flags=re.I)
        value = re.sub(r"\s+", " ", value).strip(" -:|.,")
        return value[:80]

    async def _ocr_images(self, media_urls: Iterable[str]) -> str:
        if not self.use_openai:
            return ""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            content: List[Dict[str, Any]] = [{"type": "text", "text": "OCR this FC27 leak image. Return only visible text, preserving names, ratings and card/promo labels."}]
            for url in list(media_urls)[:4]:
                content.append({"type": "image_url", "image_url": {"url": url}})
            response = await client.chat.completions.create(
                model=self.openai_model,
                temperature=0,
                max_tokens=400,
                messages=[{"role": "user", "content": content}],
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            log.exception("image OCR failed")
            return ""

    async def _parse_with_openai(self, text: str, media_urls: Iterable[str]) -> Optional[ParsedLeak]:
        if not text and not list(media_urls):
            return None
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            prompt = (
                "Extract a possible EA SPORTS FC27 leaked card. Return strict JSON with keys "
                "player_name, rating, card_type, promo_name, confidence. Use null when unknown. "
                "Confidence is 0-1. Do not infer a player or rating that is not present.\n\n" + text[:3500]
            )
            response = await client.chat.completions.create(
                model=self.openai_model,
                temperature=0,
                max_tokens=180,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            raw = json.loads(response.choices[0].message.content or "{}")
            return ParsedLeak(
                player_name=(raw.get("player_name") or None),
                rating=int(raw["rating"]) if raw.get("rating") is not None else None,
                card_type=(raw.get("card_type") or None),
                promo_name=(raw.get("promo_name") or None),
                parse_method="openai",
                parse_confidence=max(0.0, min(float(raw.get("confidence") or 0), 1.0)),
            )
        except Exception:
            log.exception("NLP leak parsing failed")
            return None

    @staticmethod
    def _reliability_score(source: Any, metrics: Dict[str, Any], parse_confidence: float) -> float:
        followers = max(0, int(source["followers_count"] or 0))
        accuracy = float(source["historical_accuracy"] or 0.50)
        likes = max(0, int(metrics.get("like_count") or 0))
        retweets = max(0, int(metrics.get("retweet_count") or 0))
        # Saturating normalisations stop huge accounts/tweets dominating the score.
        follower_score = min(1.0, followers / 100_000)
        engagement_score = min(1.0, (likes + retweets * 2) / 5_000)
        score = 0.50 * accuracy + 0.20 * follower_score + 0.15 * engagement_score + 0.15 * parse_confidence
        return round(max(0.0, min(score, 1.0)), 4)

    async def _match_player(self, conn: Any, parsed: ParsedLeak) -> Dict[str, Optional[int]]:
        if not parsed.player_name:
            return {"source_card_id": None, "source_player_id": None}
        # Match against the existing FC market catalogue. Rating is used when available,
        # but the match remains optional; an unmatched leak still persists as a signal.
        if parsed.rating:
            row = await conn.fetchrow(
                """SELECT source_card_id,source_player_id FROM futgg_players
                   WHERE game_year=27 AND rating=$1
                     AND (lower(COALESCE(display_name,name))=lower($2) OR lower(name)=lower($2))
                   ORDER BY source_card_id DESC LIMIT 1""",
                parsed.rating, parsed.player_name,
            )
        else:
            row = await conn.fetchrow(
                """SELECT source_card_id,source_player_id FROM futgg_players
                   WHERE game_year=27
                     AND (lower(COALESCE(display_name,name))=lower($1) OR lower(name)=lower($1))
                   ORDER BY rating DESC NULLS LAST,source_card_id DESC LIMIT 1""",
                parsed.player_name,
            )
        return {
            "source_card_id": int(row["source_card_id"]) if row else None,
            "source_player_id": int(row["source_player_id"]) if row and row["source_player_id"] is not None else None,
        }

    async def _post_discord(
        self,
        session: aiohttp.ClientSession,
        username: str,
        text: str,
        tweet_url: str,
        media_urls: List[str],
    ) -> None:
        content = "\n".join(x for x in [self.discord_role_mention, text, tweet_url] if x).strip()
        payload: Dict[str, Any] = {
            "content": content[:2000],
            "username": self.discord_username or "EAFC.Live Leak Feed",
            "allowed_mentions": {"parse": ["roles"] if self.discord_role_mention else []},
        }
        if self.discord_avatar_url:
            payload["avatar_url"] = self.discord_avatar_url
        if media_urls:
            payload["embeds"] = [{"image": {"url": media_urls[0]}, "footer": {"text": f"@{username}"}}]

        for attempt in range(4):
            async with session.post(self.discord_webhook, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status < 400:
                    return
                body = await resp.text()
                if resp.status == 429 and attempt < 3:
                    try:
                        retry_after = float((json.loads(body) or {}).get("retry_after") or 1.0)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        retry_after = 1.0
                    await asyncio.sleep(max(0.25, retry_after) + 0.05)
                    continue
                raise RuntimeError(f"Discord webhook {resp.status}: {body[:300]}")

    async def run_discovery(self) -> int:
        pool = await get_core_pool()
        found = 0
        async with aiohttp.ClientSession() as session, pool.acquire() as conn:
            for query in self.discovery_queries:
                payload = await self._x_get(
                    session,
                    "/tweets/search/recent",
                    {
                        "query": f"({query}) -is:retweet lang:en",
                        "max_results": "20",
                        "tweet.fields": "id,text,created_at,public_metrics,author_id",
                        "expansions": "author_id",
                        "user.fields": "id,name,username,public_metrics",
                    },
                )
                users = {u.get("id"): u for u in (payload.get("includes") or {}).get("users", [])}
                for tweet in payload.get("data") or []:
                    parsed = self.parse_regex(tweet.get("text") or "")
                    if not self.is_relevant_content(tweet.get("text") or "", parsed):
                        continue
                    if parsed.parse_confidence < 0.50:
                        continue
                    user = users.get(tweet.get("author_id")) or {}
                    followers = int((user.get("public_metrics") or {}).get("followers_count") or 0)
                    metrics = tweet.get("public_metrics") or {}
                    engagement = int(metrics.get("like_count") or 0) + 2 * int(metrics.get("retweet_count") or 0)
                    candidate_score = min(1.0, parsed.parse_confidence * 0.6 + min(followers / 100000, 1) * 0.25 + min(engagement / 5000, 1) * 0.15)
                    if candidate_score < 0.45:
                        continue
                    tweet_id = str(tweet["id"])
                    username = user.get("username") or "unknown"
                    await conn.execute(
                        """INSERT INTO leak_discovery_candidates(
                               x_user_id,username,tweet_id,tweet_text,tweet_url,followers_count,public_metrics,candidate_score
                           ) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                           ON CONFLICT(tweet_id) DO NOTHING""",
                        str(user.get("id") or ""), username, tweet_id, tweet.get("text") or "",
                        f"https://x.com/{username}/status/{tweet_id}", followers, json.dumps(metrics), round(candidate_score, 4),
                    )
                    found += 1
        return found


async def recent_internal_signals(limit: int = 100) -> List[Dict[str, Any]]:
    """Structured-only leak context for internal market engines.

    This deliberately reads active_leak_signals, whose schema contains no tweet
    text, tweet URL, account username or media URL. Product code can consume the
    signal without accidentally redistributing source content.
    """
    pool = await get_core_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM active_leak_signals
               ORDER BY reliability_score DESC,created_at DESC LIMIT $1""",
            max(1, min(limit, 500)),
        )
        return [dict(r) for r in rows]
