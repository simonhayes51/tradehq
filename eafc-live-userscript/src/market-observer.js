// src/market-observer.js
//
// Merges what eafc-live-extension split across two files -
// src/page-bridge.js (MAIN world: hook fetch/XHR, sanitize) and
// src/market-observer.js (isolated world: listen for the CustomEvent,
// group by card id) - into one, because a userscript with
// `@inject-into page` runs in a SINGLE execution context: there is no
// world boundary to bridge across, so the CustomEvent dispatch/listen
// indirection the Chrome extension needed is pure overhead here. This is
// the "do not blindly reproduce the MAIN world -> CustomEvent ->
// isolated content script -> service worker architecture, simplify it"
// instruction in practice.
//
// Component 2 of the milestone, preserved unchanged in behaviour:
// OBSERVE requests the Web App is already making, rather than issuing
// any of our own. This wraps window.fetch and XMLHttpRequest so it can
// read responses the app itself requested - it never originates a
// request to EA, never changes a request's method/URL/body/headers, and
// always calls through to the real implementation. If anything in this
// file throws, the wrapped call still completes normally - a bug here
// must never break the Web App.
//
// SECURITY: this file inspects response BODIES for market data but never
// reads request HEADERS, and never retains a full raw response - only
// the sanitized fields extractAuctionsFromResponse() keeps
// (sourceCardId/tradeId/rating/buyNowPrice/currentBid/startingBid/
// expires/tradeState) ever reach a callback. EA session values such as
// X-UT-SID / X-UT-PHISHING-TOKEN pass through this wrapper completely
// untouched and unread, exactly like every other request header.
import { extractAuctionsFromResponse, groupAuctionsByCardId } from "./card-detector.js";

const FC26_RE = /\/ut\/game\/fc26(\/|$)/;
const TRANSFERMARKET_RE = /\/transfermarket(\/|$|\?)/;

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

let onDetected = null;
let installed = false;

/** Registers the single callback invoked when a Transfer Market
 * response yields at least one identified card. Called with
 * { sourceCardId, auctions, groupedByCardId, url }. */
export function onCardDetected(callback) {
  onDetected = callback;
}

function handleResponseBody(url, bodyText) {
  let body = null;
  try {
    body = bodyText ? JSON.parse(bodyText) : null;
  } catch (_err) {
    // Not JSON, or truncated - just means we can't parse this one.
    return;
  }

  let auctions;
  try {
    auctions = extractAuctionsFromResponse(body);
  } catch (err) {
    log("card extraction failed, dropping response:", err && err.message);
    return;
  }
  if (!auctions || auctions.length === 0) {
    log("no identifiable auctions in this response");
    return;
  }

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
 * Installs the fetch/XHR hooks exactly once per page (module-level
 * `installed` guard). Safe to call again on SPA re-entry - see
 * lifecycle.js, which is what actually decides when re-init is needed;
 * this function's own idempotency is just defense-in-depth against a
 * double call.
 */
export function installMarketObserver() {
  if (installed) return;
  installed = true;

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function eafcLiveFetch(input, init) {
      const promise = originalFetch.apply(this, arguments);
      try {
        const url = typeof input === "string" ? input : input && input.url;
        if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {
          promise
            .then((response) => {
              // Clone so we never consume the body the page itself is
              // about to read - the app's own .json()/.text() call must
              // see an untouched stream.
              response
                .clone()
                .text()
                .then((text) => handleResponseBody(url, text))
                .catch(() => {});
            })
            .catch(() => {
              // Network/CORS failure on the app's own request - not
              // ours to handle, and not logged as our failure.
            });
        }
      } catch (err) {
        log("fetch hook observation failed (request unaffected):", err && err.message);
      }
      return promise;
    };
  }

  const OriginalXHR = window.XMLHttpRequest;
  if (OriginalXHR) {
    const originalOpen = OriginalXHR.prototype.open;
    const originalSend = OriginalXHR.prototype.send;

    OriginalXHR.prototype.open = function eafcLiveOpen(method, url) {
      try {
        this.__eafcLiveUrl = String(url);
      } catch (_err) {
        this.__eafcLiveUrl = null;
      }
      return originalOpen.apply(this, arguments);
    };

    OriginalXHR.prototype.send = function eafcLiveSend() {
      const url = this.__eafcLiveUrl;
      if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {
        this.addEventListener("loadend", function () {
          try {
            handleResponseBody(url, this.responseText);
          } catch (err) {
            log("XHR hook observation failed (request unaffected):", err && err.message);
          }
        });
      }
      return originalSend.apply(this, arguments);
    };
  }

  console.log("[EAFC Live] market observer installed - watching window.fetch/XHR for /ut/game/fc26 responses", {
    href: location.href,
    inIframe: window !== window.top,
  });
}
