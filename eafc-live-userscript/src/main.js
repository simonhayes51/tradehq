// src/main.js
//
// Entry point - wires market-observer.js, api-client.js, market-calc.js
// and overlay.js together. Equivalent to what eafc-live-extension split
// across src/content.js (isolated-world wiring) + src/background.js
// (privileged fetch), simplified into one file because there is only one
// execution context here (see market-observer.js's header for the fuller
// rationale).
//
// Everything here is read-only: it reacts to Transfer Market responses
// the Web App already fetched, asks the EAFC Live backend for
// intelligence on the exact card id, combines that with the live
// auctions, and renders a small panel. It never issues a request to EA
// and never writes to the page's DOM outside of its own overlay panel.
import { onCardDetected, installMarketObserver } from "./market-observer.js";
import { getCardIntelligence } from "./api-client.js";
import { combineIntelligence, cheapestBin } from "./market-calc.js";
import { render as renderOverlay } from "./overlay.js";
import { initLifecycle } from "./lifecycle.js";

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

// Statuses that mean "ask again later" rather than "here is the
// answer" - a failure of this kind must never permanently suppress a
// retry for the same unchanged market state.
const RETRYABLE_STATUSES = new Set(["network_error", "server_error", "timeout"]);

// The last renderKey we produced a TERMINAL result for (success or a
// confirmed not_found) - deliberately NOT updated on a retryable
// failure, so the identical detection firing again still retries.
let lastRenderKey = null;

function renderKeyFor(sourceCardId, auctions) {
  return `${sourceCardId}:${auctions.length}:${cheapestBin(auctions)}`;
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
  renderOverlay({ kind: "loading", key });

  const intelResult = await getCardIntelligence(sourceCardId);

  if (RETRYABLE_STATUSES.has(intelResult.status)) {
    // Do NOT update lastRenderKey here - an identical detection event
    // firing again later must be allowed to retry the backend rather
    // than being silently swallowed by the dedupe check above.
    log("intelligence request failed:", intelResult.status, intelResult.detail);
    renderOverlay({ kind: "error", status: intelResult.status, detail: intelResult.detail, key });
    return;
  }

  // Terminal outcome (success or a real 404) - safe to stop
  // re-rendering for this exact market state until it changes.
  lastRenderKey = key;

  if (intelResult.status === "not_found") {
    renderOverlay({ kind: "no-intelligence", key });
    return;
  }

  const intelligence = intelResult.data;
  const combined = combineIntelligence(auctions, intelligence);
  log("intelligence loaded, signal:", intelligence.signal);
  log("cheapest BIN:", combined.cheapestBin);
  log(`${combined.underBuyBelowCount} listing(s) under target`);

  renderOverlay({
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

function setup() {
  onCardDetected(handleDetection);
  installMarketObserver();
  console.log("[EAFC Live] Companion userscript ready", { href: location.href });
}

initLifecycle(setup);
