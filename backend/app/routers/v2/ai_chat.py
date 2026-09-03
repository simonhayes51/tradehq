# app/routers/v2/ai_chat.py
#
# OpenAI-backed chat, orchestrating real tool calls into the LIVE FUT.GG
# market layer (app/routers/v2/futgg_market.py) and the published track
# record - never a free-form model answer.
#
# It used to call app/routers/trade_finder.py and
# recommendations.get_player_recommendation() instead. Both read the
# legacy FUTBIN materialised views (fair_value_mv / recommendations_
# latest), which are written only by services gated behind
# ENABLE_LEGACY_FUTBIN and are therefore not refreshed in this
# deployment: "should I buy Mbappe 92" answered from a frozen view, or
# 404'd outright, while every other screen in the app showed live FUT.GG
# prices. An assistant confidently quoting stale prices is worse than no
# assistant, so every tool below now reads exactly the same data the
# Opportunities/Trade Finder pages do. The model's only jobs are intent-parsing (which tool to call,
# with what arguments) and turning a tool's JSON result into plain
# language; every price/ROI/recommendation in a reply must trace back to
# a tool call. This is the same "never fabricate a number" discipline
# trading_math.py exists to enforce everywhere else in this codebase -
# one hallucinated price here would undo that.
#
# Gated Pro+ (see app/auth/entitlements.py): unlike the free ai_copilot
# keyword-matcher, every message here costs a real OpenAI call.
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.access import get_viewer
from app.auth.entitlements import compute_entitlements, require_feature
from app.services import ai_usage
from app.routers.v2.futgg_market import (
    get_player_detail as futgg_player_detail,
    get_provider as futgg_provider,
    list_players as futgg_list_players,
    trade_finder as futgg_trade_finder,
)
from app.routers.v2.recommendations import track_record

log = logging.getLogger("ai_chat")

router = APIRouter(prefix="/ai", tags=["v2-ai-chat"])

OPENAI_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.6-luna")
# One customer message is at most this many model calls: the model asks
# for a tool, we answer, it asks again, and the last round produces the
# reply. Every round resends the whole conversation plus the tool schema,
# so this number multiplies the cost of a message directly.
_MAX_TOOL_ROUNDS = 3

