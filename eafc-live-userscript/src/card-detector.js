// src/card-detector.js
//
// Pure logic: turn an EA FC 26 Web App /transfermarket response body into
// exact card identity + a normalized, SANITIZED auction list.
//
// Ported from eafc-live-extension/src/card-detector.js. The Chrome
// extension's version is UMD-wrapped because the same file has to run in
// two separate browser execution contexts (MAIN world + isolated world)
// AND under Node for tests - see that file's header for the full story,
// including the Chrome MV3 bug where a script referenced from two
// different content_scripts entries only gets injected once. None of
// that applies here: a userscript with `@inject-into page` runs in a
// SINGLE execution context, and this file is bundled as a plain ES
// module (see build.mjs), so it's exported directly with no UMD
// gymnastics and no world-boundary sanitization step of its own (the
// caller in market-observer.js already only ever passes this the parsed
// response body, nothing containing request headers/cookies).
//
// IDENTITY RULE - unchanged from the Chrome extension:
// resourceId (falling back to definitionId) is the exact card-version
// identity and maps directly to futgg_players.source_card_id.
// maskedDefId is NEVER used as the identity - it identifies the
// underlying player and collides across every special version of that
// player, so it is also deliberately dropped from the sanitized auction
// shape entirely.

function toPositiveInt(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
}

/**
 * The exact card-version id for one EA item object, or null if EA gave
 * us nothing usable. Never falls back to maskedDefId (see file header).
 */
export function extractExactCardId(itemData) {
  if (!itemData || typeof itemData !== "object") return null;
  return toPositiveInt(itemData.resourceId) || toPositiveInt(itemData.definitionId) || null;
}

/**
 * Normalizes one auctionInfo[] entry into the flat, SANITIZED shape the
 * rest of the userscript ever needs: exact card identity plus the
 * handful of pricing/timing fields the overlay renders. Deliberately
 * excludes maskedDefId, itemType, seller info, or any other field EA's
 * response happens to carry.
 *
 * Returns null (rather than throwing) for an entry that doesn't look
 * like a real, identifiable auction.
 */
export function normalizeAuction(entry) {
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
    // "unknown", never as "expired now".
    expires: toPositiveInt(entry.expires),
    tradeState: typeof entry.tradeState === "string" ? entry.tradeState : null,
  };
}

/**
 * Walks a parsed /transfermarket response body and returns every
 * auction we could confidently identify, already sanitized down to the
 * fields normalizeAuction() keeps. Tolerant of:
 *   - a bare array instead of {auctionInfo: [...]}
 *   - a single-item response ({itemData: {...}, buyNowPrice: ...})
 *   - fields missing/renamed/of the wrong type
 * Never throws - an unrecognised shape just yields an empty list.
 */
export function extractAuctionsFromResponse(body) {
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
 * Resolves ONE exact card id over a list of ALREADY-SANITIZED auctions.
 *
 * Returns { sourceCardId, auctions, groupedByCardId }:
 *   - sourceCardId is set only when every auction agrees on one card -
 *     the normal case for a Transfer Market search scoped to one
 *     player/version.
 *   - groupedByCardId always reflects the full breakdown, so a caller
 *     that wants to act per-group on a mixed response still can, without
 *     this function silently picking one card to call "the" card.
 */
export function groupAuctionsByCardId(auctions) {
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
 * Convenience wrapper: extract + group in one call.
 */
export function detectCardIdentity(body) {
  return groupAuctionsByCardId(extractAuctionsFromResponse(body));
}
