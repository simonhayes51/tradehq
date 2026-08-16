// src/debug-flag.js
//
// Single source of truth for the extension's debug-logging toggle.
// Loaded first in every execution context that needs it - the page's
// MAIN world, the content script's ISOLATED world, and the background
// service worker (via importScripts, see src/background.js).
//
// Those three contexts do NOT share a JavaScript heap (that is exactly
// why page-bridge.js/ea-session.js talk to the isolated world via
// CustomEvent rather than a shared object - see market-observer.js), so
// a `window.EAFC_LIVE_CONFIG` set in one is invisible in the others.
// This file fixes that by being the one thing every context loads
// independently and by using `globalThis` (valid in a page, a content
// script, AND a service worker with no `window`) instead of `window`.
//
// Flip to `false` once live-testing is done - `true` here is temporary,
// for the first end-to-end pass against a real EA session (still V1,
// pre-release; there is no shipped build depending on this defaulting
// off yet). This never gates the "no EA session credential is ever
// logged" rule - that holds regardless of this flag's value.
globalThis.EAFC_LIVE_DEBUG = true;
