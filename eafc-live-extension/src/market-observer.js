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
// WHY window.EAFCLive.groupAuctionsByCardId COMES FROM card-grouping.js,
// NOT card-detector.js, AND IS STILL CHECKED LAZILY
// -------------------------------------------------------------------
// This file used to depend on card-detector.js (loaded earlier in the
// same content_scripts entry). Live testing against the real EA Web App
// proved that file's ISOLATED-world copy never executed at all, even
// though it's declared in this entry and every OTHER file here ran
// fine: card-detector.js is ALSO declared in the MAIN-world entry (for
// page-bridge.js), and Chrome silently skips re-injecting a script
// resource it already injected once for a page, even across a different
// declared "world". See card-grouping.js's header for the full account -
// it is a standalone file containing only groupAuctionsByCardId,
// referenced from nowhere else, specifically so this never recurs.
//
// The lazy check below (reading window.EAFCLive.groupAuctionsByCardId
// fresh inside the event handler, rather than once at load time) is kept
// regardless, as cheap defense-in-depth: nothing in this file actually
// needs it resolved before the first Transfer Market response arrives,
// which is always well after every content script has finished running.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
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
      // card-grouping.js still hasn't attached - fail harmlessly for
      // just this one response rather than disabling observation
      // permanently. Diagnostic detail always printed (not gated behind
      // EAFC_LIVE_DEBUG): if this ever fires again, `inIframe` is the
      // first thing to check (whether the real Transfer Market UI is
      // rendering inside an iframe this content script never reached -
      // manifest.json's content_scripts don't set all_frames, so only
      // the top-level document is injected into by default).
      console.warn(
        "[EAFC.Live] card-grouping unavailable for this response; skipping",
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

  // Registered unconditionally, regardless of whether card-grouping.js
  // has attached yet - content.js depends on this existing to wire
  // itself up at all (see content.js's own readiness check), and there
  // is no reason it should wait on a DIFFERENT file's timing when this
  // file's own job (listening and, once a grouper exists, grouping) does
  // not require it either.
  window.EAFCLive = window.EAFCLive || {};
  window.EAFCLive.onCardDetected = onCardDetected;
})();
