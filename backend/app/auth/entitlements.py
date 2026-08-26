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
from fastapi import HTTPException, Request

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
    # Free gets a small daily trial allowance rather than a hard block -
    # see FREE_AI_CHAT_DAILY_LIMIT - so this is listed "free" below, with
    # the tier distinction expressed as a limit, not a require_feature gate.
    "ai_chat",
    # Browser-extension overlay (companion.py) - full fair-value/buy-below/
    # sell-target data, so it needs the same protection as fair_value/
    # deal_confidence. No free tier at all per product spec (locked
    # explanation only), unlike the other Pro features above.
    "companion",
    # Invest section (investments.py) - Free gets exactly one labeled
    # preview investment (enforced in-route, not via require_feature, since
    # the preview itself must stay servable to Free callers); this feature
    # name gates the *full* feed/analysis.
    "invest_feed",
]

Tier = Literal["free", "pro", "elite"]

# Consumer-facing: "pro" and "elite" both mean Premium. Elite has no real
# Stripe SKU today (see module docstring history) - it only exists as an
# internal ceiling for admin/manual-grant overrides. Partner, Founding
# Member, staff, and any other complimentary grant map onto "pro"/"elite"
# via users.tier/premium_until (see _resolve_tier) - there is no separate
# consumer-facing plan for any of them.
PREMIUM_TIERS: Set[Tier] = {"pro", "elite"}


def is_premium_tier(tier: Tier) -> bool:
    return tier in PREMIUM_TIERS


# ---- Limits (overridable via env) -------------------------------------------
FREE_WATCHLIST_MAX = int(os.getenv("WATCHLIST_FREE_MAX", os.getenv("WATCHLIST_BASIC_MAX", "3")))
PRO_WATCHLIST_MAX = int(os.getenv("WATCHLIST_PRO_MAX", "25"))
ELITE_WATCHLIST_MAX = int(os.getenv("WATCHLIST_ELITE_MAX", "500"))

# Free-tier daily/standing allowances for the Free vs Premium redesign.
# Premium (pro/elite) gets None (unlimited) for every one of these - see
# _limits_for(). All overridable via env for tuning without a redeploy.
FREE_DAILY_OPPORTUNITY_LIMIT = int(os.getenv("FREE_DAILY_OPPORTUNITY_LIMIT", "2"))
FREE_DAILY_FINDER_LIMIT = int(os.getenv("FREE_DAILY_FINDER_LIMIT", "3"))
FREE_ACTIVE_ALERT_LIMIT = int(os.getenv("FREE_ACTIVE_ALERT_LIMIT", "1"))
FREE_OPEN_TRADE_LIMIT = int(os.getenv("FREE_OPEN_TRADE_LIMIT", "3"))
FREE_PORTFOLIO_HISTORY_DAYS = int(os.getenv("FREE_PORTFOLIO_HISTORY_DAYS", "7"))
FREE_AI_CHAT_DAILY_LIMIT = int(os.getenv("FREE_AI_CHAT_DAILY_LIMIT", "3"))
# Opportunity/finder/AI-chat daily allowances reset at this UTC hour.
# EAFC Live has no other "day boundary" convention in use anywhere else in
# the codebase, so this documents UTC as the standard rather than inventing
# a new one.
DAILY_RESET_TIMEZONE = "UTC"

# Legacy aliases still imported elsewhere
BASIC_WATCHLIST_MAX = FREE_WATCHLIST_MAX
PREMIUM_WATCHLIST_MAX = ELITE_WATCHLIST_MAX

FREE_TRENDING = {"timeframes": {"24h"}, "limit": 5, "smart": False}
PRO_TRENDING = {"timeframes": {"6h", "24h"}, "limit": 15, "smart": True}
ELITE_TRENDING = {"timeframes": {"4h", "6h", "24h"}, "limit": 50, "smart": True}

# Legacy aliases
BASIC_TRENDING = FREE_TRENDING
PREMIUM_TRENDING = ELITE_TRENDING

# Feature -> minimum tier. Everything not listed here is free.
_TIER_ORDER: Dict[str, int] = {"free": 0, "pro": 1, "elite": 2}

