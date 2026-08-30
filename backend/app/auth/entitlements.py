from __future__ import annotations

"""
Entitlements: the single place that decides what a user can do.

This module previously granted every feature to any logged-in user and
require_feature() was a no-op — the paywall did not exist (review issue C1).
It now computes a real tier from EAFC Live's own account and billing data:

  1. users.plan ('elite' | 'pro' | 'premium' | anything else -> free)
  2. users.premium_until / user_profiles.is_premium + premium_until
  3. An entitled Stripe-synced subscriptions row

and gates features through FEATURE_MATRIX. require_feature() raises
HTTP 402 (payment required) when the caller's tier doesn't include the
feature, which the frontend already understands (axios dispatches a
`premium:blocked` event on 402).
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Literal

import asyncpg
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

Feature = Literal[
    "smart_buy",
    "watchlist",
    "trade_finder",
    "deal_confidence",
    "backtest",
    "smart_trending",
    "portfolio_optimizer",
    "ai_copilot",
    "market_sentiment",
    "market_maker",
    "advanced_analytics",
    "leaderboard",
    "referrals",
    "bulk_trades",
    "fair_value",
    "undervalued_board",
    "anomaly_alerts",
    "realtime_alerts",
    # v2 - only 4 of these have a real route as of Phase 4 (ai_recommendations,
    # sbc_impact_predictions, investment_score_history, opportunity_feed) -
    # the other 6 are reserved names for features outside the v2 Phase 0-4
    # scope (backtesting/portfolio/journal/API-v2/briefing/early-refresh).
    "ai_recommendations",
    "sbc_impact_predictions",
    "investment_score_history",
    "advanced_backtesting",
    "portfolio_performance",
    "daily_ai_briefing",
    "early_data_refresh",
    "advanced_api_v2",
    "trade_journal",
    "opportunity_feed",
    # Real per-message LLM cost (unlike ai_copilot's free keyword matcher).
    "ai_chat",
    # The complete graded ledger and every time window. The recent headline
    # summary stays public as a trust signal - see track_record().
    "full_track_record",
]

# The product sells exactly two states to a signed-in customer: Free and
# Elite. "pro" survives only as an INPUT alias - `users.plan`/`users.tier`
# rows already say 'pro' or 'premium' in production, and a Stripe
# subscription resolved to "pro" - and every one of those now resolves to
# elite. See _resolve_tier() for why that is a bug fix, not a giveaway.
Tier = Literal["free", "elite"]

# Accepted spellings of "this row is paid", from manual grants, legacy
# columns and older plan names. All mean elite.
_PAID_PLAN_VALUES = {"pro", "premium", "elite"}

# ---- Limits ------------------------------------------------------------------
# ONE place. Every allowance the product enforces is here, env-overridable,
# and exported to the frontend through /api/entitlements so no component
# hardcodes a number that can drift from what the server actually enforces.
FREE_WATCHLIST_MAX = int(os.getenv("WATCHLIST_FREE_MAX", os.getenv("WATCHLIST_BASIC_MAX", "10")))
ELITE_WATCHLIST_MAX = int(os.getenv("WATCHLIST_ELITE_MAX", "500"))

FREE_ALERT_MAX = int(os.getenv("ALERTS_FREE_MAX", "3"))
ELITE_ALERT_MAX = int(os.getenv("ALERTS_ELITE_MAX", "100"))

FREE_OPEN_TRADE_MAX = int(os.getenv("PORTFOLIO_FREE_OPEN_TRADES", "5"))
ELITE_OPEN_TRADE_MAX = int(os.getenv("PORTFOLIO_ELITE_OPEN_TRADES", "1000"))

# How long a past_due subscription keeps access while Stripe retries the
# charge. Stripe's default dunning schedule runs about a week; this is
# deliberately a little shorter than that so the grace ends before Stripe
# gives up, and it is one documented number rather than an accident of a
# SQL predicate.
PAST_DUE_GRACE_DAYS = int(os.getenv("SUBSCRIPTION_PAST_DUE_GRACE_DAYS", "5"))

# How many complete opportunities a Free member gets per FC content day.
# See app/services/free_picks.py for how the specific picks are chosen.
FREE_DAILY_PICKS = int(os.getenv("FREE_DAILY_PICKS", "2"))

# Track Record: Free sees a headline summary over a recent window; Elite
# sees every window and the full ledger.
FREE_TRACK_RECORD_WINDOW_DAYS = int(os.getenv("FREE_TRACK_RECORD_WINDOW_DAYS", "30"))

# Portfolio history depth for Free, in days. Elite is unbounded.
FREE_PORTFOLIO_HISTORY_DAYS = int(os.getenv("FREE_PORTFOLIO_HISTORY_DAYS", "30"))

# Legacy aliases still imported elsewhere in the codebase.
BASIC_WATCHLIST_MAX = FREE_WATCHLIST_MAX
PREMIUM_WATCHLIST_MAX = ELITE_WATCHLIST_MAX
PRO_WATCHLIST_MAX = ELITE_WATCHLIST_MAX

FREE_TRENDING = {"timeframes": {"24h"}, "limit": 5, "smart": False}
ELITE_TRENDING = {"timeframes": {"4h", "6h", "24h"}, "limit": 50, "smart": True}

# Legacy aliases
BASIC_TRENDING = FREE_TRENDING
PREMIUM_TRENDING = ELITE_TRENDING
PRO_TRENDING = ELITE_TRENDING

# Feature -> minimum tier. Everything not listed here is free.
#
# There is no "pro" row any more. Every paid feature is "elite", because
# the product sells one paid plan. Previously `smart_buy`, `trade_finder`,
# `ai_chat` and friends were min_tier "pro" while `opportunity_feed` was
# "elite" - and _resolve_tier() returned "pro" for a Stripe subscriber, so
# a paying customer was denied the opportunity feed they had just paid
# for. Collapsing the ladder removes the class of bug entirely: there is
# one paid tier, and it has everything.
_TIER_ORDER: Dict[str, int] = {"free": 0, "elite": 1}

FEATURE_MIN_TIER: Dict[Feature, Tier] = {
    # ---- Free: enough to judge the product, not enough to replace it ----
    "ai_copilot": "free",
    "market_sentiment": "free",
    "leaderboard": "free",
    "referrals": "free",
    "watchlist": "free",          # size limited by tier, see _limits_for
    "fair_value": "free",         # fair value on a card you searched for
    "trade_journal": "free",      # basic portfolio, capped at FREE_OPEN_TRADE_MAX
    "portfolio_performance": "free",
    # ---- Elite: the paid product ----
    "opportunity_feed": "elite",
    "trade_finder": "elite",
    "smart_buy": "elite",
    "deal_confidence": "elite",
    "backtest": "elite",
    "smart_trending": "elite",
    "portfolio_optimizer": "elite",
    "advanced_analytics": "elite",
    "undervalued_board": "elite",
    "realtime_alerts": "elite",
    "market_maker": "elite",
    "bulk_trades": "elite",
    "anomaly_alerts": "elite",
    "ai_recommendations": "elite",
    "sbc_impact_predictions": "elite",
    "investment_score_history": "elite",
    "daily_ai_briefing": "elite",
    "ai_chat": "elite",
    "advanced_backtesting": "elite",
    "early_data_refresh": "elite",
    "advanced_api_v2": "elite",
    # Full Track Record ledger and every window. The recent summary is
    # public (it is a trust signal); the complete history is not.
    "full_track_record": "elite",
}

# Kept importable for older code paths that referenced the matrix by name.
FEATURE_MATRIX: Dict[Feature, Dict[str, Any]] = {
    f: {"min_tier": t} for f, t in FEATURE_MIN_TIER.items()
}

# ---- Admins ------------------------------------------------------------------
# Server-side admin was previously nonexistent - "admin" only lived in the
# frontend's VITE_ADMIN_IDS cosmetic check. Admins are now: any id listed in
# ADMIN_DISCORD_IDS (comma-separated discord ids / user ids), or a users row
# with account_type='admin'. Admins get elite entitlements unconditionally.
ADMIN_DISCORD_IDS = {
    s.strip() for s in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if s.strip()
}

# Short cache of computed entitlements so hot endpoints don't re-query the DB
# on every request. Keyed by user_id.
_ENT_CACHE: dict[str, dict] = {}
ENT_CACHE_TTL = int(os.getenv("ENTITLEMENTS_CACHE_TTL", "60"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_roles(raw: Any) -> Set[str]:
    """users.roles is JSONB, which asyncpg returns as a JSON *string* unless
    a codec is registered - so set('[\"Premium\"]') would iterate characters
    and 'Premium' in roles would never match (found live via
    /api/entitlements/debug showing roles: ['[', ']'])."""
    if not raw:
        return set()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(r) for r in raw}
    return set()


async def _load_billing_state(pool: asyncpg.Pool, user_id: str) -> Dict[str, Any]:
    """Read every stored signal that could make this user paid."""
    out: Dict[str, Any] = {
        "plan": None,
        "tier_column": None,
        "account_type": None,
        "premium_until": None,
        "roles": set(),
        "profile_premium": False,
        "profile_premium_until": None,
        "active_subscription": False,
    }
    try:
        row = await pool.fetchrow(
            "SELECT plan, tier, account_type, premium_until, roles FROM users WHERE id=$1",
            user_id,
        )
        if row:
            out["plan"] = row["plan"]
            out["tier_column"] = row["tier"]
            out["account_type"] = row["account_type"]
            out["premium_until"] = _as_aware(row["premium_until"])
            out["roles"] = _parse_roles(row["roles"])
    except Exception:
        # users table may predate tier/account_type columns
        try:
            row = await pool.fetchrow(
                "SELECT plan, premium_until, roles FROM users WHERE id=$1", user_id
            )
            if row:
                out["plan"] = row["plan"]
                out["premium_until"] = _as_aware(row["premium_until"])
                out["roles"] = _parse_roles(row["roles"])
        except Exception:
            pass

    try:
        prow = await pool.fetchrow(
            "SELECT is_premium, premium_until FROM user_profiles WHERE user_id=$1",
            user_id,
        )
        if prow:
            out["profile_premium"] = bool(prow["is_premium"])
            out["profile_premium_until"] = _as_aware(prow["premium_until"])
    except Exception:
        pass

    try:
        sub = await pool.fetchrow(
            """
            SELECT 1 FROM subscriptions
            WHERE user_id = $1
              AND (
                    -- Active, and inside the period that was paid for.
                    -- `cancel_at_period_end` deliberately does NOT appear
                    -- here: a customer who cancelled keeps everything they
                    -- paid for until the period actually ends, which is
                    -- exactly what "current_period_end > NOW()" says.
                    (status IN ('active', 'trialing', 'trial')
                     AND (current_period_end IS NULL OR current_period_end > NOW()))
                 OR
                    -- Documented dunning grace. Stripe retries a failed
                    -- renewal for days while the subscription sits in
                    -- past_due, usually with current_period_end already in
                    -- the past. Cutting a paying customer off the instant a
                    -- card expires - before Stripe has even finished
                    -- retrying - is a worse failure than a few days of
                    -- access we might not collect for.
                    (status = 'past_due'
                     AND (current_period_end IS NULL
                          OR current_period_end > NOW() - ($2::int * INTERVAL '1 day')))
              )
            LIMIT 1
            """,
            user_id,
            PAST_DUE_GRACE_DAYS,
        )
        out["active_subscription"] = sub is not None
    except Exception:
        pass

    return out


def _is_admin(state: Dict[str, Any], user_id: str, discord_id: Optional[str]) -> bool:
    if (state.get("account_type") or "").lower() == "admin":
        return True
    ids = {str(user_id)}
    if discord_id:
        ids.add(str(discord_id))
    return bool(ids & ADMIN_DISCORD_IDS)


def _resolve_tier(state: Dict[str, Any]) -> Tier:
    """Free or Elite. There is no third answer.

    This used to return "pro" for anyone whose only signal was an active
    Stripe subscription, while `opportunity_feed` required "elite" - so a
    paying customer was refused the feed they had just bought. Every paid
    signal now resolves to elite:

      * users.plan / users.tier in {pro, premium, elite} - the manual-grant
        channel, so `UPDATE users SET tier='elite'` just works.
      * an entitled subscriptions row (see _load_billing_state for which
        statuses count, and why past_due is among them).
      * users.premium_until / user_profiles.premium_until in the future -
        the Stripe-webhook-independent grant, which is what carries a user
        across a webhook delay.

    Deliberately NOT a signal: whether the account has a linked Discord id.
    Discord is a benefit of paying, never a condition of it.
    """
    plan = (state.get("plan") or "").lower()
    tier_col = (state.get("tier_column") or "").lower()
    now = _now()

    paid = (
        plan in _PAID_PLAN_VALUES
        or tier_col in _PAID_PLAN_VALUES
        or bool(state.get("active_subscription"))
        or bool(state.get("premium_until") and state["premium_until"] > now)
        or bool(state.get("profile_premium_until") and state["profile_premium_until"] > now)
        # user_profiles.is_premium is a manual grant flag with no expiry of
        # its own; honour it only when it hasn't been superseded by an
        # explicitly expired premium_until.
        or bool(state.get("profile_premium") and not _profile_grant_expired(state, now))
    )
    return "elite" if paid else "free"


def _profile_grant_expired(state: Dict[str, Any], now: datetime) -> bool:
    until = state.get("profile_premium_until")
    return bool(until and until <= now)


def _limits_for(tier: Tier) -> Dict[str, Any]:
    """Everything the frontend needs to render a limit, and everything the
    backend enforces, from one function."""
    if tier == "elite":
        return {
            "watchlist_max": ELITE_WATCHLIST_MAX,
            "alerts_max": ELITE_ALERT_MAX,
            "open_trades_max": ELITE_OPEN_TRADE_MAX,
            "portfolio_history_days": None,   # unbounded
            "daily_opportunities": None,      # the whole feed
            "track_record_window_days": None, # every window
            "trending": ELITE_TRENDING,
            "bulk_trades_max": 100,
        }
    return {
        "watchlist_max": FREE_WATCHLIST_MAX,
        "alerts_max": FREE_ALERT_MAX,
        "open_trades_max": FREE_OPEN_TRADE_MAX,
        "portfolio_history_days": FREE_PORTFOLIO_HISTORY_DAYS,
        "daily_opportunities": FREE_DAILY_PICKS,
        "track_record_window_days": FREE_TRACK_RECORD_WINDOW_DAYS,
        "trending": FREE_TRENDING,
        "bulk_trades_max": 10,
    }


# Free features that nonetheless require an ACCOUNT.
#
# Tier and identity are two different questions, and FEATURE_MIN_TIER only
# answered the first. A signed-out visitor resolves to tier "free", so
# before this set existed they received every free feature - including the
# complete fair-value analysis, which is one of the things a Free account
# is supposed to be FOR. The free tier cannot be worth creating if its
# headline benefit is already available without creating it.
#
# What stays public is discovery: searching, seeing that a card exists,
# its identity and rating, and roughly what it costs. What needs an
# account is the analysis - the exact fair value, the discount, the
# verdict and anything resembling a trade plan.
#
# The write-scoped features are here for honesty rather than protection:
# a watchlist or a portfolio trade has always needed a user id to attach
# to, so an anonymous caller was never going to succeed. Saying so in the
# feature set means the UI can offer an account instead of rendering a
# control that will fail.
ACCOUNT_ONLY_FEATURES: Set[Feature] = {
    "fair_value",
    "watchlist",
    "trade_journal",
    "portfolio_performance",
}


def features_for_tier(tier: Tier, *, authenticated: bool = True) -> Set[Feature]:
    """The features this tier grants.

    `authenticated` defaults to True because every caller that has a tier
    in hand has a real user. Only the signed-out paths pass False, and
    they are the reason this parameter exists.
    """
    rank = _TIER_ORDER[tier]
    features = {f for f, t in FEATURE_MIN_TIER.items() if _TIER_ORDER[t] <= rank}
    if not authenticated:
        features -= ACCOUNT_ONLY_FEATURES
    return features


async def resolve_user_tier(conn_or_pool, user_id: str, *, discord_id: Optional[str] = None) -> Tier:
    """The same tier decision compute_entitlements() makes, for callers
    that have a database handle and a user id but no Request.

    Exists because the Discord integration was making a SECOND, subtly
    different paid/free decision of its own (discord_integration.
    entitlement_is_active): it ignored ADMIN_DISCORD_IDS, ignored
    user_profiles.is_premium, and returned "not paid" outright for any
    user with no linked Discord id. That is how the Account page could
    show "Current plan: ELITE" directly above "Membership access: Free" -
    two answers to one question, from two code paths, on one screen.

    Accepts either a pool or a connection - it only ever calls
    .fetchrow(), which both provide.
    """
    state = await _load_billing_state(conn_or_pool, str(user_id))
    if _is_admin(state, str(user_id), str(discord_id) if discord_id else None):
        return "elite"
    return _resolve_tier(state)


async def user_is_entitled(conn_or_pool, user_id: str, *, discord_id: Optional[str] = None) -> bool:
    """Paid-or-better, by the same rules the app's own paywall uses."""
    return await resolve_user_tier(conn_or_pool, user_id, discord_id=discord_id) in ("pro", "elite")


