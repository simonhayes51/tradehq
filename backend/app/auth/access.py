# app/auth/access.py
#
# The serialisation half of access control.
#
# app/auth/entitlements.py answers "what is this caller allowed to do".
# This module answers "so what do we actually put in the response body",
# and it is deliberately a separate, NARROWER serialisation rather than
# the full payload with fields blanked out.
#
# That distinction is the whole point. A response that carries
# recommended_buy_max and expected_profit_after_tax and relies on the
# client not to render them has already given the data away - it is one
# devtools Network tab from being the paid product. So the preview shape
# is built by listing what may be included, never by deleting what may
# not: a new premium field added to the engine tomorrow is absent from
# the preview by default, instead of leaking until someone remembers to
# add it to a blocklist.
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from fastapi import HTTPException

from app.auth.entitlements import Viewer, anonymous_viewer, get_viewer


# Viewer and get_viewer live in entitlements.py, not here. There is one
# resolution path for "who is asking" and require_feature()/require_tier()
# are built on it too - so overriding get_viewer overrides every gate,
# and a bug in access resolution has exactly one home. They are
# re-exported here because call sites naturally reach for this module.
__all__ = [
    "Viewer",
    "limit_reached",
    "require_account",
    "get_viewer",
    "anonymous_viewer",
    "preview_opportunity",
    "public_card_preview",
    "public_companion_preview",
    "serialise_player_search",
    "preview_dashboard_recommendation",
    "serialise_dashboard_feed",
    "serialise_opportunities",
    "assert_no_premium_fields",
]


# ---------------------------------------------------------------------------
# Opportunity serialisation
# ---------------------------------------------------------------------------

# The complete allow-list for a locked opportunity. Enough to prove the
# card is real, current and worth paying to see in full; not enough to
# execute the trade. Notably absent, and absent on purpose:
# recommended_buy_max, buy_below, recommended_sell_target,
# expected_profit_after_tax, expected_roi, confidence_score,
# signal_reasons, reason codes, liquidity_score, fair_value.
_PREVIEW_FIELDS = (
    "source_card_id",
    "name",
    "display_name",
    "rating",
    "rarity",
    "primary_position",
    "position",
    "player_image_url",
    "card_design_image_url",
    "card_image_url",
    "card_image_stored_url",
    "composited_image_url",
    "signal",
    "risk_level",
)


