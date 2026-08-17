// src/api-client.js
//
// Replaces eafc-live-extension/src/eafc-api.js + src/background.js.
//
// WHY GM_xmlhttpRequest INSTEAD OF window.fetch
// -------------------------------------------------------------------
// The Chrome extension's page-context code could never call the EAFC
// Live backend directly (a content script's fetch() runs under the
// PAGE's origin for CORS purposes, not the extension's) - it had to hand
// the request off to a privileged background service worker instead.
// See eafc-live-extension/src/background.js's header for the full
// explanation. A userscript has no equivalent split-context problem, but
// it has the exact same underlying CORS constraint: this file runs with
// `@inject-into page`, i.e. as the EA page's own origin, so a plain
// window.fetch() to api.eafc.live would be subject to normal CORS and
// would fail unless the backend explicitly allowed the EA page's origin
// (which we deliberately do NOT want to widen - see
// backend/main.py's ALLOWED_ORIGIN_REGEX comment on why the backend's
// CORS allowlist stays narrow).
//
// GM_xmlhttpRequest is a userscript-manager-privileged API (declared via
// `@grant GM_xmlhttpRequest` + `@connect api.eafc.live` in the metadata
// block) that is exempt from the page's CORS policy, the same way the
// Chrome extension's background worker was exempt via host_permissions -
// it is the direct userscript equivalent of that privileged request path,
// not a new mechanism.
//
// SECURITY: exactly like the Chrome extension's background.js, this file
// sends nothing but a numeric card id in the URL - no headers, no body,
// no EA session credential of any kind. GM_xmlhttpRequest requests are
// NOT subject to the page's own cookie jar by default the way page fetch
// would be, so this call cannot leak an EA session value even by
// accident.
import { CONFIG } from "./config.js";
import { createIntelligenceCache } from "./intelligence-cache.js";

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

/**
 * Resolves to a structured result - never throws, never a bare
 * intelligence object:
 *
 *   { status: "success",       data: <companion intelligence object> }
 *   { status: "not_found",     data: null }  // backend 404
 *   { status: "server_error",  data: null }  // backend 5xx / unexpected status
 *   { status: "network_error", data: null }  // unreachable
 *   { status: "timeout",       data: null }  // exceeded REQUEST_TIMEOUT_MS
 */
function fetchIntelligence(sourceCardId) {
  return new Promise((resolve) => {
    if (typeof GM_xmlhttpRequest !== "function") {
      // Userscript manager doesn't support GM_xmlhttpRequest (or the
      // @grant wasn't honoured) - fail harmlessly rather than throwing
      // into the page.
      log("GM_xmlhttpRequest is not available in this userscript manager");
      resolve({ status: "network_error", data: null });
      return;
    }

    const url = `${CONFIG.API_BASE_URL}/companion/card/${sourceCardId}`;
    let settled = false;

    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    GM_xmlhttpRequest({
      method: "GET",
      url,
      timeout: CONFIG.REQUEST_TIMEOUT_MS,
      headers: { Accept: "application/json" },
      onload(response) {
        const status = response.status;
        if (status === 404) {
          log("no intelligence available for card", sourceCardId);
          finish({ status: "not_found", data: null });
          return;
        }
        if (status >= 500 || !(status >= 200 && status < 300)) {
          log("companion API unexpected status", status, "for card", sourceCardId);
          finish({ status: "server_error", data: null });
          return;
        }
        try {
          const data = JSON.parse(response.responseText);
          log("intelligence loaded for card", sourceCardId, data && data.signal);
          finish({ status: "success", data });
        } catch (err) {
          log("companion API returned unparsable JSON for card", sourceCardId, ":", err && err.message);
          finish({ status: "server_error", data: null });
        }
      },
      onerror() {
        log("companion API request failed for card", sourceCardId);
        finish({ status: "network_error", data: null });
      },
      ontimeout() {
        log("companion API request timed out for card", sourceCardId);
        finish({ status: "timeout", data: null });
      },
      onabort() {
        finish({ status: "network_error", data: null });
      },
    });
  });
}

const cache = createIntelligenceCache(fetchIntelligence, {
  successTtlMs: CONFIG.SUCCESS_CACHE_TTL_MS,
  notFoundTtlMs: CONFIG.NOT_FOUND_CACHE_TTL_MS,
});

/** Same structured-result contract as fetchIntelligence, wrapped in the
 * success/not_found/never-cache-failures policy from intelligence-cache.js. */
export function getCardIntelligence(sourceCardId) {
  return cache.get(sourceCardId);
}
