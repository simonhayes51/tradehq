// src/content.js
// Runs in the content script's ISOLATED world - the entry point that
// wires the other modules together. Loaded last (see manifest.json)
// so window.EAFCLive.{detectCardIdentity, combineIntelligence,
// getCardIntelligence, onCardDetected, overlay} are all already attached.
//
// Everything here is read-only: it reacts to Transfer Market responses
// the Web App already fetched (market-observer.js), asks our own
// backend for intelligence on the exact card id (eafc-api.js), combines
// that with the live auctions (market-calc.js), and renders a small
// panel (overlay.js). It never issues a request to EA and never writes
// to the page's DOM outside of its own overlay panel.
(function () {
  "use strict";

  const cfg = window.EAFC_LIVE_CONFIG || { DEBUG: false };
  function log(...args) {
    if (cfg.DEBUG) console.debug("[EAFC Live]", ...args);
  }

  const EAFCLive = window.EAFCLive || {};
  if (
    typeof EAFCLive.onCardDetected !== "function" ||
    typeof EAFCLive.getCardIntelligence !== "function" ||
    typeof EAFCLive.combineIntelligence !== "function" ||
    !EAFCLive.overlay
  ) {
    // One of the pieces failed to load - fail harmlessly rather than
    // throwing into the host page. The Web App is entirely unaffected;
    // there is simply no overlay.
    console.warn("[EAFC Live] one or more modules failed to initialize; overlay disabled");
    return;
  }

  // Tracks the last card we rendered for, so a burst of near-identical
  // Transfer Market responses (EA's own polling/pagination) doesn't
  // re-render on every single one - only when the detected card or its
  // live auctions meaningfully change.
  let lastRenderKey = null;

  async function handleDetection(result) {
    const { sourceCardId, auctions } = result;
    if (!sourceCardId) {
      // Response covered multiple distinct cards (e.g. an unfiltered
      // search) - V1 only surfaces a single-card confirmation panel, so
      // there is nothing safe to show without guessing which card the
      // user is looking at.
      return;
    }

    const renderKey = `${sourceCardId}:${auctions.length}:${EAFCLive.cheapestBin(auctions)}`;
    if (renderKey === lastRenderKey) return;
    lastRenderKey = renderKey;

    EAFCLive.overlay.render({ kind: "loading" });

    const intelligence = await EAFCLive.getCardIntelligence(sourceCardId);
    if (!intelligence) {
      EAFCLive.overlay.render({ kind: "no-intelligence" });
      return;
    }

    const combined = EAFCLive.combineIntelligence(auctions, intelligence);
    log("cheapest BIN:", combined.cheapestBin);
    log(`${combined.underBuyBelowCount} listing(s) under target`);

    EAFCLive.overlay.render({
      kind: "result",
      player: {
        name: intelligence.name,
        rarity: intelligence.rarity,
        rating: intelligence.rating,
      },
      intelligence,
      combined,
    });
  }

  EAFCLive.onCardDetected(handleDetection);
  log("EAFC Live Companion content script ready");
})();
