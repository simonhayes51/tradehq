// src/ea-session.js
// Runs in the page's MAIN world (see manifest.json), loaded before
// page-bridge.js.
//
// Tracks two harmless, non-secret facts purely by watching which
// requests the Web App itself makes:
//   1. which /ut/game/fc26 host the app is currently talking to - EA
//      rotates/selects this per-session (e.g. different utas.*.fut.ea.com
//      edges), so hardcoding one is fragile; observing it from the app's
//      own traffic is not.
//   2. whether we appear to be authenticated at all - inferred only from
//      "did a /ut/game/fc26 request come back with a 2xx", never by
//      reading any auth header or cookie value.
//
// SECURITY: this module NEVER reads, stores, or exposes X-UT-SID,
// X-UT-PHISHING-TOKEN, cookies, or any other EA credential. It looks
// only at request URLs and response status codes. Nothing it holds is
// ever sent anywhere - page-bridge.js reads `apiHost`/`authenticated`
// off `window.__eafcLiveSession` locally, in the same page, and neither
// value leaves the browser.
(function () {
  "use strict";

  const state = {
    apiHost: null,
    authenticated: false,
    lastObservedAt: null,
  };

  const FC26_PATH_RE = /\/ut\/game\/fc26(\/|$)/;

  function noteRequest(urlString, status) {
    let parsed;
    try {
      parsed = new URL(urlString, window.location.href);
    } catch (_err) {
      return;
    }
    if (!FC26_PATH_RE.test(parsed.pathname)) return;

    state.apiHost = parsed.origin;
    state.lastObservedAt = Date.now();
    if (typeof status === "number" && status >= 200 && status < 300) {
      state.authenticated = true;
    }
  }

  window.__eafcLiveSession = {
    get apiHost() {
      return state.apiHost;
    },
    get authenticated() {
      return state.authenticated;
    },
    get lastObservedAt() {
      return state.lastObservedAt;
    },
    _noteRequest: noteRequest,
  };
})();
