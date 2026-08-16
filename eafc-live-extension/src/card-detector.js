// src/card-detector.js
//
// Pure logic: turn an EA FC 26 Web App /transfermarket response body into
// exact card identity + a normalized auction list. No DOM, no network,
// no extension APIs - this file is unit-testable directly under Node
// (see tests/card-detector.test.js) and is also loaded as a plain
// content-script file in the browser (see manifest.json), where it
// attaches itself to `window.EAFCLive`.
//
// WHAT WE OBSERVED (documented per the milestone's own requirement)
// -------------------------------------------------------------------
// EA's Web App has never published an official schema for this endpoint.
// Community reference implementations of the /ut/game/fc26/transfermarket
// search response (e.g. auction-house browser extensions and companion
// apps in the FC modding community) consistently describe each result as
// an object shaped roughly like:
//
//   {
//     "auctionInfo": [
//       {
//         "tradeId": 1234567890,
//         "itemData": {
//           "id": 1234567890,
//           "resourceId": 117672259,     // full card-version identity
//           "definitionId": 117672259,   // usually mirrors resourceId
//           "maskedDefId": 231747,       // the underlying PLAYER, not the card
//           "rating": 97,
//           "itemType": "player"
//         },
//         "buyNowPrice": 1090000,
//         "currentBid": 1050000,
//         "startingBid": 950000,
//         "expires": 3184,
//         "tradeState": "active"
//       }
//     ]
//   }
//
// This has not been confirmed against a live authenticated session in
// this environment (no browser/EA account available here) - see the
// implementation report for what still needs manual verification. The
// code below is written defensively specifically because of that: every
// field is read with fallbacks, nothing throws on an unexpected shape,
// and identity resolution refuses to guess rather than silently return a
// wrong card id.
//
// IDENTITY RULE
// -------------------------------------------------------------------
// resourceId (falling back to definitionId) is the exact card-version
// identity and maps directly to futgg_players.source_card_id - see
// backend/app/routers/v2/companion.py's module docstring and
// auto_sync/futgg_common.py's CARD_URL_RE for why FUT.GG's own card id
// already *is* this same number, not a value needing reconciliation.
//
// maskedDefId is NEVER used as the identity - it identifies the
// underlying player and collides across every special version of that
// player (an 85-rated gold and a 97-rated TOTY Mbappe can share it), so
// treating it as the card id would silently mismatch a special card's
// intelligence onto a totally different card's numbers.
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.EAFCLive = root.EAFCLive || {};
    Object.assign(root.EAFCLive, factory());
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function toPositiveInt(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
  }

  /**
   * The exact card-version id for one EA item object, or null if EA gave
   * us nothing usable. Never falls back to maskedDefId (see file header).
   */
  function extractExactCardId(itemData) {
    if (!itemData || typeof itemData !== "object") return null;
    return toPositiveInt(itemData.resourceId) || toPositiveInt(itemData.definitionId) || null;
  }

  /**
   * Normalizes one auctionInfo[] entry into the flat shape the rest of
   * the extension works with. Returns null (rather than throwing) for an
   * entry that doesn't look like a real auction - a defensively-written
   * caller can just filter those out.
   */
  function normalizeAuction(entry) {
    if (!entry || typeof entry !== "object") return null;
    const item = entry.itemData || entry.item || {};
    const sourceCardId = extractExactCardId(item);
    if (sourceCardId === null) return null;

    return {
      sourceCardId,
      tradeId: toPositiveInt(entry.tradeId ?? item.id) ?? null,
      maskedDefId: toPositiveInt(item.maskedDefId),
      rating: Number.isFinite(Number(item.rating)) ? Number(item.rating) : null,
      buyNowPrice: toPositiveInt(entry.buyNowPrice) ?? 0,
      currentBid: toPositiveInt(entry.currentBid) ?? 0,
      startingBid: toPositiveInt(entry.startingBid) ?? 0,
      // Seconds remaining, as EA reports it. 0/undefined is treated as
      // "unknown", never as "expired now" - an auction that has actually
      // ended is simply absent from a fresh search response.
      expires: toPositiveInt(entry.expires),
      tradeState: typeof entry.tradeState === "string" ? entry.tradeState : null,
    };
  }

  /**
   * Walks a parsed /transfermarket response body and returns every
   * auction we could confidently identify. Tolerant of:
   *   - a bare array instead of {auctionInfo: [...]}
   *   - a single-item response ({itemData: {...}, buyNowPrice: ...})
   *   - fields missing/renamed/of the wrong type
   * Never throws - an unrecognised shape just yields an empty list, and
   * the caller (market-observer.js) treats that exactly like "EA's
   * response format changed, show nothing" per the milestone's graceful-
   * failure requirement.
   */
  function extractAuctionsFromResponse(body) {
    if (!body || typeof body !== "object") return [];

    let rawList;
    if (Array.isArray(body.auctionInfo)) {
      rawList = body.auctionInfo;
    } else if (Array.isArray(body)) {
      rawList = body;
    } else if (body.itemData || body.buyNowPrice !== undefined) {
      rawList = [body];
    } else {
      rawList = [];
    }

    const out = [];
    for (const entry of rawList) {
      const normalized = normalizeAuction(entry);
      if (normalized) out.push(normalized);
    }
    return out;
  }

  /**
   * Resolves ONE exact card id for a response, when the response is
   * confidently about a single card - which is the normal case for a
   * transfer-market search scoped to one player/version.
   *
   * Returns { sourceCardId, auctions } when every identified auction
   * agrees on the same sourceCardId, or { sourceCardId: null, auctions,
   * groupedByCardId } when the response mixes multiple cards (e.g. an
   * unfiltered/"any version" search) - in that case the caller can still
   * act per-group, but must not silently pick one card as "the" card.
   */
  function detectCardIdentity(body) {
    const auctions = extractAuctionsFromResponse(body);
    if (auctions.length === 0) {
      return { sourceCardId: null, auctions, groupedByCardId: {} };
    }

    const groupedByCardId = {};
    for (const auction of auctions) {
      (groupedByCardId[auction.sourceCardId] = groupedByCardId[auction.sourceCardId] || []).push(auction);
    }

    const distinctIds = Object.keys(groupedByCardId);
    const sourceCardId = distinctIds.length === 1 ? Number(distinctIds[0]) : null;

    return { sourceCardId, auctions, groupedByCardId };
  }

  return {
    extractExactCardId,
    normalizeAuction,
    extractAuctionsFromResponse,
    detectCardIdentity,
  };
});
