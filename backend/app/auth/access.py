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

from typing import Any, Dict, Iterable, List, Sequence

from app.auth.entitlements import Viewer, anonymous_viewer, get_viewer


# Viewer and get_viewer live in entitlements.py, not here. There is one
# resolution path for "who is asking" and require_feature()/require_tier()
# are built on it too - so overriding get_viewer overrides every gate,
# and a bug in access resolution has exactly one home. They are
# re-exported here because call sites naturally reach for this module.
__all__ = [
    "Viewer",
    "get_viewer",
    "anonymous_viewer",
    "preview_opportunity",
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


def assert_no_premium_fields(payload: Any) -> None:
    """Test/debug helper: raise if an executable number is present anywhere
    in a payload that is supposed to be locked.

    Exists because "did we leak a field" is exactly the kind of regression
    that passes review and fails in production - the response still looks
    right on screen.
    """
    banned = {
        "recommended_buy_max", "buy_below", "recommended_sell_target",
        "expected_profit_after_tax", "expected_roi", "expected_profit_basis",
        "confidence_score", "signal_reasons", "liquidity_score", "fair_value",
        "current_bin", "current_executable_buy",
    }

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
