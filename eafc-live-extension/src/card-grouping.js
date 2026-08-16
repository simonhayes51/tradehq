// src/card-grouping.js
// ISOLATED-world-only counterpart to card-detector.js.
//
// WHY THIS FILE EXISTS SEPARATELY FROM card-detector.js
// -------------------------------------------------------------------
// Confirmed live against the real EA FC 26 Web App: card-detector.js is
// declared in BOTH manifest.json content_scripts entries (the MAIN world,
// for page-bridge.js's raw-response sanitization, and the ISOLATED world,
// for market-observer.js's grouping over the already-sanitized payload).
// Diagnostic logging proved the MAIN-world copy runs and attaches
// correctly, but the ISOLATED-world copy of that SAME FILE never
// executes at all - while every other file unique to the isolated
// world's content_scripts entry (market-calc.js, eafc-api.js,
// market-observer.js, overlay.js, content.js) runs fine. The one thing
// the two silently-skipped files (card-detector.js and debug-flag.js,
// see debug-flag-isolated.js) have in common is being referenced from
// BOTH content_scripts entries - Chrome appears to silently skip
// re-injecting a script resource it already injected once for that
// page, even across a different declared "world". This is not
// documented behaviour to rely on either way, so the fix is simply to
// never reference the same file path from two content_scripts entries
// again, rather than depend on understanding exactly why.
//
// The isolated world never needed everything card-detector.js exports
// anyway - see that file's own header on the MAIN/isolated split by
// design: raw EA response parsing (extractExactCardId, normalizeAuction,
// extractAuctionsFromResponse) happens entirely in the MAIN world before
// anything is dispatched, and market-observer.js only ever grouped an
// ALREADY-sanitized auction list it received across the world boundary.
// This file is that one function, standalone.
(function (factory) {
  // See card-detector.js's identical wrapper for the full reasoning.
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
    console.error("[EAFC Live] card-grouping.js failed to initialize:", err);
  }
})(function () {
  "use strict";

  /**
   * Resolves ONE exact card id over a list of ALREADY-SANITIZED auctions
   * (each already carrying a numeric sourceCardId - the shape
   * page-bridge.js's use of card-detector.js's extractAuctionsFromResponse
   * produces, dispatched across the MAIN -> isolated-world boundary).
   *
   * Returns { sourceCardId, auctions, groupedByCardId }:
   *   - sourceCardId is set only when every auction agrees on one card -
   *     the normal case for a Transfer Market search scoped to one
   *     player/version.
   *   - groupedByCardId always reflects the full breakdown, so a caller
   *     that wants to act per-group on a mixed response still can,
   *     without this function silently picking one card to call "the"
   *     card.
   *
   * Identical logic to card-detector.js's groupAuctionsByCardId - kept
   * in sync manually. Covered by the same shared test cases in
   * tests/card-grouping.test.js as card-detector.test.js's
   * groupAuctionsByCardId suite.
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

  return { groupAuctionsByCardId };
});
