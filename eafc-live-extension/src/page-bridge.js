// src/page-bridge.js
// Runs in the page's MAIN world (see manifest.json), after ea-session.js.
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
// SECURITY: this file inspects response BODIES for market data
// (auctionInfo, resourceId, buyNowPrice, ...) but never reads request
// HEADERS at all, and never logs a full response body (only counts/ids -
// see the [EAFC Live] debug lines below). EA session values such as
// X-UT-SID / X-UT-PHISHING-TOKEN pass through this wrapper completely
// untouched and unread, exactly like every other request header.
(function () {
  "use strict";

  const cfg = window.EAFC_LIVE_CONFIG || { DEBUG: false };
  function log(...args) {
    if (cfg.DEBUG) console.debug("[EAFC Live]", ...args);
  }

  const FC26_RE = /\/ut\/game\/fc26(\/|$)/;
  const TRANSFERMARKET_RE = /\/transfermarket(\/|$|\?)/;

  function dispatchResponse(url, status, bodyText) {
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
    try {
      window.dispatchEvent(
        new CustomEvent("eafc-live:fc26-response", {
          detail: { url, status, isTransferMarket: TRANSFERMARKET_RE.test(url), body },
        })
      );
    } catch (err) {
      log("failed to dispatch observed response event:", err && err.message);
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
        if (url && FC26_RE.test(url)) {
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
                .then((text) => dispatchResponse(url, response.status, text))
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
      if (url && FC26_RE.test(url)) {
        this.addEventListener("loadend", function () {
          try {
            noteRequest(url, this.status);
            if (!TRANSFERMARKET_RE.test(url)) return;
            dispatchResponse(url, this.status, this.responseText);
          } catch (err) {
            log("XHR hook observation failed (request unaffected):", err && err.message);
          }
        });
      }
      return originalSend.apply(this, arguments);
    };
  }

  log("page-bridge installed - observing window.fetch/XHR for /ut/game/fc26 responses");
})();
