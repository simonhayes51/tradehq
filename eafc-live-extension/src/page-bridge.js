// src/page-bridge.js
// Runs in the page's MAIN world (see manifest.json), after
// debug-flag.js, card-detector.js and ea-session.js.
//
// Component 2 of the milestone: OBSERVE requests the Web App is already
// making, rather than issuing any of our own. This file wraps
// window.fetch and XMLHttpRequest so it can read responses the app
// itself requested - it never originates a request to EA, never changes
// a request's method/URL/body/headers, and always calls through to the
// real implementation. If anything in this file throws, the wrapped
// call still completes normally - a bug here must never break the Web
// App.
//
// SANITIZATION AT THE WORLD BOUNDARY
// -------------------------------------------------------------------
// Earlier V1 dispatched the ENTIRE parsed /transfermarket response body
// across the MAIN -> isolated-world CustomEvent. That was broader than
// this extension needs: the isolated world (src/market-observer.js
// onward) only ever reads card identity and a handful of pricing/timing
// fields. This file now runs card-detector.js's extraction/sanitization
// IN THIS (MAIN) WORLD, before anything is dispatched, so the event
// payload is already reduced to exactly:
//
//   { sourceCardId, tradeId, rating, buyNowPrice, currentBid,
//     startingBid, expires, tradeState }
//
// per auction - never the raw itemData, never any other field EA's
// response happens to carry, and never a request header, cookie, or
// session value (this file never reads request headers at all, on
// purpose - see below).
//
// SECURITY: this file inspects response BODIES for market data but
// never reads request HEADERS, and never logs a full response body
// (only counts/ids - see the [EAFC.Live] debug lines below). EA session
// values such as X-UT-SID / X-UT-PHISHING-TOKEN pass through this
// wrapper completely untouched and unread, exactly like every other
// request header - and even if they weren't, nothing containing them
// would cross into the isolated world, because only the sanitized
// auction fields above ever do.
(function () {
  "use strict";

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }

  // EA versions the Ultimate Team API path by game. Keep this scoped to
  // /ut/game/fcNN while accepting the active season (FC26, FC27, future).
  const FC_GAME_RE = /\/ut\/game\/fc\d+(\/|$)/i;
  const TRANSFERMARKET_RE = /\/transfermarket(\/|$|\?)/;

  function sanitizeAuctions(body) {
    const extract = window.EAFCLive && window.EAFCLive.extractAuctionsFromResponse;
    if (typeof extract !== "function") {
      // card-detector.js failed to load in this world - fail harmlessly,
      // no market event is dispatched at all.
      return null;
    }
    try {
      return extract(body);
    } catch (err) {
      log("card extraction failed in MAIN world, dropping response:", err && err.message);
      return null;
    }
  }

  function dispatchSanitizedAuctions(url, status, bodyText) {
    let body = null;
    try {
      body = bodyText ? JSON.parse(bodyText) : null;
    } catch (_err) {
      // Not JSON, or truncated - just means we can't parse this one.
      // The observer downstream treats "no usable body" the same as
      // "format we don't recognise" and shows nothing, per the
      // milestone's graceful-failure requirement.
      return;
    }

    const auctions = sanitizeAuctions(body);
    if (auctions === null) return;

    try {
      window.dispatchEvent(
        new CustomEvent("eafc-live:transfermarket", {
          detail: { url, status, auctions },
        })
      );
    } catch (err) {
      log("failed to dispatch sanitized market event:", err && err.message);
    }
  }

  function noteRequest(url, status) {
    try {
      if (window.__eafcLiveSession && typeof window.__eafcLiveSession._noteRequest === "function") {
        window.__eafcLiveSession._noteRequest(url, status);
      }
    } catch (_err) {
      // Never let session bookkeeping break request observation.
    }
  }

  // ---------------------------------------------------------------------
  // fetch()
  // ---------------------------------------------------------------------
  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function eafcLiveFetch(input, init) {
      const promise = originalFetch.apply(this, arguments);
      try {
        const url = typeof input === "string" ? input : input && input.url;
        if (url && FC_GAME_RE.test(url)) {
          promise
            .then((response) => {
              noteRequest(url, response.status);
              if (!TRANSFERMARKET_RE.test(url)) return;
              // Clone so we never consume the body the page itself is
              // about to read - the app's own .json()/.text() call must
              // see an untouched stream.
              response
                .clone()
                .text()
                .then((text) => dispatchSanitizedAuctions(url, response.status, text))
                .catch(() => {});
            })
            .catch(() => {
              // Network/CORS failure on the app's own request - not ours
              // to handle, and not logged as our failure.
            });
        }
      } catch (err) {
        log("fetch hook observation failed (request unaffected):", err && err.message);
      }
      return promise;
    };
  }

  // ---------------------------------------------------------------------
  // XMLHttpRequest
  // ---------------------------------------------------------------------
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
      if (url && FC_GAME_RE.test(url)) {
        this.addEventListener("loadend", function () {
          try {
            noteRequest(url, this.status);
            if (!TRANSFERMARKET_RE.test(url)) return;
            dispatchSanitizedAuctions(url, this.status, this.responseText);
          } catch (err) {
            log("XHR hook observation failed (request unaffected):", err && err.message);
          }
        });
      }
      return originalSend.apply(this, arguments);
    };
  }

  // Always printed (not gated behind EAFC_LIVE_DEBUG) - this is the one
  // line that confirms the MAIN world actually got this file at all, and
  // `inIframe`/`href` are needed to rule out "the real Transfer Market
  // UI renders inside an iframe, which this never saw" (manifest.json's
  // content_scripts don't set all_frames, so only the top-level document
  // is injected into by default).
  console.log("[EAFC.Live] page-bridge installed - observing active /ut/game/fc*/ responses", {
    href: location.href,
    inIframe: window !== window.top,
  });
})();
