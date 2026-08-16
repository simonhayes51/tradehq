// src/content.js
// Runs in the content script's ISOLATED world - the entry point that
// wires the other modules together. Loaded last (see manifest.json).
//
// Everything here is read-only: it reacts to Transfer Market responses
// the Web App already fetched (market-observer.js), asks the background
// service worker for intelligence on the exact card id (eafc-api.js ->
// background.js), combines that with the live auctions (market-calc.js),
// and renders a small panel (overlay.js). It never issues a request to
// EA and never writes to the page's DOM outside of its own overlay panel.
//
// WHY window.EAFCLive's PIECES ARE READ FRESH, NOT CAPTURED ONCE
// -------------------------------------------------------------------
// This file previously captured `window.EAFCLive` in a const ONCE at the
// top and refused to do anything at all (including registering with
// market-observer.js) unless every piece it needs was already present at
// that exact synchronous moment. Live testing against the real EA Web
// App surfaced a case where content scripts in the same manifest.json
// array did not finish attaching their exports in declared order on a
// given load - see market-observer.js's own header for the fuller
// account. The fix there was to stop assuming synchronous load-order
// guarantees; the same applies here. `onCardDetected` (needed
// immediately, to register at all) gets a small bounded retry; every
// other piece (getCardIntelligence, combineIntelligence, verdictLabel,
// overlay) is only ever actually needed once a Transfer Market response
// has arrived - which happens well after every content script has
// finished running regardless of relative ordering - so those are read
// fresh off window.EAFCLive inside handleDetection() rather than
// snapshotted at load time.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }

  // Statuses that mean "ask again later" rather than "here is the
  // answer" - a failure of this kind must never permanently suppress a
  // retry for the same unchanged market state (this used to be set
  // BEFORE the backend call resolved, so a single transient outage could
  // lock a card out of ever rendering, even once the backend recovered).
  const RETRYABLE_STATUSES = new Set(["network_error", "server_error", "timeout"]);

  // The last renderKey we produced a TERMINAL result for (success or a
  // confirmed not_found) - deliberately NOT updated on a retryable
  // failure, so the identical detection firing again still retries.
  let lastRenderKey = null;

  function renderKeyFor(sourceCardId, auctions, cheapestBinFn) {
    return `${sourceCardId}:${auctions.length}:${cheapestBinFn(auctions)}`;
  }

  async function handleDetection(result) {
    const EAFCLive = window.EAFCLive || {};
    const missing = [];
    for (const key of ["combineIntelligence", "verdictLabel", "getCardIntelligence", "cheapestBin"]) {
      if (typeof EAFCLive[key] !== "function") missing.push(key);
    }
    if (!EAFCLive.overlay) missing.push("overlay");
    if (missing.length > 0) {
      // Still not ready by the time an actual response arrived (rather
      // than merely at load time) - now genuinely worth reporting,
      // rather than a load-order race we'd otherwise recover from.
      console.warn("[EAFC Live] required modules still unavailable when a market response arrived", {
        href: location.href,
        missing,
        eafcLiveKeys: Object.keys(EAFCLive),
      });
      return;
    }

    const { sourceCardId, auctions } = result;
    if (!sourceCardId) {
      // Response covered multiple distinct cards (e.g. an unfiltered
      // search) - V1 only surfaces a single-card confirmation panel, so
      // there is nothing safe to show without guessing which card the
      // user is looking at.
      return;
    }

    const key = renderKeyFor(sourceCardId, auctions, EAFCLive.cheapestBin);
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

  // onCardDetected is the one thing that must exist synchronously, to
  // register at all - bounded retry (10 attempts, 100ms apart, 1s total)
  // rather than an immediate hard fail, since market-observer.js (which
  // defines it) is only ever one file-load away in the normal case, and
  // this exists purely to tolerate the same load-order looseness
  // documented above.
  function register(attemptsLeft) {
    const onCardDetected = window.EAFCLive && window.EAFCLive.onCardDetected;
    if (typeof onCardDetected === "function") {
      onCardDetected(handleDetection);
      log("EAFC Live Companion content script ready");
      return;
    }
    if (attemptsLeft <= 0) {
      console.warn("[EAFC Live] onCardDetected never became available; overlay disabled", {
        href: location.href,
        eafcLiveKeys: window.EAFCLive ? Object.keys(window.EAFCLive) : null,
      });
      return;
    }
    setTimeout(() => register(attemptsLeft - 1), 100);
  }

  register(10);
})();