def _session_of(req: Request) -> Dict[str, Any]:
    """Starlette raises AssertionError from `request.session` when
    SessionMiddleware is not installed. Treat that as "nobody is signed
    in" rather than letting it 500: the safe interpretation of "we cannot
    identify this caller" is anonymous, and a crash here would turn a
    misconfigured mount into an outage instead of a locked door."""
    try:
        return req.session or {}
    except (AssertionError, AttributeError):
        return {}


async def compute_entitlements(req: Request) -> Dict[str, Any]:
    sess = _session_of(req)
    user_id = sess.get("user_id") or (sess.get("user") or {}).get("id")

    if not user_id:
        tier: Tier = "free"
        return {
            "user_id": None,
            "plan": None,
            "tier": tier,
            "premium_until": None,
            "roles": [],
            "is_premium": False,
            "is_admin": False,
            "features": sorted(features_for_tier(tier, authenticated=False)),
            "limits": _limits_for(tier),
        }

    cached = _ENT_CACHE.get(str(user_id))
    if cached and (time.time() - cached["at"] < ENT_CACHE_TTL):
        return dict(cached["ent"])

    pool: asyncpg.Pool = req.app.state.pool
    state = await _load_billing_state(pool, str(user_id))
    roles: Set[str] = set(sess.get("roles") or []) | set(state.get("roles") or set())
    discord_id = sess.get("discord_id") or user_id

    is_admin = _is_admin(state, str(user_id), str(discord_id) if discord_id else None)
    tier = "elite" if is_admin else _resolve_tier(state)
    is_premium = tier in ("pro", "elite")

    premium_until = state.get("premium_until") or (
        state.get("profile_premium_until") if state.get("active_subscription") else None
    )

    ent = {
        "user_id": str(user_id),
        "plan": state.get("plan"),
        "tier": tier,
        "premium_until": premium_until.isoformat() if premium_until else None,
        "roles": sorted(roles),
        "is_premium": is_premium,
        "is_admin": is_admin,
        "features": sorted(features_for_tier(tier)),
        "limits": _limits_for(tier),
    }
    _ENT_CACHE[str(user_id)] = {"at": time.time(), "ent": dict(ent)}
    return ent


