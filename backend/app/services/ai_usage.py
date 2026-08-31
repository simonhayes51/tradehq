# app/services/ai_usage.py
#
# What the assistant costs, and the limits that keep it affordable.
#
# WHY THIS EXISTS
#
# The assistant shipped with a 6/minute throttle and, from Phase 2, a
# 200/day ceiling that was never costed. It was chosen as "generous enough
# not to be a daily allowance", which is a UX judgement, not a commercial
# one. Sizing it against the actual prompt showed 200/day is between 124%
# and 307% of the £3 subscription for a single heavy member - the product
# loses money on anyone who uses the feature as permitted.
#
# Worse, the input side was attacker-controlled. ChatRequest.messages had
# min_length=1 and no maximum, and the client sends the whole conversation
# on every request, so history length - the dominant term in the cost -
# was chosen by the caller. A 1MB body of history costs ~£0.08 per
# message, or roughly £27/hour at the per-minute limit.
#
# WHAT THIS MODULE DOES
#
#   * bounds the input (history cap) and the output (max_tokens)
#   * counts usage from the PROVIDER'S reported token numbers, never an
#     estimate from character counts - an estimate that drifts from the
#     invoice is worse than no number, because it looks authoritative
#   * enforces per-minute, per-day and per-month limits, fleet-wide when
#     Redis is configured and per-worker when it is not
#   * refuses concurrent requests from one account
#   * has an off switch
#
# PRICING IS CONFIGURATION, NOT A CONSTANT. No pricing existed anywhere in
# this repository, so the defaults below are the published list rates for
# the default model at the time of writing and are NOT verified against
# this account's invoices. They are environment-overridable precisely
# because they will be wrong eventually; see docs/AI_CHAT_COST.md.
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger("ai_usage")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log.warning("%s is not an integer; using %s", name, default)
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log.warning("%s is not a number; using %s", name, default)
        return default


