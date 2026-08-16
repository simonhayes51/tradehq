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
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }

  const grouper = window.EAFCLive && window.EAFCLive.groupAuctionsByCardId;
  if (typeof grouper !== "function") {
    // card-detector.js failed to load or attach - fail harmlessly rather
    // than throwing on every market response. Diagnostic detail always
    // printed (not gated behind EAFC_LIVE_DEBUG) because this is
    // precisely the failure being live-debugged - in particular
    // `inIframe` distinguishes "this really is the top frame and
    // EAFCLive is genuinely missing" from "this script is running
    // inside a frame that never got card-detector.js at all", which
    // `all_frames` being unset in manifest.json would cause if the real
    // Transfer Market UI is rendered inside an iframe rather than the
    // top-level document.
    console.warn(
      "[EAFC Live] card-detector unavailable; Transfer Market observation disabled",
      {
        href: location.href,
        inIframe: window !== window.top,
        hasEAFCLive: typeof window.EAFCLive,
        eafcLiveKeys: window.EAFCLive ? Object.keys(window.EAFCLive) : null,
      }
    );
    return;
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

  window.EAFCLive.onCardDetected = onCardDetected;
})();
