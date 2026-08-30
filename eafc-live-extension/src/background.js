// src/background.js
// MV3 service worker - the ONLY place this extension makes a network
// request to the EAFC Live backend.
//
// WHY THE FETCH LIVES HERE AND NOT IN THE CONTENT SCRIPT
// -------------------------------------------------------------------
// A content script's fetch() runs with the WEB PAGE's origin for CORS
// purposes, not the extension's - host_permissions do not reliably
// exempt it the way they do for an extension page (popup/options/
// background). Doing the fetch from a content script would make success
// depend on undocumented, version-specific Chrome behaviour, and if it
// silently failed the symptom would be exactly the "clean 200 in the
// server logs, nothing reaches the page" CORS failure mode main.py's own
// ALLOWED_ORIGIN_REGEX comment describes for the main TradeHQ frontend -
// which is also why loosening backend CORS to accept the page's own
// origin was rejected as a fix here (that would weaken the backend's
// origin allowlist for every caller, not just this extension).
//
// A background service worker's fetch, by contrast, is unambiguously an
// extension-privileged request: with `https://api.eafc.live/*` in
// manifest.json's host_permissions, it is exempt from CORS entirely and
// needs no cooperation from the backend's CORS configuration at all.
// Content scripts ask this worker for intelligence via
// chrome.runtime.sendMessage (see src/eafc-api.js) instead of fetching
// directly.
//
// SECURITY: exactly like src/eafc-api.js before it, this file sends
// nothing but a numeric card id in the URL - no headers, no body, no EA
// session credential. It has no way to read anything from the EA Web
// App page (service workers have no DOM/window access at all) - the
// only input it accepts is the sourceCardId a content script asks about.
importScripts("debug-flag.js", "config.js", "intelligence-cache.js");

(function () {
  "use strict";

  const cfg = globalThis.EAFC_LIVE_CONFIG || {};
  const API_BASE_URL = cfg.API_BASE_URL || "https://api.eafc.live/api/v2";
  const REQUEST_TIMEOUT_MS = cfg.REQUEST_TIMEOUT_MS || 6000;

  function log(...args) {
    if (globalThis.EAFC_LIVE_DEBUG) console.log("[EAFC Live:bg]", ...args);
  }

  // The companion token, minted by the app at app.eafc.live where the
  // session cookie is first-party and unambiguous, and stored here rather
  // than in the page: chrome.storage.local is extension-private, so the
  // EA Web App cannot read it.
  //
  // It is short-lived by design (see app/auth/companion_token.py). When it
  // expires the overlay falls back to the anonymous payload and the
  // reconnect prompt, which is the correct outcome - not an error.
  let cachedToken = null;
  let cachedExpiry = 0;

  async function getToken() {
    // The expiry is re-checked on every read, not only on the first.
    // A service worker can outlive a token, and continuing to send an
    // expired credential is pointless traffic - the server treats it as
    // anonymous either way, so the only thing it changes is that we stop
    // noticing the token has lapsed.
    if (cachedToken) {
      if (cachedExpiry * 1000 > Date.now()) return cachedToken;
      // It WAS valid and no longer is. Drop it, so the overlay can tell
      // "this member needs to reconnect" from "this is a stranger".
      cachedToken = null;
      cachedExpiry = 0;
      try { await chrome.storage.local.remove("companionToken"); } catch { /* already gone */ }
      return "";
    }
    if (cachedToken === "") return "";
    try {
      const stored = await chrome.storage.local.get("companionToken");
      const entry = stored?.companionToken;
      if (entry && entry.token && entry.expiresAt * 1000 > Date.now()) {
        cachedToken = entry.token;
        cachedExpiry = entry.expiresAt;
      } else {
        cachedToken = "";
        cachedExpiry = 0;
        if (entry) await chrome.storage.local.remove("companionToken");
      }
    } catch {
      cachedToken = "";
      cachedExpiry = 0;
    }
    return cachedToken;
  }

  // The app posts the token here after the member connects the companion.
  chrome.runtime.onMessageExternal?.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "eafc-live:companion-token") return false;
    const { token, expiresAt } = message;
    if (typeof token !== "string" || typeof expiresAt !== "number") {
      sendResponse({ ok: false });
      return true;
    }
    chrome.storage.local.set({ companionToken: { token, expiresAt } }).then(() => {
      cachedToken = token;
      cachedExpiry = expiresAt;
      sendResponse({ ok: true });
    });
    return true;
  });

  async function fetchIntelligence(sourceCardId) {
    const token = await getToken();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const url = `${API_BASE_URL}/companion/card/${sourceCardId}`;
      const res = await fetch(url, {
        method: "GET",
        signal: controller.signal,
        // Sends the EAFC Live session cookie (never the EA session, which
        // this worker has no access to regardless). It used to be
        // `credentials: "omit"`, which meant the overlay asked for the
        // trade plan anonymously - and got it, because the endpoint was
        // ungated. Now that the endpoint enforces access, an Elite
        // member's overlay only shows their trade plan if their session
        // travels with the request.
        //
        // Requires api.eafc.live to allow this extension's origin with
        // credentials - see DEPLOY notes. Without that the request still
        // succeeds, just anonymously, and the overlay renders its locked
        // state rather than breaking.
        credentials: "include",
        // The bearer token is the RELIABLE path; the cookie above is the
        // convenient one. Both are sent: whichever the browser honours
        // wins, and the server prefers a live cookie session over a
        // token that might be stale (see get_companion_viewer).
        //
        // This matters because the cookie has to travel THIRD-PARTY -
        // this page is www.ea.com and the API is api.eafc.live - and
        // third-party cookie behaviour has changed repeatedly and is
        // changing again. A bearer header cannot be declined.
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        headers: { Accept: "application/json" },
      });

      if (res.status === 404) {
        log("no intelligence available for card", sourceCardId);
        return { status: "not_found", data: null };
      }
      if (res.status >= 500) {
        log("companion API server error", res.status, "for card", sourceCardId);
        return { status: "server_error", data: null };
      }
      if (!res.ok) {
        // Any other non-2xx (e.g. an unexpected 4xx) - treated the same
        // as a server-side problem: not a confirmed "card doesn't
        // exist", so still retryable rather than negative-cached.
        log("companion API unexpected status", res.status, "for card", sourceCardId);
        return { status: "server_error", data: null };
      }

      const data = await res.json();
      log("intelligence loaded for card", sourceCardId, data && data.signal);
      return { status: "success", data };
    } catch (err) {
      if (controller.signal.aborted) {
        log("companion API request timed out for card", sourceCardId);
        return { status: "timeout", data: null };
      }
      log("companion API request failed for card", sourceCardId, ":", err && err.message);
      return { status: "network_error", data: null };
    } finally {
      clearTimeout(timer);
    }
  }

  const cache = globalThis.EAFCLive.createIntelligenceCache(fetchIntelligence, {
    successTtlMs: cfg.SUCCESS_CACHE_TTL_MS,
    notFoundTtlMs: cfg.NOT_FOUND_CACHE_TTL_MS,
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== "EAFC_LIVE_GET_CARD_INTELLIGENCE") return false;
    cache
      .get(message.sourceCardId)
      .then(sendResponse)
      .catch(() => sendResponse({ status: "network_error", data: null }));
    return true; // keep the message channel open for the async response
  });

  log("background service worker ready");
})();
