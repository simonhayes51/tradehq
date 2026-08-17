// src/lifecycle.js
//
// New in the userscript (no Chrome-extension equivalent - a content
// script's lifecycle is managed by the browser: it's injected once per
// matching document load and torn down with the page). A userscript
// manager gives similar per-document-load guarantees for a normal full
// navigation, but EA's Web App is a single-page app: after the initial
// load, most in-app "navigation" (login redirect settling, switching
// between EA's own screens, re-running a search, leaving and re-entering
// Transfer Market) never triggers a new top-level document load at all.
// If the userscript manager ever re-runs this script on such a
// navigation, init() must not install a second copy of the fetch/XHR
// hooks or register a second onCardDetected callback.
//
// Why this is enough, without a MutationObserver or an EA-specific
// navigation event: market-observer.js's hooks are permanent overrides
// of window.fetch/XMLHttpRequest.prototype - once installed they keep
// observing every future /transfermarket response regardless of how the
// SPA got there, and overlay.js's ensureMounted() re-attaches its own
// DOM node lazily (checking hostEl.isConnected) on the very next
// render() call if EA's SPA ever replaced <body> wholesale. So the only
// thing that actually needs "only run once" protection is init() itself.
let initialized = false;

/** Runs `setup` exactly once for the lifetime of this page, no matter
 * how many times a userscript manager (re-)fires this script. */
export function initLifecycle(setup) {
  if (initialized) return;
  initialized = true;
  setup();
}

/** Test-only: resets the module-level guard so a fresh test can call
 * initLifecycle() again as if on a new page load. */
export function __resetForTests() {
  initialized = false;
}
