// src/content.js
// Runs in the content script's ISOLATED world - the entry point that
// wires the other modules together. Loaded last (see manifest.json)
// so window.EAFCLive.{groupAuctionsByCardId, combineIntelligence,
// verdictLabel, getCardIntelligence, onCardDetected, overlay} are all
// already attached.
//
// Everything here is read-only: it reacts to Transfer Market responses
// the Web App already fetched (market-observer.js), asks the background
// service worker for intelligence on the exact card id (eafc-api.js ->
// background.js), combines that with the live auctions (market-calc.js),
// and renders a small panel (overlay.js). It never issues a request to
// EA and never writes to the page's DOM outside of its own overlay panel.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.debug("[EAFC Live]", ...args);
  }

  const EAFCLive = window.EAFCLive || {};
  if (
    typeof EAFCLive.onCardDetected !== "function" ||
    typeof EAFCLive.getCardIntelligence !== "function" ||
    typeof EAFCLive.combineIntelligence !== "function" ||
    typeof EAFCLive.verdictLabel !== "function" ||
    !EAFCLive.overlay
  ) {
    // One of the pieces failed to load - fail harmlessly rather than
    // throwing into the host page. The Web App is entirely unaffected;
    // there is simply no overlay.
    console.warn("[EAFC Live] one or more modules failed to initialize; overlay disabled");
    return;
  }

  // Statuses that mean "ask again later" rather than "here is the
  // answer" - a failure of this kind must never permanently suppress a
  // retry for the same unchanged market state (see the fix's own
  // discussion: this used to be set BEFORE the backend call resolved,
  // so a single transient outage could lock a card out of ever
  // rendering, even once the backend recovered).
  const RETRYABLE_STATUSES = new Set(["network_error", "server_error", "timeout"]);

  // The last renderKey we produced a TERMINAL result for (success or a
  // confirmed not_found) - deliberately NOT updated on a retryable
  // failure, so the identical detection firing again still retries.
  let lastRenderKey = null;

  function renderKeyFor(sourceCardId, auctions) {
    return `${sourceCardId}:${auctions.length}:${EAFCLive.cheapestBin(auctions)}`;
  }

  async function handleDetection(result) {
    const { sourceCardId, auctions } = result;
    if (!sourceCardId) {
      // Response covered multiple distinct cards (e.g. an unfiltered
      // search) - V1 only surfaces a single-card confirmation panel, so
      // there is nothing safe to show without guessing which card the
      // user is looking at.
      return;
    }

    const key = renderKeyFor(sourceCardId, auctions);
    if (key === lastRenderKey) return;

    log("exact card ID:", sourceCardId);
    EAFCLive.overlay.render({ kind: "loading", key });

    const intelResult = await EAFCLive.getCardIntelligence(sourceCardId);

    if (RETRYABLE_STATUSES.has(intelResult.status)) {
      // Do NOT update lastRenderKey here - an identical detection event
      // firing again later (e.g. the same search re-runs, or EA's own
      // pagination repeats the page) must be allowed to retry the
      // backend rather than being silently swallowed by the dedupe
      // check above.
      log("intelligence request failed:", intelResult.status);
      EAFCLive.overlay.render({ kind: "error", status: intelResult.status, key });
      return;
    }

    // Terminal outcome (success or a real 404) - safe to stop
    // re-rendering for this exact market state until it changes.
    lastRenderKey = key;

    if (intelResult.status === "not_found") {
      EAFCLive.overlay.render({ kind: "no-intelligence", key });
      return;
    }

    const intelligence = intelResult.data;
    const combined = EAFCLive.combineIntelligence(auctions, intelligence);
    log("intelligence loaded, signal:", intelligence.signal);
    log("cheapest BIN:", combined.cheapestBin);
    log(`${combined.underBuyBelowCount} listing(s) under target`);

    EAFCLive.overlay.render({
      kind: "result",
      key,
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