FEATURE_MIN_TIER: Dict[Feature, Tier] = {
    # Free — the hook
    "ai_copilot": "free",
    "market_sentiment": "free",
    "leaderboard": "free",
    "referrals": "free",
    "watchlist": "free",          # size still limited by tier below
    "trade_journal": "free",      # open-trade count still limited by tier below
    "ai_chat": "free",            # daily message count still limited by tier below
    "trade_finder": "free",       # daily search count still limited by tier below
    # Pro — the daily-driver tools
    "smart_buy": "pro",
    "deal_confidence": "pro",
    "backtest": "pro",
    "smart_trending": "pro",
    "portfolio_optimizer": "pro",
    "advanced_analytics": "pro",
    "fair_value": "pro",
    "undervalued_board": "pro",
    "realtime_alerts": "pro",
    # Elite — the edge
    "market_maker": "elite",
    "bulk_trades": "elite",
    "anomaly_alerts": "elite",
    # v2 - Pro (daily-driver AI tools)
    "ai_recommendations": "pro",
    "sbc_impact_predictions": "pro",
    "investment_score_history": "pro",
    "portfolio_performance": "pro",
    "daily_ai_briefing": "pro",
    # Premium (pro) gets every eligible opportunity immediately per the
    # Free/Premium product spec - this used to be "elite" (meaning no real
    # Stripe customer ever actually got it, since elite has no SKU - see
    # module docstring). Free itself is handled via FREE_DAILY_OPPORTUNITY_LIMIT
    # + in-route locked previews, not by excluding "opportunity_feed" from
    # free's feature set entirely.
    "opportunity_feed": "pro",
    "companion": "pro",
    "invest_feed": "pro",
    # v2 - Elite (the edge)
    "advanced_backtesting": "elite",
    "early_data_refresh": "elite",
    "advanced_api_v2": "elite",
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
              AND status IN ('active', 'trialing', 'trial', 'past_due')
              AND (current_period_end IS NULL OR current_period_end > NOW())
            LIMIT 1
            """,
            user_id,
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
    plan = (state.get("plan") or "").lower()
    # users.tier is honored as a manual-grant channel: an admin running
    # UPDATE users SET tier='elite' WHERE ... should just work, without
    # having to fake a Stripe subscription. ('basic' means nothing here -
    # it's the column's legacy default, not a grant.)
    tier_col = (state.get("tier_column") or "").lower()
    now = _now()

    if plan == "elite" or tier_col == "elite":
        return "elite"

    paid = (
        plan in {"pro", "premium"}
        or tier_col in {"pro", "premium"}
        or state.get("active_subscription")
        or (state.get("premium_until") and state["premium_until"] > now)
    )
    return "pro" if paid else "free"


# Premium (pro/elite) allowances that are simply "no cap" - represented as
# None (rather than a large finite number) so callers can tell "unlimited"
# apart from "a generous limit" without hardcoding a magic threshold.
_PREMIUM_DAILY_LIMITS = {
    "daily_opportunity_limit": None,
    "daily_finder_limit": None,
    "active_alert_limit": None,
    "open_trade_limit": None,
    "portfolio_history_days": None,
    "ai_chat_daily_limit": None,
}


def _limits_for(tier: Tier) -> Dict[str, Any]:
    if tier == "elite":
        return {
            "watchlist_max": ELITE_WATCHLIST_MAX,
            "trending": ELITE_TRENDING,
            "bulk_trades_max": 100,
            **_PREMIUM_DAILY_LIMITS,
        }
    if tier == "pro":
        return {
            "watchlist_max": PRO_WATCHLIST_MAX,
            "trending": PRO_TRENDING,
            "bulk_trades_max": 25,
            **_PREMIUM_DAILY_LIMITS,
        }
    return {
        "watchlist_max": FREE_WATCHLIST_MAX,
        "trending": FREE_TRENDING,
        "bulk_trades_max": 10,
        "daily_opportunity_limit": FREE_DAILY_OPPORTUNITY_LIMIT,
        "daily_finder_limit": FREE_DAILY_FINDER_LIMIT,
        "active_alert_limit": FREE_ACTIVE_ALERT_LIMIT,
        "open_trade_limit": FREE_OPEN_TRADE_LIMIT,
        "portfolio_history_days": FREE_PORTFOLIO_HISTORY_DAYS,
        "ai_chat_daily_limit": FREE_AI_CHAT_DAILY_LIMIT,
    }


def _capabilities_for(tier: Tier, limits: Dict[str, Any]) -> Dict[str, Any]:
    """A consumer-facing summary of `limits`/`features`, in the shape the
    frontend actually wants (named capabilities rather than `plan ===`
    checks scattered across components). Derived entirely from the same
    tier/limits this module already computes - not a second, competing
    source of truth."""
    premium = is_premium_tier(tier)
    return {
        "is_premium": premium,
        "all_live_opportunities": premium,
        "daily_opportunity_limit": limits["daily_opportunity_limit"],
        "daily_finder_limit": limits["daily_finder_limit"],
        "watchlist_limit": limits["watchlist_max"],
        "active_alert_limit": limits["active_alert_limit"],
        "open_trade_limit": limits["open_trade_limit"],
        "portfolio_history_days": limits["portfolio_history_days"],
        "ai_chat_daily_limit": limits["ai_chat_daily_limit"],
        "full_price_history": premium,
        "full_history": premium,
        "companion_access": premium,
        "invest_full_feed": premium,
    }


def features_for_tier(tier: Tier) -> Set[Feature]:
    rank = _TIER_ORDER[tier]
    return {f for f, t in FEATURE_MIN_TIER.items() if _TIER_ORDER[t] <= rank}


async def compute_entitlements(req: Request) -> Dict[str, Any]:
    # req.session raises AssertionError if SessionMiddleware isn't
    # installed on this app (rather than just returning {}) - true for
    # every real deployment (main.py installs it), but not for a bare
    # FastAPI() test app exercising a router in isolation. Treating that
    # case as anonymous/free (not raising) means routes that call this can
    # be exercised in tests that never wire up SessionMiddleware, and a
    # genuinely session-less internal caller degrades safely too.
    sess = (req.session or {}) if "session" in req.scope else {}
    user_id = sess.get("user_id") or (sess.get("user") or {}).get("id")

    if not user_id:
        tier: Tier = "free"
        limits = _limits_for(tier)
        return {
            "user_id": None,
            "plan": None,
            "tier": tier,
            "premium_until": None,
            "roles": [],
            "is_premium": False,
            "is_admin": False,
            "features": sorted(features_for_tier(tier)),
            "limits": limits,
            "capabilities": _capabilities_for(tier, limits),
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
    is_premium = is_premium_tier(tier)

    premium_until = state.get("premium_until") or (
        state.get("profile_premium_until") if state.get("active_subscription") else None
    )

    limits = _limits_for(tier)
    ent = {
        "user_id": str(user_id),
        "plan": state.get("plan"),
        "tier": tier,
        "premium_until": premium_until.isoformat() if premium_until else None,
        "roles": sorted(roles),
        "is_premium": is_premium,
        "is_admin": is_admin,
        "features": sorted(features_for_tier(tier)),
        "limits": limits,
        "capabilities": _capabilities_for(tier, limits),
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
    # req.session raises AssertionError if SessionMiddleware isn't
    # installed on this app (rather than just returning {}) - true for
    # every real deployment (main.py installs it), but not for a bare
    # FastAPI() test app exercising a router in isolation. Treating that
    # case as anonymous/free (not raising) means routes that call this can
    # be exercised in tests that never wire up SessionMiddleware, and a
    # genuinely session-less internal caller degrades safely too.
    sess = (req.session or {}) if "session" in req.scope else {}
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


def _current_reset_day():
    return _now().date()


async def increment_daily_usage(pool: asyncpg.Pool, user_id: str, usage_key: str, *, limit: Optional[int]) -> Dict[str, Any]:
    """Atomically records one use of `usage_key` for `user_id` today (UTC -
    see DAILY_RESET_TIMEZONE) and reports whether it was within `limit`.

    `limit=None` means unlimited (Premium) - skips the table write entirely
    since there's nothing to enforce or count against.

    Concurrency-safe: the INSERT .. ON CONFLICT DO UPDATE .. RETURNING count
    is a single atomic statement, so two simultaneous requests from the same
    user (two tabs, a retry) can't both observe "count was 2, now allowed"
    for what should have been the 3rd and 4th use - Postgres serializes the
    two writers on that row and each sees the other's increment.
    """
    if limit is None:
        return {"allowed": True, "used": None, "limit": None, "remaining": None}

    day = _current_reset_day()
    row = await pool.fetchrow(
        """
        INSERT INTO daily_usage_counters (user_id, usage_key, day, count)
        VALUES ($1, $2, $3, 1)
        ON CONFLICT (user_id, usage_key, day)
        DO UPDATE SET count = daily_usage_counters.count + 1, updated_at = now()
        RETURNING count
        """,
        str(user_id), usage_key, day,
    )
    used = row["count"]
    return {
        "allowed": used <= limit,
        "used": min(used, limit),
        "limit": limit,
        "remaining": max(0, limit - used),
    }


async def get_daily_usage(pool: asyncpg.Pool, user_id: str, usage_key: str, *, limit: Optional[int]) -> Dict[str, Any]:
    """Read-only version of increment_daily_usage - for status/usage
    endpoints (e.g. the Account page's counters) that must not themselves
    consume an allowance just by being viewed."""
    if limit is None:
        return {"used": None, "limit": None, "remaining": None}

    day = _current_reset_day()
    row = await pool.fetchrow(
        "SELECT count FROM daily_usage_counters WHERE user_id=$1 AND usage_key=$2 AND day=$3",
        str(user_id), usage_key, day,
    )
    used = row["count"] if row else 0
    return {"used": min(used, limit), "limit": limit, "remaining": max(0, limit - used)}


def require_feature(feature: Feature):
    """FastAPI dependency: 401 if anonymous, 402 if tier doesn't cover it."""

    async def _dep(req: Request) -> Dict[str, Any]:
        ent = await compute_entitlements(req)
        if not ent["user_id"]:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if feature not in ent["features"]:
            min_tier = FEATURE_MIN_TIER.get(feature, "pro")
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
    """FastAPI dependency for tier-gated (rather than feature-gated) routes."""

    async def _dep(req: Request) -> Dict[str, Any]:
        ent = await compute_entitlements(req)
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
