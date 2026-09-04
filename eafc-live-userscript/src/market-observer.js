// src/market-observer.js
//
// Runs in the userscript manager's DEFAULT (non-page-injected) context -
// see page-hook.js's header for why the actual fetch/XHR observation
// moved there instead, and metadata.js for why @inject-into page was
// dropped. This file's job is now just:
//   1. inject page-hook.js's bundled source as a real <script> tag
//      (guaranteed to run in the page's TRUE realm on every manager,
//      independent of whatever context this file itself runs in)
//   2. listen for the CustomEvent it dispatches, group the already-
//      sanitized auctions by card id, and hand the result to whoever
//      registered via onCardDetected()
//
// PAGE_HOOK_SOURCE is injected by build.mjs as a string constant (see
// that file) - esbuild bundles src/page-hook.js to its own tiny IIFE
// separately from this file's own bundle, so the two never share
// module-scope state; the CustomEvent on `window` is the only channel
// between them, by design (see page-hook.js's header for why).
import { groupAuctionsByCardId } from "./card-detector.js";
import PAGE_HOOK_SOURCE from "virtual:page-hook-source";

const EVENT_NAME = "eafc-live:transfermarket";

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
}

let onDetected = null;
let installed = false;

/** Registers the single callback invoked when a Transfer Market
 * response yields at least one identified card. Called with
 * { sourceCardId, auctions, groupedByCardId, url }. */
export function onCardDetected(callback) {
  onDetected = callback;
}

function handleSanitizedAuctions(url, auctions) {
  if (!Array.isArray(auctions) || auctions.length === 0) return;

  let result;
  try {
    result = groupAuctionsByCardId(auctions);
  } catch (err) {
    log("card grouping failed, ignoring response:", err && err.message);
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
      onDetected({ ...result, url });
    } catch (err) {
      log("onCardDetected callback threw:", err && err.message);
    }
  }
}

/**
 * Injects page-hook.js's bundled source as a real <script> element (runs
 * in the page's true realm - see page-hook.js's header) and starts
 * listening for the sanitized-auctions event it dispatches. Idempotent
 * per page (module-level `installed` guard) - see lifecycle.js, which
 * is what actually decides when re-init is needed; this function's own
 * idempotency is just defense-in-depth against a double call.
 */
export function installMarketObserver() {
  if (installed) return;
  installed = true;

  window.addEventListener(EVENT_NAME, (event) => {
    const detail = event && event.detail;
    if (!detail) return;
    handleSanitizedAuctions(detail.url, detail.auctions);
  });

  try {
    const script = document.createElement("script");
    script.textContent = PAGE_HOOK_SOURCE;
    (document.head || document.documentElement).appendChild(script);
    script.remove();
  } catch (err) {
    console.error("[EAFC.Live] failed to inject page hook - market observation disabled:", err);
  }
}
