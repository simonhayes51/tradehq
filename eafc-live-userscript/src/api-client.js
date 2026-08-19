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

export function resolveGMRequest() {
  if (typeof GM_xmlhttpRequest === "function") return GM_xmlhttpRequest;
  if (typeof GM !== "undefined" && GM && typeof GM.xmlHttpRequest === "function") {
    return GM.xmlHttpRequest.bind(GM);
  }
  return null;
}

/** Wraps one GM request in a Promise that settles from whichever of the
 * callback-style or Promise-style contract actually fires. Rejects with
 * { kind: "network_error" | "timeout", detail: <short diagnostic string> };
 * resolves with the response object ({ status, responseText }) on
 * completion, regardless of HTTP status - HTTP-level interpretation
 * happens in fetchIntelligence().
 *
 * `detail` exists because this is genuinely hard to debug blind: a
 * userscript running on an iPhone has no attached console without a Mac
 * (Safari's remote Web Inspector requires one), so the overlay itself
 * (see overlay.js's "error" state) surfaces this string on-screen -
 * that's the only debugging channel available on that platform. */
function callGM(gmRequest, details) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const settleResolve = (response) => {
      if (settled) return;
      settled = true;
      resolve(response);
    };
    const settleReject = (kind, detail) => {
      if (settled) return;
      settled = true;
      reject({ kind, detail });
    };

    let maybePromise;
    try {
      maybePromise = gmRequest({
        ...details,
        onload: settleResolve,
        onerror: (err) => settleReject("network_error", `onerror: ${describeGmError(err)}`),
        ontimeout: () => settleReject("timeout", "ontimeout fired"),
        onabort: () => settleReject("network_error", "onabort fired"),
      });
    } catch (err) {
      settleReject("network_error", `gmRequest() threw: ${err && err.message}`);
      return;
    }

    if (maybePromise && typeof maybePromise.then === "function") {
      maybePromise
        .then(settleResolve)
        .catch((err) => settleReject("network_error", `promise rejected: ${describeGmError(err)}`));
    }
  });
}

function describeGmError(err) {
  if (!err) return "no error info given";
  if (typeof err === "string") return err;
  if (err.error || err.message || err.statusText) {
    return String(err.error || err.message || err.statusText);
  }
  try {
    return JSON.stringify(err).slice(0, 120);
  } catch (_e) {
    return String(err);
  }
}

/**
 * Resolves to a structured result - never throws, never a bare
 * intelligence object:
 *
 *   { status: "success",       data: <companion intelligence object> }
 *   { status: "not_found",     data: null }  // backend 404
 *   { status: "server_error",  data: null, detail }  // backend 5xx / unexpected status
 *   { status: "network_error", data: null, detail }  // unreachable
 *   { status: "timeout",       data: null, detail }  // exceeded REQUEST_TIMEOUT_MS
 *
 * `detail` is a short, always-on (not debug-flag-gated) diagnostic
 * string - see callGM()'s header for why: this is the only debugging
 * channel available on an iOS userscript manager with no attached
 * console.
 */
/**
 * Shared GET implementation behind both fetchIntelligence() and
 * getOpportunities() - same structured-result contract described above,
 * generalized over the URL and what "not_found" means for it (the
 * companion endpoint has a real 404 case per-card; the opportunities
 * list endpoint does not, so callers that don't need it just never see
 * that status).
 */
async function getJson(url, label) {
  const gmRequest = resolveGMRequest();
  if (!gmRequest) {
    // Neither API shape is available in this userscript manager (or the
    // @grant wasn't honoured) - fail harmlessly rather than throwing
    // into the page.
    const detail = "no GM_xmlhttpRequest / GM.xmlHttpRequest in this manager";
    log(detail);
    return { status: "network_error", data: null, detail };
  }

  let response;
  try {
    response = await callGM(gmRequest, {
      method: "GET",
      url,
      timeout: CONFIG.REQUEST_TIMEOUT_MS,
      headers: { Accept: "application/json" },
    });
  } catch (err) {
    const detail = (err && err.detail) || "unknown request failure";
    if (err && err.kind === "timeout") {
      log(`${label} request timed out`, detail);
      return { status: "timeout", data: null, detail };
    }
    log(`${label} request failed`, detail);
    return { status: "network_error", data: null, detail };
  }

  const status = response.status;
  if (status === 0) {
    // A GM.xmlHttpRequest/XHR-style status of 0 is the conventional
    // signature of a request that never actually reached the network
    // (blocked before the fact, not a real HTTP round trip) - reported
    // distinctly from a genuine backend 5xx so the on-screen detail
    // doesn't misleadingly suggest the backend itself responded.
    const detail = `status 0 (request blocked before reaching the network - likely missing @connect approval or a manager-level permission)`;
    log(detail);
    return { status: "network_error", data: null, detail };
  }
  if (status === 404) {
    log(`${label}: not found`);
    return { status: "not_found", data: null };
  }
  if (status >= 500 || !(status >= 200 && status < 300)) {
    const detail = `HTTP ${status}`;
    log(`${label} unexpected status`, status);
    return { status: "server_error", data: null, detail };
  }
  try {
    const data = JSON.parse(response.responseText);
    log(`${label} loaded`);
    return { status: "success", data };
  } catch (err) {
    const detail = `unparsable JSON: ${err && err.message}`;
    log(`${label} returned unparsable JSON:`, err && err.message);
    return { status: "server_error", data: null, detail };
  }
}

function fetchIntelligence(sourceCardId) {
  return getJson(`${CONFIG.API_BASE_URL}/companion/card/${sourceCardId}`, `companion card ${sourceCardId}`);
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

/**
 * GET /api/v2/opportunities - a public, unauthenticated feed of cards
 * the backend already scores as a real buy/strong_buy signal (server-
 * side filtered, same contract HomeDashboard.jsx's normaliseFutgg()
 * consumes - see backend/app/routers/v2/futgg_market.py's
 * list_opportunities()). No TradeHQ login/session involved - this
 * userscript has no concept of one, same as the companion endpoint.
 * Not cached (the nav tab panel re-fetches fresh every time it's
 * opened, which is infrequent enough not to need it).
 */
export function getOpportunities(pageSize = 20) {
  return getJson(`${CONFIG.API_BASE_URL}/opportunities?page_size=${pageSize}`, "opportunities feed");
}