def chat_enabled() -> bool:
    """The kill switch.

    Read on every request rather than captured at import, so turning the
    assistant off does not require a deploy. Off means a clean, honest
    refusal - never a model call.
    """
    return os.getenv("AI_CHAT_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


# --- Limits ---------------------------------------------------------------
#
# The daily default is 30, not 200. At the measured worst case (the
# heaviest tool, history at the cap) 30/day is about 21% of the £3
# subscription; 200/day was 124-307%. 30 is still far above what anyone
# using this as a trading assistant sends in a day - the median session
# in the product's own UI is a handful of questions - so this is a
# backstop against automation and runaway loops, not a rationing scheme.
RATE_LIMIT_PER_MINUTE = _int_env("AI_CHAT_RATE_LIMIT_PER_MINUTE", 6)
RATE_LIMIT_PER_DAY = _int_env("AI_CHAT_RATE_LIMIT_PER_DAY", 30)
# A month at the daily cap would be 900. The monthly ceiling exists for
# the member who hits the daily limit every single day: 300 keeps the
# worst realistic month inside a fifth of one subscription.
RATE_LIMIT_PER_MONTH = _int_env("AI_CHAT_RATE_LIMIT_PER_MONTH", 300)
# One in-flight message per account. A chat UI sends one at a time; more
# than that is a script, and concurrency is how a per-minute counter gets
# turned into six simultaneous expensive calls.
MAX_CONCURRENT = _int_env("AI_CHAT_MAX_CONCURRENT", 1)

# The dominant cost term, and the only one the caller used to control.
# 10 messages is five exchanges, which is more than enough for the
# assistant to follow a conversation about one card or one budget.
MAX_HISTORY_MESSAGES = _int_env("AI_CHAT_MAX_HISTORY_MESSAGES", 10)
# Each message is also capped on its own, so one enormous message cannot
# do what a long history no longer can.
MAX_MESSAGE_CHARS = _int_env("AI_CHAT_MAX_MESSAGE_CHARS", 2000)
# "Keep replies short and concrete" is in the system prompt; this is the
# same instruction made enforceable.
MAX_OUTPUT_TOKENS = _int_env("AI_CHAT_MAX_OUTPUT_TOKENS", 600)

# --- Pricing --------------------------------------------------------------
PRICE_PER_1M_INPUT = _float_env("AI_CHAT_PRICE_PER_1M_INPUT", 0.15)
PRICE_PER_1M_OUTPUT = _float_env("AI_CHAT_PRICE_PER_1M_OUTPUT", 0.60)
PRICE_CURRENCY = os.getenv("AI_CHAT_PRICE_CURRENCY", "USD").upper()


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Cost of one customer message, in PRICE_CURRENCY.

    Internal only. It is never returned to a customer: what someone pays
    is their subscription, and showing them a per-message cost invites
    them to ration themselves against a number that is not their problem.
    """
    return round(
        (input_tokens / 1_000_000) * PRICE_PER_1M_INPUT
        + (output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT,
        6,
    )


# --- Input bounding -------------------------------------------------------


def trim_history(messages: list, *, max_messages: Optional[int] = None,
                 max_chars: Optional[int] = None) -> list:
    """Keep the most recent turns, and only the most recent turns.

    The newest messages are kept because that is the part of a
    conversation that carries the question. The latest message is always
    kept even if it alone exceeds the cap, so a request can never be
    trimmed to nothing.
    """
    limit = MAX_HISTORY_MESSAGES if max_messages is None else max_messages
    chars = MAX_MESSAGE_CHARS if max_chars is None else max_chars
    kept = list(messages)[-limit:] if limit > 0 else list(messages)[-1:]
    if not kept and messages:
        kept = [messages[-1]]
    out = []
    for message in kept:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
        out.append({"role": role, "content": (content or "")[:chars]})
    return out


# --- Limit windows --------------------------------------------------------


@dataclass(frozen=True)
class LimitDecision:
    """Whether this request may proceed, and if not, when it may."""
    allowed: bool
    reason: str = ""
    message: str = ""
    resets_at: Optional[datetime] = None

    @property
    def resets_at_iso(self) -> Optional[str]:
        return self.resets_at.isoformat() if self.resets_at else None


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _day_message(resets_at: datetime) -> str:
    return (
        "You've reached today's fair-use limit for the assistant. It resets at "
        f"{resets_at.strftime('%H:%M')} UTC - everything else on your plan is unaffected."
    )


def _month_message(resets_at: datetime) -> str:
    return (
        "You've reached this month's fair-use limit for the assistant. It resets on "
        f"{resets_at.strftime('%-d %B')} - everything else on your plan is unaffected."
    )


MINUTE_MESSAGE = "Too many chat messages - please wait a moment."
CONCURRENT_MESSAGE = (
    "You already have a message in progress. Wait for that answer before sending another."
)
DISABLED_MESSAGE = (
    "The assistant is switched off right now. Everything else on your plan is unaffected."
)


# --- The limiter ----------------------------------------------------------
#
# Redis is the real backend: the limits are per ACCOUNT, and an account's
# requests land on whichever worker the load balancer picks, so a
# per-process counter is not a fleet-wide ceiling. The in-process path
# below exists so a Redis outage DEGRADES the limit instead of removing
# it - it still bounds one worker, which is a far smaller hole than none.

_redis = None
_redis_checked = False

# Per-worker fallback state: {user_id: (window_start_epoch, count)}
_minute_window: Dict[str, tuple] = {}
_day_window: Dict[str, tuple] = {}
_month_window: Dict[str, tuple] = {}
_in_flight: Dict[str, int] = {}


async def _get_redis():
    """Lazy, optional, and checked once. Same pattern as app/auth/api_keys."""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore

        _redis = aioredis.from_url(url, decode_responses=True)
        await _redis.ping()
    except Exception as e:
        log.warning("REDIS_URL set but unusable for AI usage limits (%s)", e)
        _redis = None
    return _redis


def reset_state_for_tests() -> None:
    """Clear the per-worker fallback windows. Tests only."""
    _minute_window.clear()
    _day_window.clear()
    _month_window.clear()
    _in_flight.clear()


def _allow(now: datetime) -> LimitDecision:
    return LimitDecision(allowed=True)


async def acquire(user_id: str, *, now: Optional[datetime] = None) -> LimitDecision:
    """Reserve one message for this account.

    Reserving BEFORE the model call is deliberate: the alternative is
    counting afterwards, which lets a burst of concurrent requests all
    pass a check that nothing has yet incremented. A reservation that
    turns out not to have been spent is released again by `release()`.
    """
    now = now or datetime.now(timezone.utc)

    if not chat_enabled():
        return LimitDecision(allowed=False, reason="disabled", message=DISABLED_MESSAGE)

    day_reset = _next_utc_midnight(now)
    month_reset = _next_month_start(now)
    epoch = now.timestamp()

    redis = await _get_redis()
    if redis is not None:
        try:
            return await _acquire_redis(redis, user_id, now, epoch, day_reset, month_reset)
        except _LimitHit as hit:
            return hit.decision
        except Exception:
            log.warning("AI usage limits: Redis unavailable, degrading to per-worker", exc_info=True)

    return _acquire_in_process(user_id, epoch, day_reset, month_reset)


class _LimitHit(Exception):
    def __init__(self, decision: LimitDecision):
        self.decision = decision
        super().__init__(decision.reason)


async def _acquire_redis(redis, user_id, now, epoch, day_reset, month_reset) -> LimitDecision:
    # Concurrency first, and it is the only key that has to be released.
    # A crashed worker must not lock an account out forever, so the key
    # carries a short TTL as a dead-man's switch.
    if MAX_CONCURRENT > 0:
        in_flight = await redis.incr(f"ai:conc:{user_id}")
        if in_flight == 1:
            await redis.expire(f"ai:conc:{user_id}", 120)
        if in_flight > MAX_CONCURRENT:
            await redis.decr(f"ai:conc:{user_id}")
            raise _LimitHit(LimitDecision(
                allowed=False, reason="concurrent", message=CONCURRENT_MESSAGE,
            ))

    async def _bump(key: str, ttl: int, limit: int, reason: str,
                    message: str, resets_at) -> None:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, ttl)
        if count > limit:
            # Do NOT decrement: the window is a rate, and letting a
            # refused request lower the count would let a caller hold
            # themselves just under the limit indefinitely.
            await _release_concurrency(redis, user_id)
            raise _LimitHit(LimitDecision(
                allowed=False, reason=reason, message=message, resets_at=resets_at,
            ))

    await _bump(f"ai:min:{user_id}:{int(epoch // 60)}", 90,
                RATE_LIMIT_PER_MINUTE, "minute", MINUTE_MESSAGE, None)
    await _bump(f"ai:day:{user_id}:{now.strftime('%Y%m%d')}", 90_000,
                RATE_LIMIT_PER_DAY, "day", _day_message(day_reset), day_reset)
    await _bump(f"ai:mon:{user_id}:{now.strftime('%Y%m')}", 32 * 86_400,
                RATE_LIMIT_PER_MONTH, "month", _month_message(month_reset), month_reset)
    return _allow(now)


def _acquire_in_process(user_id, epoch, day_reset, month_reset) -> LimitDecision:
    if MAX_CONCURRENT > 0:
        if _in_flight.get(user_id, 0) >= MAX_CONCURRENT:
            return LimitDecision(allowed=False, reason="concurrent", message=CONCURRENT_MESSAGE)
        _in_flight[user_id] = _in_flight.get(user_id, 0) + 1

    def _bump(store, span, limit, reason, message, resets_at):
        start, count = store.get(user_id, (epoch, 0))
        if epoch - start >= span:
            store[user_id] = (epoch, 1)
            return None
        if count >= limit:
            return LimitDecision(allowed=False, reason=reason, message=message, resets_at=resets_at)
        store[user_id] = (start, count + 1)
        return None

    for args in (
        (_minute_window, 60, RATE_LIMIT_PER_MINUTE, "minute", MINUTE_MESSAGE, None),
        (_day_window, 86_400, RATE_LIMIT_PER_DAY, "day", _day_message(day_reset), day_reset),
        (_month_window, 31 * 86_400, RATE_LIMIT_PER_MONTH, "month",
         _month_message(month_reset), month_reset),
    ):
        refused = _bump(*args)
        if refused is not None:
            _release_in_process(user_id)
            return refused

    return LimitDecision(allowed=True)


async def _release_concurrency(redis, user_id: str) -> None:
    try:
        remaining = await redis.decr(f"ai:conc:{user_id}")
        if remaining < 0:
            await redis.set(f"ai:conc:{user_id}", 0, ex=120)
    except Exception:
        log.debug("AI usage limits: could not release concurrency slot", exc_info=True)


def _release_in_process(user_id: str) -> None:
    if user_id in _in_flight:
        _in_flight[user_id] = max(0, _in_flight[user_id] - 1)
        if _in_flight[user_id] == 0:
            _in_flight.pop(user_id, None)


async def release(user_id: str) -> None:
    """Give back the concurrency slot. Always call this, in a finally.

    The rate windows are NOT rolled back: a request that reached the
    provider spent money whether or not it produced an answer, and a
    refused request must not be able to lower its own count.
    """
    redis = await _get_redis()
    if redis is not None:
        try:
            await _release_concurrency(redis, user_id)
            return
        except Exception:
            log.debug("AI usage limits: Redis release failed", exc_info=True)
    _release_in_process(user_id)


# --- Accounting -----------------------------------------------------------


@dataclass
class UsageTally:
    """Provider-reported usage for ONE customer message.

    One customer message is several model calls: the tool loop calls again
    with each tool result in context. Accumulating across the loop is the
    difference between the real cost and a third of it.
    """
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0

    def add(self, response: Any) -> None:
        """Fold in one provider response.

        Reads the provider's own usage object. Nothing here estimates from
        character counts: if the provider stops reporting usage the counts
        stay at zero, which reads as "we do not know" rather than as a
        confident wrong number.
        """
        self.model_calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            log.warning("ai_usage: provider returned no usage object for model=%s", self.model)
            return
        self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost(self) -> float:
        return estimate_cost(self.input_tokens, self.output_tokens)


async def record(user_id: str, tally: UsageTally, status: str, *, pool=None) -> bool:
    """Write one usage row. Never raises.

    Accounting must not be able to fail a request the customer has already
    paid for in tokens - but a failure to record is logged loudly, because
    silent loss of cost data is how a limit stays a guess.
    """
    try:
        if pool is None:
            from app.db import get_core_pool

            pool = await get_core_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ai_usage_events
                    (user_id, model, status, input_tokens, output_tokens,
                     total_tokens, model_calls, estimated_cost, cost_currency)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                str(user_id), tally.model, status,
                tally.input_tokens, tally.output_tokens, tally.total_tokens,
                tally.model_calls, tally.cost, PRICE_CURRENCY,
            )
        return True
    except Exception:
        log.warning("ai_usage: could not record usage for status=%s", status, exc_info=True)
        return False


async def usage_summary(days: int = 30, *, pool=None) -> Dict[str, Any]:
    """What the assistant has cost, for the admin view.

    Aggregated in SQL. Raw usage rows are never handed to a browser: they
    are a per-account behavioural record, and the question being asked is
    about totals.
    """
    if pool is None:
        from app.db import get_core_pool

        pool = await get_core_pool()

    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT
                COUNT(*)                                        AS messages,
                COUNT(*) FILTER (WHERE status = 'ok')           AS ok,
                COUNT(*) FILTER (WHERE status = 'failed')       AS failed,
                COUNT(*) FILTER (WHERE status = 'blocked')      AS blocked,
                COUNT(DISTINCT user_id)                         AS users,
                COALESCE(SUM(input_tokens), 0)                  AS input_tokens,
                COALESCE(SUM(output_tokens), 0)                 AS output_tokens,
                COALESCE(SUM(model_calls), 0)                   AS model_calls,
                COALESCE(SUM(estimated_cost), 0)                AS cost
            FROM ai_usage_events
            WHERE occurred_at >= NOW() - ($1::int * INTERVAL '1 day')
            """,
            days,
        )
        # The number that actually decides whether the limit is right: not
        # the average member, but the most expensive one.
        heaviest = await conn.fetchrow(
            """
            SELECT COUNT(*) AS messages, COALESCE(SUM(estimated_cost), 0) AS cost
            FROM ai_usage_events
            WHERE occurred_at >= NOW() - ($1::int * INTERVAL '1 day')
              AND status <> 'blocked'
            GROUP BY user_id
            ORDER BY cost DESC
            LIMIT 1
            """,
            days,
        )

    row = dict(totals or {})
    messages = int(row.get("messages") or 0)
    ok = int(row.get("ok") or 0)
    users = int(row.get("users") or 0)
    cost = float(row.get("cost") or 0.0)
    return {
        "window_days": days,
        "messages": messages,
        "ok": ok,
        "failed": int(row.get("failed") or 0),
        "blocked": int(row.get("blocked") or 0),
        "unique_users": users,
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "model_calls": int(row.get("model_calls") or 0),
        "estimated_cost": round(cost, 4),
        "cost_currency": PRICE_CURRENCY,
        "cost_per_message": round(cost / ok, 6) if ok else None,
        "cost_per_active_user": round(cost / users, 4) if users else None,
        "heaviest_user_messages": int(heaviest["messages"]) if heaviest else 0,
        "heaviest_user_cost": round(float(heaviest["cost"]), 4) if heaviest else 0.0,
        "limits": {
            "per_minute": RATE_LIMIT_PER_MINUTE,
            "per_day": RATE_LIMIT_PER_DAY,
            "per_month": RATE_LIMIT_PER_MONTH,
            "max_concurrent": MAX_CONCURRENT,
            "max_history_messages": MAX_HISTORY_MESSAGES,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "enabled": chat_enabled(),
        },
        "pricing": {
            "per_1m_input": PRICE_PER_1M_INPUT,
            "per_1m_output": PRICE_PER_1M_OUTPUT,
            "currency": PRICE_CURRENCY,
            # Said out loud in the payload, not just in a doc: these rates
            # were never verified against a real invoice.
            "source": "configured list rates - verify against the provider invoice",
        },
    }
