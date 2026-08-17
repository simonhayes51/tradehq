// src/metadata.js
//
// The `==UserScript==` header, kept as data (not a template literal in
// build.mjs) so it's easy to read/diff on its own. build.mjs prepends
// this verbatim to the top of the esbuild output.
//
// Directive choices, and why - each one checked against what
// autosbc.user.js (a real, currently-shipping userscript targeting the
// same EA Web App) actually uses, per the "investigate rather than
// assume" instruction:
//
//   @match (x2)        - EA's Web App URL includes a locale segment for
//                         some regions/accounts (e.g. /fr-fr/ea-sports-
//                         fc/...) and not for others - confirmed both
//                         forms are real by autosbc.user.js itself
//                         shipping both @match lines, not just the
//                         locale-prefixed one.
//   @connect            - userscript managers require @connect for any
//                         host GM_xmlhttpRequest is allowed to reach;
//                         only api.eafc.live is listed since that's the
//                         only cross-origin host this script ever calls.
//   @grant GM_xmlhttpRequest
//                       - the one privileged API this script needs (see
//                         src/api-client.js's header for why a plain
//                         page fetch() can't reach api.eafc.live).
//   @inject-into page   - runs in the page's own JS realm rather than a
//                         sandboxed content-script-like context. Chosen
//                         because (a) autosbc.user.js itself uses it
//                         against the same page, so it's confirmed
//                         compatible with the managers that matter here,
//                         and (b) it's what makes the single-execution-
//                         context simplification in market-observer.js
//                         possible at all - a sandboxed context runs
//                         against a separate, wrapped window.fetch that
//                         may not see the page's real network activity
//                         the same way.
//   @downloadURL/@updateURL
//                       - point at the same hosted URL so a userscript
//                         manager can detect and offer future updates
//                         automatically, without the user re-downloading
//                         by hand. Both are required (not just one) for
//                         Tampermonkey/Violentmonkey/Userscripts to
//                         reliably treat this as an auto-updating script
//                         rather than a one-off local file.
export const METADATA_BLOCK = `// ==UserScript==
// @name         EAFC Live Companion
// @namespace    https://eafc.live/
// @version      1.0.0
// @description  Read-only EAFC Live trading intelligence overlay for the EA SPORTS FC 26 Web App Transfer Market. Never buys, bids, lists, or automates anything - observation only.
// @author       EAFC Live
// @match        https://www.ea.com/*/ea-sports-fc/ultimate-team/web-app/*
// @match        https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*
// @connect      api.eafc.live
// @grant        GM_xmlhttpRequest
// @inject-into  page
// @run-at       document-start
// @downloadURL  https://eafc.live/companion/eafc-live.user.js
// @updateURL    https://eafc.live/companion/eafc-live.user.js
// ==/UserScript==
`;
