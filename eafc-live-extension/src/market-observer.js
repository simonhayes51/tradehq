// src/market-observer.js
// Runs in the content script's ISOLATED world (see manifest.json).
//
// Bridges page-bridge.js (MAIN world, observes raw fetch/XHR responses)
// to the rest of the extension: listens for the `eafc-live:fc26-response`
// DOM event page-bridge.js dispatches on `window`, filters it down to
// Transfer Market responses, runs card-detector.js over the body, and
// hands the result to whatever callback content.js has registered.
//
// This file does no rendering and no network calls of its own - it is
// purely the listen -> detect -> notify wiring, kept separate from
// content.js so the "what do we do when a card is detected" logic isn't
// tangled up with "how do we hear about a response at all".
(function () {
  "use strict";

  const cfg = window.EAFC_LIVE_CONFIG || { DEBUG: false };
  function log(...args) {
    if (cfg.DEBUG) console.debug("[EAFC Live]", ...args);
  }

  const detector = window.EAFCLive && window.EAFCLive.detectCardIdentity;
  if (typeof detector !== "function") {
    // card-detector.js failed to load or attach - fail harmlessly rather
    // than throwing on every market response.
    console.warn("[EAFC Live] card-detector unavailable; Transfer Market observation disabled");
    return;
  }

  let onDetected = null;

  /** Registers the single callback invoked when a Transfer Market
   * response yields at least one identified card. Called with
   * { sourceCardId, auctions, groupedByCardId, url }. */
  function onCardDetected(callback) {
    onDetected = callback;
  }

  window.addEventListener("eafc-live:fc26-response", (event) => {
    const detail = event.detail;
    if (!detail || !detail.isTransferMarket) return;

    log("Transfer Market response detected:", detail.url);

    let result;
    try {
      result = detector(detail.body);
    } catch (err) {
      log("card detection failed, ignoring response:", err && err.message);
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
