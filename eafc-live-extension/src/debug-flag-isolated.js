// src/debug-flag-isolated.js
// ISOLATED-world-only counterpart to debug-flag.js - identical content,
// separate file. See card-grouping.js's header for why: debug-flag.js
// is also referenced by the MAIN-world content_scripts entry (and by
// background.js via importScripts, a different loading path entirely
// and unaffected by this), and a file referenced from two
// content_scripts entries for the SAME page was confirmed live to only
// ever execute once - Chrome silently skips the second injection, even
// across a different declared "world". Kept in sync manually with
// debug-flag.js; both are one line.
globalThis.EAFC_LIVE_DEBUG = true;