# Rate limiting, cost accounting and the kill switch live in
# app/services/ai_usage.py - one module, so the limit and the number that
# justifies it cannot drift apart. See docs/AI_CHAT_COST.md for the
# measurement the daily default is set from.


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    # An upper bound at the parse boundary, before any of this reaches a
    # model. The client sends the whole conversation on every request, so
    # history length - the dominant term in the cost of a message - used
    # to be chosen by the caller: min_length=1 and no maximum. A 1MB body
    # of history costs roughly £0.08 per message, or £27/hour at the
    # per-minute limit.
    #
    # This cap is deliberately looser than ai_usage.MAX_HISTORY_MESSAGES,
    # which does the real trimming. Rejecting outright at 10 would break
    # any client that keeps a longer local transcript; the server simply
    # declines to PAY for more than the last few turns.
    messages: List[ChatMessage] = Field(..., min_length=1, max_length=200)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_and_evaluate_card",
            "description": (
                "Look up one specific card on the live market and return its current price, how "
                "old that price is, the engine's verdict (buy/hold/avoid), recommended buy and "
                "sell prices, expected after-tax profit, risk and recent sales. Use this for any "
                "question about a specific player or card."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Player name only, e.g. 'Mbappe'"},
                    "rating": {"type": "integer", "description": "Card rating if the user gave one, e.g. 92"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_trades_for_budget",
            "description": (
                "Find profitable trade candidates within a coin budget. Use this for questions like "
                "'who should I buy for 40k' or 'best trades under 100000'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_max": {"type": "number", "description": "Maximum coins to spend, e.g. 40000 for '40k'"},
                    "budget_min": {"type": "number", "description": "Minimum coins to spend, if the user gave a range"},
                },
                "required": ["budget_max"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_track_record",
            "description": (
                "Get the graded results of recommendations that were actually published to users. "
                "Use this when asked how accurate or reliable the recommendations are."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM_PROMPT = (
    "You are the EAFC.Live trading assistant for EA FC Ultimate Team. You have no market knowledge "
    "of your own - every price, ROI, confidence score, or verdict you state MUST come from a tool "
    "call result. Never state a coin value, percentage, or buy/sell verdict that did not come from "
    "a tool.\n"
    "Always say WHY, using the tool's own numbers: name the current price, the recommended buy "
    "price, the expected after-tax profit and the risk level rather than just giving a verdict. EA "
    "takes 5% on every sale and the tool's profit figures already account for that.\n"
    "Prices are snapshots, not a live feed. If a card's price_age_minutes is above about 60, say "
    "how old the price is before quoting it, and do not describe it as the current price.\n"
    "If a tool returns a disambiguation list, ask which card they meant instead of guessing. If a "
    "tool returns an error or no data, say so plainly - never fill the gap from general knowledge, "
    "and never predict a promo, an SBC, or a future price. If no tool applies to the question, say "
    "that this assistant only answers from EAFC.Live market data.\n"
    "Keep replies short and concrete. Do not use internal field names, reason codes or scoring "
    "jargon in your reply - write plain English a trader would use."
)


async def _resolve_futgg_card(query: str, rating: Optional[int]) -> Dict[str, Any]:
    """Name (+ optional rating) -> one live FUT.GG card, or a
    disambiguation list. Uses the same /api/v2/players search the Players
    page uses, so the assistant can only ever talk about cards the app
    itself can show."""
    result = await futgg_list_players(
        search=query, rating=rating, page=1, page_size=6,
        provider=await futgg_provider(),
    )
    items = result.get("items") or []
    if not items:
        return {"error": f"No card found matching '{query}'"}
    if len(items) > 1:
        # An exact rating match collapses the ambiguity without guessing.
        exact = [i for i in items if rating is not None and i.get("rating") == rating]
        if len(exact) == 1:
            items = exact
    if len(items) > 1:
        return {
            "ambiguous": True,
            "candidates": [
                {
                    "card_id": i.get("source_card_id") or i.get("card_id"),
                    "name": i.get("display_name") or i.get("name"),
                    "rating": i.get("rating"),
                    "version": i.get("rarity"),
                }
                for i in items
            ],
        }
    return {"card": items[0]}


def _chat_safe_card(detail: Dict[str, Any]) -> Dict[str, Any]:
    """What the model is allowed to see about a card.

    Deliberately narrow. The full detail payload carries dozens of
    internal fields (reason codes, gate names, scoring internals); handing
    those to a language model invites it to paraphrase internal
    diagnostics at a customer as if they were advice. Only the fields the
    product already shows on a card page are passed through, plus the
    freshness of the price - so the model can say how old a number is
    instead of implying everything is live.
    """
    intelligence = detail.get("intelligence") or {}
    return {
        "card_id": detail.get("source_card_id") or detail.get("card_id"),
        "name": detail.get("display_name") or detail.get("name"),
        "rating": detail.get("rating"),
        "version": detail.get("rarity"),
        "verdict": intelligence.get("signal") or detail.get("signal"),
        "current_price": detail.get("current_bin"),
        "price_age_minutes": detail.get("price_age_minutes"),
        "recommended_buy_max": detail.get("recommended_buy_max"),
        "recommended_sell_target": detail.get("recommended_sell_target"),
        "expected_profit_after_tax": detail.get("expected_profit_after_tax"),
        "expected_roi": detail.get("expected_roi"),
        "confidence": detail.get("confidence_score"),
        "risk_level": detail.get("risk_level"),
        "recent_sale_count": detail.get("recent_sale_count"),
        "trend": detail.get("trend_description"),
        "reasons": (detail.get("signal_reasons") or [])[:4],
    }


# Defence in depth. The /chat endpoint is already Elite-gated, so in
# principle no Free caller reaches these tools at all. This check exists
# anyway because the tool layer is the thing that actually reads premium
# data, and a future change - a free "demo" mode, a shared handler, a new
# entry point - would otherwise silently turn the model into an
# unauthenticated proxy for the paid feed. A model that can be talked into
# calling a tool is only as safe as the tool.
_TOOL_FEATURE = {
    "resolve_and_evaluate_card": "opportunity_feed",
    "find_trades_for_budget": "trade_finder",
    "get_track_record": "full_track_record",
}


async def _run_tool(request: Request, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    viewer = await get_viewer(request)
    required = _TOOL_FEATURE.get(name)
    if required and not viewer.has(required):
        # Returned as a tool RESULT, not raised: the model needs to be
        # able to tell the user it cannot answer, and an exception here
        # would abort the whole turn with no explanation.
        return {
            "error": "not_available_on_this_plan",
            "explanation": (
                "This lookup is part of the Elite plan. Say so plainly and do not "
                "guess or estimate the answer from anything else."
            ),
        }

    if name == "resolve_and_evaluate_card":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "No query provided"}
        rating = args.get("rating")
        try:
            rating = int(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None
        resolved = await _resolve_futgg_card(query, rating)
        if "card" not in resolved:
            return resolved
        card = resolved["card"]
        card_id = card.get("source_card_id") or card.get("card_id")
        try:
            detail = await futgg_player_detail(int(card_id), provider=await futgg_provider())
        except HTTPException as e:
            return {"error": e.detail}
        return {"card": _chat_safe_card(detail)}

    if name == "find_trades_for_budget":
        budget_max = args.get("budget_max")
        if budget_max is None:
            return {"error": "budget_max is required"}
        try:
            budget_max = int(float(budget_max))
            budget_min = int(float(args["budget_min"])) if args.get("budget_min") is not None else None
        except (TypeError, ValueError):
            return {"error": "budget must be a number of coins"}
        # Exhaustive for the same reason as _resolve_futgg_card above.
        # meaningful_profit + require_executable are the floors the "Quick
        # flips" strategy tab applies: "who should I buy for 40k" should
        # be answered with trades that are actionable now, not cards that
        # merely score well and can't be entered at a sensible price.
        result = await futgg_trade_finder(
            budget=budget_max, min_profit=None, min_roi=None,
            risk_tolerance=None, risk=None, min_confidence=None,
            position=None, rarity=None, rating=None, rating_min=None,
            rating_max=None, min_liquidity=None, max_price_age_minutes=None,
            max_price_age=None, sort_by="best_opportunity", sort=None,
            require_executable=True, meaningful_profit=True,
            page=1, page_size=10, provider=await futgg_provider(),
        )
        items = result.get("items") or []
        if budget_min is not None:
            items = [i for i in items if (i.get("current_bin") or 0) >= budget_min]
        return {
            "items": [_chat_safe_card(item) for item in items[:5]],
            "total": result.get("total"),
        }

    if name == "get_track_record":
        # Exhaustive call, same rule as the futgg_market handlers: an
        # omitted argument on a route function keeps its Depends() object
        # rather than becoming a value.
        record = await track_record(request, window="90d", viewer=viewer)
        published = record.get("published") or {}
        # Only the PUBLISHED half is exposed. model_validation is internal
        # engine diagnostics on a much larger, never-shown dataset - it is
        # labelled as such in the UI, and handing it to a model that will
        # paraphrase it loses that label immediately.
        if not published.get("has_published_data") or not published.get("has_enough_data"):
            return {
                "has_enough_data": False,
                "explanation": (
                    "Not enough published recommendations have been graded yet to quote a "
                    "hit rate honestly."
                ),
            }
        return {
            "has_enough_data": True,
            "window_days": record.get("window_days"),
            "published_recommendations": published.get("published_opportunities"),
            "wins": published.get("wins"),
            "losses": published.get("losses"),
            "win_rate_pct": published.get("win_rate_pct"),
            "median_roi_pct": published.get("median_roi_pct"),
            "caveat": (
                "Hypothetical results from real market data for recommendations actually sent "
                "to users. Not a guarantee any individual achieved these prices."
            ),
        }

    return {"error": f"Unknown tool: {name}"}


def _chat_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


@router.get("/status")
async def chat_status() -> Dict[str, Any]:
    """Whether this deployment can actually answer a chat message.

    Exists so the app can hide the AI Chat nav entry entirely when the
    service is not configured, rather than routing users to a page whose
    only possible reply is an apology mentioning an environment variable.
    Deliberately ungated and free of any detail beyond the boolean: a
    signed-out visitor needs the answer to render navigation, and nobody
    outside the deployment needs to know which key is missing.
    """
    return {"configured": _chat_configured()}


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> Dict[str, Any]:
    await require_feature("ai_chat")(request)
    ent = await compute_entitlements(request)
    user_id = ent.get("user_id")
    if not user_id:
        raise HTTPException(401, "Not authenticated")

    decision = await ai_usage.acquire(str(user_id))
    if not decision.allowed:
        # Recorded, so refusals are visible in the usage summary rather
        # than being an invisible dead end. A blocked row costs nothing:
        # no model call happened.
        await ai_usage.record(
            str(user_id), ai_usage.UsageTally(model=OPENAI_MODEL), "blocked",
        )
        detail = {"error": decision.reason, "message": decision.message}
        if decision.resets_at_iso:
            detail["resetsAt"] = decision.resets_at_iso
        raise HTTPException(503 if decision.reason == "disabled" else 429, detail)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # 503, not a 200 with an apology in the `reply` field: a message
        # that never reached a model is not an assistant answer, and
        # rendering it as one taught users the assistant was broken rather
        # than switched off. The nav entry is hidden via GET /ai/status,
        # so this is the deep-link/stale-tab path. No env var names in
        # customer-facing text.
        await ai_usage.release(str(user_id))
        raise HTTPException(503, "AI Chat is not available on this deployment yet.")

    from openai import AsyncOpenAI  # imported lazily so a missing/unset key never breaks module import

    client = AsyncOpenAI(api_key=api_key)

    # The server decides how much history it is willing to pay for. The
    # client may keep a longer transcript locally; only the last few turns
    # are sent to the model, and each message is capped on its own so one
    # enormous message cannot do what a long history no longer can.
    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(ai_usage.trim_history(body.messages))

    tool_results: List[Dict[str, Any]] = []
    tally = ai_usage.UsageTally(model=OPENAI_MODEL)
    status = "failed"

    try:
        for _ in range(_MAX_TOOL_ROUNDS):  # bounded - never let a model loop forever
            response = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                # Bounds the output side. "Keep replies short and concrete"
                # is already in the system prompt; this is the same
                # instruction made enforceable rather than requested.
                max_tokens=ai_usage.MAX_OUTPUT_TOKENS,
            )
            # Every call in the loop counts. One CUSTOMER message is up to
            # _MAX_TOOL_ROUNDS model calls, each resending the whole
            # conversation - accumulating here rather than reading the
            # last response is the difference between the real cost and a
            # third of it.
            tally.add(response)
            choice = response.choices[0]
            message = choice.message

            if not message.tool_calls:
                status = "ok"
                return {"reply": message.content or "", "toolResults": tool_results}

            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            })

            for tool_call in message.tool_calls:
                try:
                    args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await _run_tool(request, tool_call.function.name, args)
                tool_results.append({"tool": tool_call.function.name, "args": args, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, default=str),
                })

        # Loop exhausted without a final text reply - surface the last
        # tool results rather than silently returning nothing.
        status = "ok"
        return {"reply": "Here's what I found:", "toolResults": tool_results}
    except Exception:
        # The exception CLASS NAME used to be interpolated into the reply
        # ("...unavailable (APIConnectionError)"), which is an internal
        # diagnostic shown to a customer. It is logged instead.
        log.exception("ai_chat: model call failed")
        raise HTTPException(503, "The assistant is temporarily unavailable. Please try again shortly.")
    finally:
        # A failed request still spent whatever tokens it spent before
        # failing, so it is recorded with the tokens it actually used and
        # a status that says it did not produce an answer. The concurrency
        # slot is always given back; the rate windows are not, because a
        # request that reached the provider cost money either way.
        await ai_usage.record(str(user_id), tally, status)
        await ai_usage.release(str(user_id))