def preview_opportunity(item: Dict[str, Any]) -> Dict[str, Any]:
    """A locked opportunity: identity, verdict, and one plain-language
    reason. No executable numbers."""
    out = {key: item.get(key) for key in _PREVIEW_FIELDS}
    # Rounded hard and labelled as approximate. A trader can tell whether
    # this is a 5k card or a 500k card - which is what makes the preview
    # worth looking at - without being handed a price to buy against.
    current_bin = item.get("current_bin")
    out["current_bin_approx"] = (
        round(float(current_bin) / 1000) * 1000 if current_bin is not None else None
    )
    # One short reason, never the full list: the reason array can spell
    # out the exact thresholds and margins the engine used.
    reasons = item.get("signal_reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    elif not isinstance(reasons, (list, tuple)):
        reasons = []
    out["headline_reason"] = str(reasons[0]) if reasons else None
    out["locked"] = True
    return out


def serialise_opportunities(
    items: Sequence[Dict[str, Any]],
    viewer: Viewer,
    *,
    unlocked_ids: Iterable[int] = (),
) -> Dict[str, Any]:
    """Turn a scored candidate list into the response this viewer may have.

    Elite  - everything, untouched.
    Free   - the day's free picks in full, everything else previewed.
    Signed out - everything previewed.

    `locked_count` is returned so the UI can say "8 more today" honestly
    instead of the feed just looking short.
    """
    if viewer.has("opportunity_feed"):
        return {"items": list(items), "locked_count": 0, "viewer_tier": viewer.tier}

    unlocked = {int(i) for i in unlocked_ids}
    out: List[Dict[str, Any]] = []
    locked_count = 0
    for item in items:
        card_id = item.get("source_card_id")
        if card_id is not None and int(card_id) in unlocked:
            out.append(item)
        else:
            out.append(preview_opportunity(item))
            locked_count += 1
    return {"items": out, "locked_count": locked_count, "viewer_tier": viewer.tier}


# ---------------------------------------------------------------------------
# Dashboard (Home) serialisation
# ---------------------------------------------------------------------------
#
# Home returns the same recommendations in a different shape: camelCase,
# with the card nested under "player" (see dashboard.py's
# _futgg_to_recommendation). It needs its own allow-list rather than
# reusing _PREVIEW_FIELDS, because a key-name mismatch between the two
# shapes would silently produce an empty preview - or, if written
# subtractively, silently leak the next field somebody adds.
#
# `player` is passed through whole: it is card art and identity, and
# nothing in it is a trade instruction. Everything else is named
# explicitly.
_DASHBOARD_PREVIEW_FIELDS = ("cardId", "player", "status", "recommendation", "updatedAt")

# What must never survive into a locked Home card. Kept as an explicit
# list too, so assert_no_premium_fields can check this shape and the flat
# one with the same helper.
_DASHBOARD_PREMIUM_FIELDS = (
    "confidence", "expectedRoi", "netRoi", "expectedNetRoi", "entryPrice",
    "recommendedBuyMax", "recommendedSellTarget", "buyBelow", "expectedProfitBasis",
    "breakEvenPrice", "holdingPeriod", "qualifiedStrategies", "failedGateReasons",
    "fairValue", "reasoning", "marketDrivers", "historicalSimilarEvents", "scores",
)


def preview_dashboard_recommendation(item: Dict[str, Any]) -> Dict[str, Any]:
    """A locked Home card: the player, the verdict, an approximate price
    and one plain-language reason. No executable numbers."""
    out = {key: item.get(key) for key in _DASHBOARD_PREVIEW_FIELDS}
    current_bin = item.get("currentBin")
    out["currentBinApprox"] = (
        round(float(current_bin) / 1000) * 1000 if current_bin is not None else None
    )
    risk = item.get("risk")
    out["risk"] = {"overall": risk.get("overall")} if isinstance(risk, dict) else risk
    reasoning = item.get("reasoning")
    if isinstance(reasoning, str):
        reasoning = [part.strip() for part in reasoning.split(";") if part.strip()]
    elif not isinstance(reasoning, (list, tuple)):
        reasoning = []
    out["headlineReason"] = str(reasoning[0]) if reasoning else None
    out["locked"] = True
    return out


def serialise_dashboard_feed(
    items: Sequence[Dict[str, Any]],
    viewer: Viewer,
    *,
    unlocked_ids: Iterable[int] = (),
) -> "tuple[List[Dict[str, Any]], int]":
    """Home's feed for this viewer, plus how many were locked.

    The unlocked set is the SAME persisted daily-pick set /opportunities
    uses (app/services/free_picks.py). Home used to hand every Free member
    the current top-ranked BUY in full, re-derived on each request - a
    third free pick that nobody counted, that changed as the market moved,
    and that a signed-out visitor got as well.
    """
    if viewer.has("opportunity_feed"):
        return list(items), 0

    unlocked = {int(i) for i in unlocked_ids}
    out: List[Dict[str, Any]] = []
    locked_count = 0
    for item in items:
        card_id = item.get("cardId")
        if card_id is not None and int(card_id) in unlocked:
            out.append(item)
        else:
            out.append(preview_dashboard_recommendation(item))
            locked_count += 1
    return out, locked_count


def assert_no_premium_fields(payload: Any) -> None:
    """Test/debug helper: raise if an executable number is present anywhere
    in a payload that is supposed to be locked.

    Exists because "did we leak a field" is exactly the kind of regression
    that passes review and fails in production - the response still looks
    right on screen.
    """
    # Both shapes: the flat snake_case one /opportunities and /players use,
    # and Home's nested camelCase one. Checked together so a payload does
    # not have to declare which shape it is.
    banned = {
        "recommended_buy_max", "buy_below", "recommended_sell_target",
        "expected_profit_after_tax", "expected_roi", "expected_profit_basis",
        "confidence_score", "signal_reasons", "liquidity_score", "fair_value",
        "current_bin", "current_executable_buy",
        *_DASHBOARD_PREMIUM_FIELDS,
    }
    # "risk" survives on a locked Home card as {"overall": "low"} - a label,
    # not a number - and "player" is card art. Neither is a trade
    # instruction, so neither is banned above.

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in banned:
                    raise AssertionError(f"premium field {key!r} leaked at {path or '<root>'}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload)


# ---------------------------------------------------------------------------
# Card detail (the player page, and the companion overlay)
# ---------------------------------------------------------------------------

# The executable trade plan. These are what a Free member's two daily
# picks actually unlock, so they must be absent everywhere else - a Free
# member who can look up any card and read its entry and target has a
# limit of "two", enforced by nothing.
_TRADE_PLAN_FIELDS = (
    "recommended_buy_max",
    "buy_below",
    "recommended_sell_target",
    "expected_profit_after_tax",
    "expected_profit",
    "expected_profit_basis",
    "expected_roi",
    "current_executable_buy",
    "signal_reasons",
    "reasons",
    "reason_codes",
    "blocking_codes",
    "failed_gate_reasons",
    "why_this_fits",
)


def redact_trade_plan(item: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the executable plan from an otherwise-full card payload.

    Unlike preview_opportunity() this is a subtractive helper, and that is
    deliberate: the card page's value to a Free member is everything ELSE
    - the live price, the fair value, the trend, the sales history - which
    is genuinely useful and is what "full player search and fair-value
    information" promises. Only the plan is withheld.

    Because it is subtractive, _TRADE_PLAN_FIELDS has to be maintained as
    the engine grows. assert_no_premium_fields() in the test suite is what
    keeps that honest.
    """
    out = {key: value for key, value in item.items() if key not in _TRADE_PLAN_FIELDS}
    nested = out.get("intelligence")
    if isinstance(nested, dict):
        out["intelligence"] = {k: v for k, v in nested.items() if k not in _TRADE_PLAN_FIELDS}
    out["trade_plan_locked"] = True
    return out


# ---------------------------------------------------------------------------
# Public (signed-out) card serialisation
# ---------------------------------------------------------------------------
#
# What someone with no account may see about a card.
#
# Search itself stays public: being able to look a card up is discovery,
# it is shareable, and it is what makes a link to this product worth
# sending. What is NOT public is the analysis - the exact live price, the
# fair value, the discount, the verdict and anything resembling a trade
# plan - because "complete live price and fair-value analysis" is the
# headline reason to create a Free account, and a free tier cannot be
# worth creating if its main benefit is already given away.
#
# So a signed-out visitor gets enough to know the card is real, current
# and roughly what it costs, and a clear reason to sign up for the rest.
_PUBLIC_CARD_FIELDS = (
    "source_card_id",
    "card_id",
    "name",
    "display_name",
    "full_name",
    "rating",
    "rarity",
    "primary_position",
    "position",
    "alternate_positions",
    "club",
    "league",
    "nation",
    "player_image_url",
    "image_url",
    "card_design_image_url",
    "card_image_url",
    "card_image_stored_url",
    "composited_image_url",
    "club_image_url",
    "league_image_url",
    "nation_image_url",
    "is_tradeable",
    "game_year",
)

# The rounding applied to a public price. Coarse enough that it cannot be
# used as a live price feed, fine enough to tell a 5k card from a 500k one
# - which is the whole point of showing it.
_PUBLIC_PRICE_BUCKETS = ((10_000, 500), (100_000, 5_000), (1_000_000, 25_000))
_PUBLIC_PRICE_COARSEST = 100_000


def _approximate_price(value: Any) -> Optional[int]:
    """A deliberately imprecise price.

    Proportional rather than a flat rounding: rounding a 3,200-coin card
    to the nearest 1,000 loses a third of its value, while rounding a
    900,000-coin card to the nearest 1,000 is not an approximation at all.
    """
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    step = _PUBLIC_PRICE_COARSEST
    for ceiling, bucket in _PUBLIC_PRICE_BUCKETS:
        if amount < ceiling:
            step = bucket
            break
    return int(round(amount / step) * step)


def public_card_preview(item: Dict[str, Any]) -> Dict[str, Any]:
    """One card, as a signed-out visitor may see it.

    Allow-listed, like every other preview in this module: a field added
    to the card payload tomorrow is absent from a public response without
    anyone remembering to exclude it.
    """
    out = {key: item.get(key) for key in _PUBLIC_CARD_FIELDS if key in item}
    price = item.get("current_bin")
    out["current_bin_approx"] = _approximate_price(price)
    # Said explicitly so the UI never has to infer it, and so a client
    # cannot mistake the approximate figure for a live one.
    out["price_is_approximate"] = True
    out["requires_account"] = True
    # Freshness is safe and useful: it tells someone whether the product
    # is actually live without telling them what the number is.
    out["price_age_seconds"] = item.get("price_age_seconds")
    return out


# The companion overlay speaks its own field vocabulary (reference_bin
# rather than current_bin, a nested risk object, an engine version), so it
# gets its own allow-list for the same reason Home does: a key-name
# mismatch between shapes silently produces an empty preview, and written
# subtractively it silently leaks whatever field is added next.
_PUBLIC_COMPANION_FIELDS = (
    "source_card_id",
    "name",
    "rating",
    "rarity",
    "image_url",
    "engine_version",
    "evaluated_at",
)


def public_companion_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The overlay, as a signed-out visitor sees it.

    Still worth having on screen: it confirms the card, and confirms that
    something is watching this market. It is no longer a free valuation.
    """
    out = {key: payload.get(key) for key in _PUBLIC_COMPANION_FIELDS if key in payload}
    out["reference_bin_approx"] = _approximate_price(payload.get("reference_bin"))
    out["price_is_approximate"] = True
    out["requires_account"] = True
    out["locked"] = True
    return out


def serialise_player_search(
    items: Sequence[Dict[str, Any]],
    viewer: Viewer,
) -> List[Dict[str, Any]]:
    """Search results for this viewer.

    Signed out gets identity and an approximate price. A Free member gets
    the real market data (that is what the account is for) minus the
    executable plan, which stays with the daily picks. Elite gets
    everything.
    """
    if viewer.has("opportunity_feed"):
        return list(items)
    if not viewer.is_authenticated:
        return [public_card_preview(item) for item in items]
    return [redact_trade_plan(item) for item in items]


def serialise_card_detail(
    item: Dict[str, Any],
    viewer: Viewer,
    *,
    unlocked_ids: Iterable[int] = (),
) -> Dict[str, Any]:
    """A single card, serialised for this viewer.

    Elite gets the whole thing.

    A Free member gets the card, the market data and the verdict - which
    is what the account is for - but the executable plan only if this
    exact card is one of today's free picks.

    A signed-out visitor gets the public preview: identity and an
    approximate price, no analysis. Before this, the card page handed
    anonymous callers the complete live price, fair value and trend, which
    is the substance of what a Free account is supposed to unlock.
    """
    if viewer.has("opportunity_feed"):
        return item
    if not viewer.is_authenticated:
        return public_card_preview(item)
    card_id = item.get("source_card_id") or item.get("card_id")
    if card_id is not None and int(card_id) in {int(i) for i in unlocked_ids}:
        return item
    return redact_trade_plan(item)


# ---------------------------------------------------------------------------
# Allowance limits
# ---------------------------------------------------------------------------


def require_account(viewer: Viewer, what: str) -> None:
    """Refuse a signed-out caller, without refusing a Free member.

    401 rather than 402: this is not a payment problem. What is missing is
    an account, and one is free - so the frontend routes this to signup,
    not to checkout. `feature` names fair_value because that is the
    entitlement these endpoints all sit behind.
    """
    if viewer.is_authenticated:
        return
    raise HTTPException(
        status_code=401,
        detail={
            "error": "account_required",
            "feature": "fair_value",
            "message": f"Create a free account to see {what}. No card needed.",
        },
    )


def limit_reached(
    *,
    feature: str,
    used: int,
    maximum: int,
    viewer: Viewer,
    noun: str,
    elite_maximum: Optional[int] = None,
) -> HTTPException:
    """One shape for every "you've hit your allowance" refusal.

    402 rather than 403: the frontend already routes 402 to the upgrade
    flow, and this is a payment problem, not a permissions one.

    The message is written from the caller's actual tier. The watchlist
    endpoint used to say "Free plan allows up to N" unconditionally, which
    an Elite member hitting the 500-card ceiling would have read as being
    told they were on the free plan.
    """
    if viewer.is_elite:
        message = f"You've reached the maximum of {maximum:,} {noun} on Elite."
        upgradeable = False
    else:
        message = f"Free members can keep {maximum:,} {noun}."
        if elite_maximum:
            message += f" Elite raises this to {elite_maximum:,}."
        upgradeable = True
    return HTTPException(
        status_code=402,
        detail={
            "error": "limit_reached",
            "feature": feature,
            "used": used,
            "limit": maximum,
            "current_tier": viewer.tier,
            "upgradeable": upgradeable,
            "message": message,
        },
    )
