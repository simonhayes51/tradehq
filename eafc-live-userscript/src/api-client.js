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
//
// TWO DIFFERENT API SHAPES, BOTH GRANTED
// -------------------------------------------------------------------
// Confirmed live against a real iOS session: the "Userscripts" app (and
// several other current managers) only expose the modern NAMESPACED
// `GM.xmlHttpRequest` API, not the legacy bare `GM_xmlhttpRequest`
// global this file originally called exclusively - `typeof
// GM_xmlhttpRequest !== "function"` was true there, so every request
// failed closed as "network_error" before ever reaching the network.
// resolveGMRequest() below tries the legacy global first (still the
// primary form Tampermonkey/Violentmonkey/autosbc.user.js itself uses),
// then falls back to the namespaced GM.xmlHttpRequest. Both forms are
// called with the same callback-style details object AND their return
// value is also treated as a Promise if it is one (the namespaced form
// is Promise-returning per the GM4 spec, and not every manager reliably
// invokes onload/onerror/ontimeout on it) - whichever path actually
// settles first wins, so this works regardless of which contract a
// given manager honours.
import { CONFIG } from "./config.js";
import { createIntelligenceCache } from "./intelligence-cache.js";

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

function resolveGMRequest() {
  if (typeof GM_xmlhttpRequest === "function") return GM_xmlhttpRequest;
  if (typeof GM !== "undefined" && GM && typeof GM.xmlHttpRequest === "function") {
    return GM.xmlHttpRequest.bind(GM);
  }
  return null;
}

/** Wraps one GM request in a Promise that settles from whichever of the
 * callback-style or Promise-style contract actually fires. Rejects with
 * { kind: "network_error" | "timeout" }; resolves with the response
 * object ({ status, responseText }) on completion, regardless of HTTP
 * status - HTTP-level interpretation happens in fetchIntelligence(). */
function callGM(gmRequest, details) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const settleResolve = (response) => {
      if (settled) return;
      settled = true;
      resolve(response);
    };
    const settleReject = (kind) => {
      if (settled) return;
      settled = true;
      reject({ kind });
    };

    let maybePromise;
    try {
      maybePromise = gmRequest({
        ...details,
        onload: settleResolve,
        onerror: () => settleReject("network_error"),
        ontimeout: () => settleReject("timeout"),
        onabort: () => settleReject("network_error"),
      });
    } catch (err) {
      settleReject("network_error");
      return;
    }

    if (maybePromise && typeof maybePromise.then === "function") {
      maybePromise.then(settleResolve).catch(() => settleReject("network_error"));
    }
  });
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
async function fetchIntelligence(sourceCardId) {
  const gmRequest = resolveGMRequest();
  if (!gmRequest) {
    // Neither API shape is available in this userscript manager (or the
    // @grant wasn't honoured) - fail harmlessly rather than throwing
    // into the page.
    log("no GM_xmlhttpRequest / GM.xmlHttpRequest available in this userscript manager");
    return { status: "network_error", data: null };
  }

  const url = `${CONFIG.API_BASE_URL}/companion/card/${sourceCardId}`;

  let response;
  try {
    response = await callGM(gmRequest, {
      method: "GET",
      url,
      timeout: CONFIG.REQUEST_TIMEOUT_MS,
      headers: { Accept: "application/json" },
    });
  } catch (err) {
    if (err && err.kind === "timeout") {
      log("companion API request timed out for card", sourceCardId);
      return { status: "timeout", data: null };
    }
    log("companion API request failed for card", sourceCardId);
    return { status: "network_error", data: null };
  }

  const status = response.status;
  if (status === 404) {
    log("no intelligence available for card", sourceCardId);
    return { status: "not_found", data: null };
  }
  if (status >= 500 || !(status >= 200 && status < 300)) {
    log("companion API unexpected status", status, "for card", sourceCardId);
    return { status: "server_error", data: null };
  }
  try {
    const data = JSON.parse(response.responseText);
    log("intelligence loaded for card", sourceCardId, data && data.signal);
    return { status: "success", data };
  } catch (err) {
    log("companion API returned unparsable JSON for card", sourceCardId, ":", err && err.message);
    return { status: "server_error", data: null };
  }
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
