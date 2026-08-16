// src/card-detector.js
//
// Pure logic: turn an EA FC 26 Web App /transfermarket response body into
// exact card identity + a normalized, SANITIZED auction list. No DOM, no
// network, no extension APIs - this file is unit-testable directly under
// Node (see tests/card-detector.test.js) and is also loaded as a plain
// script in TWO separate browser execution contexts (see manifest.json):
//
//   - the page's MAIN world (src/page-bridge.js uses
//     extractAuctionsFromResponse() to build the sanitized payload it
//     dispatches - see that file's own header for why only a handful of
//     fields ever cross the MAIN -> isolated-world boundary)
//   - the content script's ISOLATED world (src/market-observer.js uses
//     groupAuctionsByCardId() over the already-sanitized auctions it
//     receives, to decide whether a response is confidently about a
//     single card)
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
// This has NOT been confirmed against a live authenticated session in
// this environment - see the extension README's live-test checklist for
// what still needs manual verification. The code below is written
// defensively specifically because of that: every field is read with
// fallbacks, nothing throws on an unexpected shape, and identity
// resolution refuses to guess rather than silently return a wrong card id.
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
// intelligence onto a totally different card's numbers. It is also
// deliberately dropped from the sanitized auction shape entirely (see
// normalizeAuction below) - once sourceCardId is resolved from
// resourceId/definitionId, nothing downstream has any use for the raw
// EA player id, so it never needs to leave the MAIN world at all.
(function (factory) {
  // Prefer the browser-global branch whenever ANY browser-like global is
  // present (window for a page/content script, importScripts for a
  // service worker), and only fall back to module.exports when NEITHER
  // exists - i.e. we're definitely under Node (the test runner).
  //
  // The naive UMD check (`typeof module !== "undefined" && module.exports`)
  // is not safe to run inside a content script: EA's own bundled page JS
  // can leave a global `module` behind (a common leftover from vendor
  // code built for CommonJS), and since content scripts execute against
  // the SAME shared window/global as the page, `typeof module` sees it
  // too - tripping this file into the module.exports branch and never
  // attaching anything to window.EAFCLive at all. Confirmed live: this
  // is exactly what happened on the real EA FC 26 Web App
  // (market-observer.js logged "card-detector unavailable" even though
  // this file loaded and ran with no error).
  const isBrowserLike = typeof window !== "undefined" || typeof importScripts === "function";
  if (!isBrowserLike && typeof module !== "undefined" && module.exports) {
    module.exports = factory();
    return;
  }
  const root = typeof window !== "undefined" ? window : globalThis;
  root.EAFCLive = root.EAFCLive || {};
  try {
    Object.assign(root.EAFCLive, factory());
  } catch (err) {
    // Previously silent: a throw here left root.EAFCLive as an empty
    // object with none of this file's functions attached, and every
    // downstream file (market-observer.js, content.js) could only ever
    // report "card-detector unavailable" with no way to say why. Loud
    // now on purpose - this is exactly the class of failure that was
    // otherwise invisible during live testing against the real EA page.
    console.error("[EAFC Live] card-detector.js failed to initialize:", err);
  }
})(function () {
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
   * Normalizes one auctionInfo[] entry into the flat, SANITIZED shape
   * that is safe to cross the MAIN -> isolated-world boundary (see
   * src/page-bridge.js) and is all the rest of the extension ever needs:
   * exact card identity plus the handful of pricing/timing fields the
   * overlay renders. Deliberately excludes maskedDefId, itemType, seller
   * info, or any other field EA's response happens to carry - narrower
   * than "everything EA gave us", not a passthrough of it.
   *
   * Returns null (rather than throwing) for an entry that doesn't look
   * like a real, identifiable auction - a defensively-written caller can
   * just filter those out.
   */
  function normalizeAuction(entry) {
    if (!entry || typeof entry !== "object") return null;
    const item = entry.itemData || entry.item || {};
    const sourceCardId = extractExactCardId(item);
    if (sourceCardId === null) return null;

    return {
      sourceCardId,
      tradeId: toPositiveInt(entry.tradeId ?? item.id) ?? null,
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
   * auction we could confidently identify, already sanitized down to
   * the fields normalizeAuction() keeps. Tolerant of:
   *   - a bare array instead of {auctionInfo: [...]}
   *   - a single-item response ({itemData: {...}, buyNowPrice: ...})
   *   - fields missing/renamed/of the wrong type
   * Never throws - an unrecognised shape just yields an empty list, and
   * the caller (page-bridge.js / market-observer.js) treats that exactly
   * like "EA's response format changed, show nothing" per the
   * milestone's graceful-failure requirement.
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
   * Resolves ONE exact card id over a list of ALREADY-SANITIZED auctions
   * (each already carrying a numeric sourceCardId - i.e. the shape
   * normalizeAuction()/extractAuctionsFromResponse() produce, or the
   * sanitized payload src/page-bridge.js dispatches across the world
   * boundary). Pure grouping - no raw itemData involved, which is why
   * this can run entirely in the isolated world on the payload it
   * receives, with no access to (or need for) the original EA response.
   *
   * Returns { sourceCardId, auctions, groupedByCardId }:
   *   - sourceCardId is set only when every auction agrees on one card -
   *     the normal case for a Transfer Market search scoped to one
   *     player/version.
   *   - groupedByCardId always reflects the full breakdown, so a caller
   *     that wants to act per-group on a mixed response still can,
   *     without this function silently picking one card to call "the"
   *     card.
   */
  function groupAuctionsByCardId(auctions) {
    const list = Array.isArray(auctions)
      ? auctions.filter((a) => a && Number.isFinite(a.sourceCardId))
      : [];

    const groupedByCardId = {};
    for (const auction of list) {
      (groupedByCardId[auction.sourceCardId] = groupedByCardId[auction.sourceCardId] || []).push(auction);
    }

    const distinctIds = Object.keys(groupedByCardId);
    const sourceCardId = distinctIds.length === 1 ? Number(distinctIds[0]) : null;

    return { sourceCardId, auctions: list, groupedByCardId };
  }

  /**
   * Convenience wrapper: extract + group in one call, for callers (tests,
   * or a context that legitimately has the raw response body) that don't
   * need the sanitize/group split. src/page-bridge.js and
   * src/market-observer.js call the two halves separately instead, since
   * one runs in the MAIN world and the other in the isolated world.
   */
  function detectCardIdentity(body) {
    return groupAuctionsByCardId(extractAuctionsFromResponse(body));
  }

  return {
    extractExactCardId,
    normalizeAuction,
    extractAuctionsFromResponse,
    groupAuctionsByCardId,
    detectCardIdentity,
  };
});
