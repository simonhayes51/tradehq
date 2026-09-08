"""Read-only market reinforcement research for broad SBC resource demand.

Broad SBC requirements are segment hypotheses, not card-level triggers. This
module checks those hypotheses against observed BIN and completed-sales changes.
It never writes market_status or changes priority weights.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

MIN_MATCHED_CARDS = 10
PRICE_CONFIRM_PCT = 3.0
PRICE_CONFLICT_PCT = -3.0
SALES_ACTIVITY_RATIO = 1.20
EVIDENCE_WINDOW_HOURS = 2


def _resource_filter(resource_type: str, resource_value: str) -> Optional[Tuple[str, list[Any]]]:
    kind = str(resource_type or "").upper()
    value = str(resource_value or "").strip()
    if kind == "EXACT_RATING" and value.isdigit():
        return "m.rating = $1", [int(value)]
    if kind == "MIN_RATING":
        match = re.match(r"^(\d+)", value)
        return ("m.rating >= $1", [int(match.group(1))]) if match else None
    if kind == "MIN_TEAM_RATING":
        return None
    # FUT.GG stores the human rarity family, e.g. "Team of the Week", not
    # the SBC parser's compact `totw` token. Match the actual catalogue
    # vocabulary rather than silently returning an empty cohort.
    patterns = {
        "ICON": "%icon%", "HERO": "%hero%",
        "TOTW": "%team of the week%", "TOTS": "%team of the season%",
        "GOLD_RARE": "%gold rare%", "SILVER_RARE": "%silver rare%", "BRONZE_RARE": "%bronze rare%",
    }
    pattern = patterns.get(kind)
    return ("lower(COALESCE(m.rarity, '')) LIKE $1", [pattern]) if pattern else None


async def _matching_ids(conn, resource_type: str, resource_value: str, *, limit: int = 250):
    resolved = _resource_filter(resource_type, resource_value)
    if resolved is None:
        return None
    clause, args = resolved
    rows = await conn.fetch(
        f"SELECT m.source_card_id FROM futgg_market_snapshot m "
        f"WHERE m.is_tradeable IS DISTINCT FROM FALSE AND m.current_bin IS NOT NULL AND {clause} "
        f"ORDER BY m.source_card_id LIMIT ${len(args)+1}",
        *args, int(limit),
    )
    return [int(r["source_card_id"]) for r in rows]


def _empty_evidence(card_ids, *, pending=False):
    return {"matched_cards": len(card_ids or []), "baseline_cards": 0, "current_cards": 0,
            "baseline_bin": None, "current_bin": None, "price_change_pct": None,
            "baseline_sales": None, "current_sales": None, "sales_activity_ratio": None,
            "pending": pending}


async def _segment_evidence(conn, card_ids: list[int], event_started_at, *, window_hours: int = EVIDENCE_WINDOW_HOURS) -> Dict[str, Any]:
    if not card_ids or event_started_at is None:
        return _empty_evidence(card_ids)
    now = datetime.now(timezone.utc)
    if event_started_at.tzinfo is None:
        event_started_at = event_started_at.replace(tzinfo=timezone.utc)
    window = timedelta(hours=int(window_hours))
    if now - event_started_at < window:
        return _empty_evidence(card_ids, pending=True)

    # Restrict raw-history reads to the two windows actually compared. An SBC
    # from 72h ago must not make an admin report scan all intervening history.
    price = await conn.fetchrow(
        """WITH per_card AS (
          SELECT source_card_id,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_bin)
              FILTER (WHERE captured_at >= $2-$3::interval AND captured_at < $2) baseline_bin,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY lowest_bin)
              FILTER (WHERE captured_at >= now()-$3::interval) current_bin
          FROM futgg_bin_history
          WHERE source_card_id=ANY($1::bigint[]) AND
            ((captured_at >= $2-$3::interval AND captured_at < $2) OR captured_at >= now()-$3::interval)
          GROUP BY source_card_id)
        SELECT count(*) FILTER (WHERE baseline_bin IS NOT NULL) baseline_cards,
          count(*) FILTER (WHERE current_bin IS NOT NULL) current_cards,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY baseline_bin) FILTER (WHERE baseline_bin IS NOT NULL) baseline_bin,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY current_bin) FILTER (WHERE current_bin IS NOT NULL) current_bin
        FROM per_card""", card_ids, event_started_at, window)
    sales = await conn.fetchrow(
        """SELECT
          count(*) FILTER (WHERE approximate_sold_at >= $2-$3::interval AND approximate_sold_at < $2) baseline_sales,
          count(*) FILTER (WHERE approximate_sold_at >= now()-$3::interval) current_sales
        FROM futgg_sales_history WHERE source_card_id=ANY($1::bigint[]) AND
          ((approximate_sold_at >= $2-$3::interval AND approximate_sold_at < $2) OR approximate_sold_at >= now()-$3::interval)""",
        card_ids, event_started_at, window)
    baseline = float(price["baseline_bin"]) if price and price["baseline_bin"] is not None else None
    current = float(price["current_bin"]) if price and price["current_bin"] is not None else None
    change = ((current-baseline)/baseline)*100 if baseline and current is not None else None
    bs = int(sales["baseline_sales"] or 0) if sales else 0
    cs = int(sales["current_sales"] or 0) if sales else 0
    return {"matched_cards": len(card_ids), "baseline_cards": int(price["baseline_cards"] or 0),
            "current_cards": int(price["current_cards"] or 0), "baseline_bin": round(baseline) if baseline else None,
            "current_bin": round(current) if current else None, "price_change_pct": round(change,2) if change is not None else None,
            "baseline_sales": bs, "current_sales": cs, "sales_activity_ratio": round(cs/bs,2) if bs else None,
            "pending": False}


def classify_shadow_reinforcement(evidence: Dict[str, Any]) -> Tuple[str, str]:
    if evidence.get("pending"):
        return "PENDING", "The SBC has not yet had a full post-event observation window."
    matched, base, current = (int(evidence.get(k) or 0) for k in ("matched_cards","baseline_cards","current_cards"))
    change, ratio = evidence.get("price_change_pct"), evidence.get("sales_activity_ratio")
    if min(matched, base, current) < MIN_MATCHED_CARDS:
        return "INSUFFICIENT", "Not enough matched cards have both pre-event and current BIN evidence."
    if change is None:
        return "INSUFFICIENT", "No defensible segment price change can be measured."
    if float(change) <= PRICE_CONFLICT_PCT:
        return "CONFLICTING", "The segment is cheaper than its pre-event baseline."
    if float(change) >= PRICE_CONFIRM_PCT:
        if ratio is not None and float(ratio) >= SALES_ACTIVITY_RATIO:
            return "MARKET_CONFIRMED_CANDIDATE", "Price and completed-sales activity both strengthened after the SBC demand appeared."
        return "PRICE_ONLY", "Price strengthened, but completed-sales activity did not independently confirm it."
    return "UNCONFIRMED", "Observed segment behaviour is not materially different from baseline."


async def reinforcement_shadow_report(pool, *, lookback_hours: int = 72, limit: int = 40) -> Dict[str, Any]:
    interval = timedelta(hours=max(1, int(lookback_hours)))
    async with pool.acquire() as conn:
        demands = await conn.fetch(
            """SELECT id, resource_type, resource_value, required_count, event_started_at, event_expires_at,
                      last_confirmed_at, decayed_weight, market_status, raw_text
               FROM sbc_resource_demand WHERE ambiguity_status='CONFIRMED'
                 AND last_confirmed_at >= now()-$1::interval
                 AND (event_expires_at IS NULL OR event_expires_at > now())
               ORDER BY last_confirmed_at DESC LIMIT $2""", interval, max(1,min(int(limit),100)))
        items=[]
        for d in demands:
            ids=await _matching_ids(conn,d["resource_type"],d["resource_value"])
            if ids is None:
                items.append({"demand_id":int(d["id"]),"resource_type":d["resource_type"],"resource_value":d["resource_value"],
                              "stored_market_status":d["market_status"],"shadow_status":"UNSUPPORTED",
                              "reason":"This resource shape cannot be mapped without guessing.","evidence":None,"raw_text":d["raw_text"]})
                continue
            evidence=await _segment_evidence(conn,ids,d["event_started_at"])
            status,reason=classify_shadow_reinforcement(evidence)
            items.append({"demand_id":int(d["id"]),"resource_type":d["resource_type"],"resource_value":d["resource_value"],
                          "required_count":int(d["required_count"] or 0),"event_started_at":d["event_started_at"].isoformat() if d["event_started_at"] else None,
                          "event_expires_at":d["event_expires_at"].isoformat() if d["event_expires_at"] else None,
                          "decayed_weight":float(d["decayed_weight"]) if d["decayed_weight"] is not None else None,
                          "stored_market_status":d["market_status"],"shadow_status":status,"reason":reason,"evidence":evidence,"raw_text":d["raw_text"]})
    counts: Dict[str,int]={}
    for item in items: counts[item["shadow_status"]]=counts.get(item["shadow_status"],0)+1
    return {"mode":"SHADOW_ONLY","lookback_hours":int(lookback_hours),"demands_checked":len(items),"status_counts":counts,
            "items":items,"writes_market_status":False,"changes_priority_weight":False}
