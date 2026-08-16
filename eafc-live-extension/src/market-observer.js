// src/market-observer.js
// Runs in the content script's ISOLATED world (see manifest.json).
//
// Bridges page-bridge.js (MAIN world) to the rest of the extension:
// listens for the `eafc-live:transfermarket` DOM event page-bridge.js
// dispatches on `window` - already carrying a SANITIZED auction list
// (sourceCardId/tradeId/rating/buyNowPrice/currentBid/startingBid/
// expires/tradeState only, see page-bridge.js's header for why) - groups
// it by card identity, and hands the result to whatever callback
// content.js has registered.
//
// This file does no rendering, no network calls, and no raw EA response
// parsing of its own (that now happens entirely in the MAIN world,
// before the event is even dispatched) - it is purely the listen ->
// group -> notify wiring.
//
// WHY window.EAFCLive.groupAuctionsByCardId IS CHECKED LAZILY, NOT ONCE
// AT LOAD TIME
// -------------------------------------------------------------------
// This file previously read `window.EAFCLive.groupAuctionsByCardId`
// ONCE, synchronously, at the top of this IIFE, and bailed out (never
// registering onCardDetected at all) if it wasn't there yet. Live
// testing against the real EA Web App surfaced a case where, at that
// exact synchronous moment, window.EAFCLive already had market-calc.js's
// and eafc-api.js's exports (files listed AFTER card-detector.js in
// manifest.json) but NOT card-detector.js's own - i.e. content scripts
// in the same array did not finish attaching in the declared order on
// that particular load. The root cause was never fully pinned down (and
// may be intermittent), but the fix does not require knowing it: nothing
// in this file needs to resolve window.EAFCLive.groupAuctionsByCardId
// before the very first Transfer Market response arrives, which happens
// well after every content script's top-level code has finished running
// regardless of relative ordering between files. Reading it fresh inside
// the event handler - instead of capturing a possibly-still-undefined
// reference in a closure once at load time - makes this file correct
// regardless of exactly when card-detector.js finishes attaching.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }

  let onDetected = null;

  /** Registers the single callback invoked when a Transfer Market
   * response yields at least one identified card. Called with
   * { sourceCardId, auctions, groupedByCardId, url }. */
  function onCardDetected(callback) {
    onDetected = callback;
  }

  window.addEventListener("eafc-live:transfermarket", (event) => {
    const detail = event.detail;
    if (!detail || !Array.isArray(detail.auctions)) return;

    const grouper = window.EAFCLive && window.EAFCLive.groupAuctionsByCardId;
    if (typeof grouper !== "function") {
      // card-detector.js still hasn't attached - fail harmlessly for
      // just this one response rather than disabling observation
      // permanently. Diagnostic detail always printed (not gated behind
      // EAFC_LIVE_DEBUG) since this is precisely the failure mode that
      // was hard to pin down live - `inIframe` alone rules out/in the
      // "real Transfer Market UI renders inside an iframe this content
      // script never reached" theory (manifest.json's content_scripts
      // don't set all_frames, so only the top-level document is injected
      // into by default).
      console.warn(
        "[EAFC Live] card-detector unavailable for this response; skipping",
        {
          href: location.href,
          inIframe: window !== window.top,
          hasEAFCLive: typeof window.EAFCLive,
          eafcLiveKeys: window.EAFCLive ? Object.keys(window.EAFCLive) : null,
        }
      );
      return;
    }

    log("Transfer Market response detected:", detail.url);

    let result;
    try {
      result = grouper(detail.auctions);
    } catch (err) {
      log("card grouping failed, ignoring response:", err && err.message);
      return;
    }

    if (!result || result.auctions.length === 0) {
      log("no identifiable auctions in this response");
      return;
    }

    log(`${result.auctions.length} auction(s) detected`);
    if (result.sourceCardId) {
      log("exact card ID:", result.sourceCardId);
    } else {
      log("response contains multiple distinct card IDs; not treating as a single-card view");
    }

    if (typeof onDetected === "function") {
      try {
        onDetected({ ...result, url: detail.url });
      } catch (err) {
        log("onCardDetected callback threw:", err && err.message);
      }
    }
  });

  // Registered unconditionally, regardless of whether card-detector.js
  // has attached yet - content.js depends on this existing to wire
  // itself up at all (see content.js's own readiness check), and there
  // is no reason it should wait on a DIFFERENT file's timing when this
  // file's own job (listening and, once a grouper exists, grouping) does
  // not require it either.
  window.EAFCLive = window.EAFCLive || {};
  window.EAFCLive.onCardDetected = onCardDetected;
})();
