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
//   @connect             - userscript managers require @connect for any
//                         host GM_xmlhttpRequest is allowed to reach.
//                         api.eafc.live for the JSON API, plus a wildcard
//                         for the card-art image host: that host is
//                         whatever AWS_PUBLIC_BASE_URL resolves to on the
//                         backend (an R2/CDN domain set by deploy-time
//                         config, not a fixed value this file can name -
//                         see nav-tab.js's fetchImageAsObjectUrl() for why
//                         card images are fetched through GM at all, not
//                         a plain <img src>). The wildcard only ever
//                         receives URLs that came back from OUR OWN
//                         api.eafc.live response in the first place, never
//                         attacker- or page-supplied ones, so it's not
//                         widening what this script can be made to fetch
//                         by anything untrusted.
//   @grant GM_xmlhttpRequest
//   @grant GM.xmlHttpRequest
//                       - the privileged APIs this script needs (see
//                         src/api-client.js's header for why a plain
//                         page fetch() can't reach api.eafc.live); both
//                         forms granted since managers differ on which
//                         one they actually implement (see api-client.js).
//   (no @inject-into)  - V1 forced `@inject-into page`, matching
//                         autosbc.user.js, on the theory that running in
//                         the page's own JS realm was needed for BOTH
//                         observing window.fetch/XHR reliably AND
//                         reaching GM_xmlhttpRequest. Confirmed live on
//                         a real iOS session (the "Userscripts" app):
//                         the first half held, the second did not -
//                         neither GM API was reachable from a script
//                         actually running in page context on that
//                         manager. Omitting @inject-into lets each
//                         manager use its own default context, which
//                         reliably has GM access everywhere tested; the
//                         fetch/XHR observation need is now met a
//                         different way - see page-hook.js's header for
//                         the split and why it doesn't depend on this
//                         script's own execution context at all.
//   @run-at document-start
//                       - the page-hook <script> tag (see
//                         market-observer.js) has to be injected before
//                         the Web App's own bundle makes its first
//                         /transfermarket request, or that first
//                         response is missed.
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
// @namespace    https://app.eafc.live/
// @version      1.7.1
// @description  Read-only EAFC Live trading intelligence overlay for the EA SPORTS FC 26 Web App Transfer Market. Never buys, bids, lists, or automates anything - observation only.
// @author       EAFC Live
// @match        https://www.ea.com/*/ea-sports-fc/ultimate-team/web-app/*
// @match        https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*
// @connect      api.eafc.live
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        GM.xmlHttpRequest
// @run-at       document-start
// @downloadURL  https://app.eafc.live/companion/eafc-live.user.js
// @updateURL    https://app.eafc.live/companion/eafc-live.user.js
// ==/UserScript==
`;