def invalidate_entitlements_cache(user_id: str) -> None:
    """Call after any billing change (Stripe webhook, admin action)."""
    _ENT_CACHE.pop(str(user_id), None)


async def explain_entitlements(req: Request) -> Dict[str, Any]:
    """Self-serve diagnosis: every raw signal the tier decision reads, plus
    Answers 'why am I not premium?' without anyone having to read server logs. Exposed (for the
    logged-in user only) at /api/entitlements/debug."""
    sess = req.session or {}
    user_id = sess.get("user_id") or (sess.get("user") or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool: asyncpg.Pool = req.app.state.pool
    state = await _load_billing_state(pool, str(user_id))
    # Bypass the cache so this always reflects the live DB.
    invalidate_entitlements_cache(str(user_id))
    ent = await compute_entitlements(req)

    return {
        "resolved": {"tier": ent["tier"], "is_premium": ent["is_premium"], "is_admin": ent["is_admin"]},
        "signals": {
            "users.plan": state.get("plan"),
            "users.tier": state.get("tier_column"),
            "users.account_type": state.get("account_type"),
            "users.premium_until": state["premium_until"].isoformat() if state.get("premium_until") else None,
            "users.roles": sorted(state.get("roles") or []),
            "user_profiles.is_premium": state.get("profile_premium"),
            "user_profiles.premium_until": state["profile_premium_until"].isoformat() if state.get("profile_premium_until") else None,
            "subscriptions.entitled": state.get("active_subscription"),
            "admin.matched": ent["is_admin"],
        },
        "config": {
            "admin_ids_configured": bool(ADMIN_DISCORD_IDS),
        },
        "hint": (
            "Premium comes from ANY of: users.plan in (pro/premium/elite), users.tier in "
            "(pro/elite), an active/trialing/past_due subscriptions row with remaining "
            "entitlement time, unexpired users.premium_until, or admin "
            "(ADMIN_DISCORD_IDS / account_type='admin')."
        ),
    }


@dataclass(frozen=True)
class Viewer:
    """Who is asking, in the only terms the product has: signed out, Free,
    or Elite."""

    user_id: Optional[str]
    tier: str
    is_admin: bool
    features: frozenset

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def is_elite(self) -> bool:
        return self.tier == "elite"

    @property
    def is_free(self) -> bool:
        return self.is_authenticated and not self.is_elite

    def has(self, feature: str) -> bool:
        return feature in self.features


def anonymous_viewer() -> "Viewer":
    return Viewer(user_id=None, tier="free", is_admin=False,
                  features=frozenset(features_for_tier("free", authenticated=False)))


async def get_viewer(req: Request) -> "Viewer":
    """The ONE resolution path.

    Never raises: an endpoint that serves all three states needs to know
    which one it is serving, and "signed out" is a supported response
    here, not an error. Anything that goes wrong resolves to anonymous -
    least privilege, never accidental elite.

    require_feature() below is built on top of this rather than doing its
    own compute_entitlements() call, so there is a single place to
    override in tests and a single place for a bug to live.
    """
    try:
        ent = await compute_entitlements(req)
    except Exception:
        return anonymous_viewer()
    return Viewer(
        user_id=ent.get("user_id"),
        tier=ent.get("tier") or "free",
        is_admin=bool(ent.get("is_admin")),
        features=frozenset(ent.get("features") or ()),
    )


def require_feature(feature: Feature):
    """FastAPI dependency: 401 if anonymous, 402 if tier doesn't cover it."""

    async def _dep(viewer: "Viewer" = Depends(get_viewer)) -> Dict[str, Any]:
        ent = {"user_id": viewer.user_id, "tier": viewer.tier,
               "is_admin": viewer.is_admin, "features": sorted(viewer.features)}
        if not ent["user_id"]:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if feature not in viewer.features:
            min_tier = FEATURE_MIN_TIER.get(feature, "elite")
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "upgrade_required",
                    "feature": feature,
                    "required_tier": min_tier,
                    "current_tier": ent["tier"],
                    "message": f"'{feature}' needs the {min_tier.title()} plan.",
                },
            )
        return ent

    return _dep


def require_tier(min_tier: Tier):
    """FastAPI dependency for tier-gated (rather than feature-gated) routes.

    Built on get_viewer for the same reason require_feature is: one
    resolution path, one place to override, one place for a bug."""

    async def _dep(viewer: "Viewer" = Depends(get_viewer)) -> Dict[str, Any]:
        ent = {"user_id": viewer.user_id, "tier": viewer.tier,
               "is_admin": viewer.is_admin, "features": sorted(viewer.features)}
        if not ent["user_id"]:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if _TIER_ORDER[ent["tier"]] < _TIER_ORDER[min_tier]:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "upgrade_required",
                    "required_tier": min_tier,
                    "current_tier": ent["tier"],
                    "message": f"This needs the {min_tier.title()} plan.",
                },
            )
        return ent

    return _dep
