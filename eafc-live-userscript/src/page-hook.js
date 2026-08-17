// src/page-hook.js
//
// Runs in the EA page's TRUE JS realm, regardless of what execution
// context the rest of this userscript runs in - see build.mjs for how
// this file gets bundled separately and injected as a plain <script>
// tag's textContent (a technique that always executes in the real page,
// the same way any page's own inline <script> would, independent of
// whatever sandboxing a given userscript manager applies to the
// userscript body itself).
//
// WHY THIS FILE EXISTS SEPARATELY FROM main.js/market-observer.js
// -------------------------------------------------------------------
// V1 ran the entire userscript with `@inject-into page`, on the
// assumption that doing so would both (a) give direct access to the
// page's real window.fetch/XMLHttpRequest for observation, AND (b)
// still expose GM_xmlhttpRequest/GM.xmlHttpRequest for the backend API
// call, since autosbc.user.js's own metadata uses that exact
// combination. Confirmed live on a real iOS session running the
// "Userscripts" app: (a) held, (b) did not - neither GM API was
// reachable from a script actually running in page context on that
// manager. `@inject-into page` opts a script OUT of that manager's
// privileged bridge entirely on at least one real, currently-used
// manager, not just a rare edge case.
//
// So responsibility is split by what each half actually needs:
//   - Observing window.fetch/XHR needs the REAL page context (a
//     sandboxed content-script-style context on some managers has its
//     OWN wrapped fetch/XHR that never sees the page's real network
//     traffic) - this file, injected as a <script> tag, always runs
//     there regardless of manager.
//   - Calling the EAFC Live backend needs GM_xmlhttpRequest/
//     GM.xmlHttpRequest, which is reliably available in a manager's
//     DEFAULT (non-page-injected) context - main.js/api-client.js now
//     run there instead (see metadata.js for the dropped @inject-into).
//
// The two communicate via a CustomEvent on `window`, carrying ONLY the
// already-sanitized auction fields - functionally the same boundary
// eafc-live-extension's src/page-bridge.js drew between its MAIN and
// isolated worlds, for the identical reason: never let a raw EA
// response, a request header, or a session value cross out of page
// context, only the handful of fields the overlay actually renders.
import { extractAuctionsFromResponse } from "./card-detector.js";

const FC26_RE = /\/ut\/game\/fc26(\/|$)/;
const TRANSFERMARKET_RE = /\/transfermarket(\/|$|\?)/;
const EVENT_NAME = "eafc-live:transfermarket";

function log(...args) {
  if (window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

function dispatchSanitizedAuctions(url, bodyText) {
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
    log("card extraction failed in page context, dropping response:", err && err.message);
    return;
  }
  if (!auctions || auctions.length === 0) return;

  try {
    window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { url, auctions } }));
  } catch (err) {
    log("failed to dispatch sanitized market event:", err && err.message);
  }
}

(function installPageHook() {
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
                .then((text) => dispatchSanitizedAuctions(url, text))
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
            dispatchSanitizedAuctions(url, this.responseText);
          } catch (err) {
            log("XHR hook observation failed (request unaffected):", err && err.message);
          }
        });
      }
      return originalSend.apply(this, arguments);
    };
  }

  console.log("[EAFC Live] page hook installed - observing window.fetch/XHR for /ut/game/fc26 responses", {
    href: location.href,
    inIframe: window !== window.top,
  });
})();
