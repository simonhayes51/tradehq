// src/nav-tab.js
//
// Adds a real "EAFC.LIVE" tab to EA's own bottom navigation bar
// (Home/Squads/SBC/Transfers/Store/Club), matching how third-party
// userscripts like Paletools add their own extra tabs - requested
// explicitly after the floating overlay/badges (list-badges.js,
// overlay.js) were judged too easy to miss.
//
// UNVERIFIED AGAINST EA'S REAL MARKUP - READ BEFORE CHANGING THE MATCH
// -------------------------------------------------------------------
// Same caveat as list-badges.js's findPriceAnchor(): this project has
// no access to EA's actual nav-bar DOM, only screenshots. findNavBar()
// below locates it by text content (every screenshot shows "Home",
// "Squads", and "Transfers" as sibling tab labels), not a class name we
// have any way to know. A wrong match degrades safely: if the heuristic
// can't confidently find the nav bar, this does nothing at all rather
// than inserting a broken/misplaced tab - the existing floating
// indicator (overlay.js) still works as a fallback either way.
//
// WHY A REAL FLEX CHILD, WITH EVERY TAB'S WIDTH FORCED EQUAL
// -------------------------------------------------------------------
// Three live-tested attempts got here:
//   v1.4.0 cloned an existing tab item's DOM structure to inherit EA's
//   own icon size/spacing for free. EA's bar turned out to have some
//   fixed-width rule per tab, and a real 7th flex child squeezed all
//   seven into the space budgeted for six - every icon shrank.
//   v1.5.0/v1.5.1 tried taking the tab OUT of the bar instead (absolute,
//   then fixed + body-level) to dodge that squeeze. Confirmed working
//   (tap registered, panel opened) but explicitly rejected on sight:
//   floating on top of the bar hid EA's own Club tab underneath it,
//   which is worse than being small - the request was to be embedded IN
//   the bar, not to sit on top of it.
// installNavTab() now appends a real 7th child again, but immediately
// calls equalizeTabWidths() to set `flex: 1 1 0%` inline on EVERY child
// of the bar - EA's original six AND the new one. An inline style beats
// an external stylesheet rule of equal specificity, so this overrides
// whatever fixed-width rule caused the v1.4.0 squeeze and forces a
// genuine even 1/7 split instead, for all seven tabs including EA's own.
//
// WHY THE PANEL IS OUR OWN OVERLAY, NOT REAL EA NAVIGATION
// -------------------------------------------------------------------
// This never attempts to make EA's own SPA router "navigate" to a fake
// route - that would need reverse-engineering EA's view-controller
// internals this project has no visibility into, and a mistake there
// risks actually breaking the Web App, not just mis-drawing our own UI.
// Instead, tapping the tab shows a full-screen panel we render and own
// completely (closed Shadow DOM, same as overlay.js), and tapping any
// OTHER EA tab closes it - simulating "leaving the page" without ever
// touching EA's real routing.
import { getOpportunities, resolveGMRequest } from "./api-client.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
function fmt(n) {
  return n === null || n === undefined ? "-" : NUMBER_FORMAT.format(n);
}

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
}

// Same shape as api-client.js's describeGmError() - kept as a separate
// local copy rather than importing it, since this file's failure needs
// (image fetch) and api-client.js's (JSON fetch) are otherwise unrelated.
function describeError(err) {
  if (!err) return "no error info given";
  if (typeof err === "string") return err;
  if (err.error || err.message || err.statusText) return String(err.error || err.message || err.statusText);
  try {
    return JSON.stringify(err).slice(0, 120);
  } catch (_e) {
    return String(err);
  }
}

/**
 * Live test on v1.6.4: EVERY card image request failed (0/14 loaded, 14
 * failed), not just some - the same all-or-nothing pattern api-client.js's
 * header describes for api.eafc.live JSON calls, which is exactly why
 * this project already uses GM_xmlhttpRequest instead of window.fetch()
 * for those: a plain <img src> is a request made in the EA PAGE's own
 * context, subject to whatever Content-Security-Policy that page sets -
 * almost certainly missing our image host from its img-src allowlist,
 * the same restriction a page fetch() would hit for JSON. GM requests
 * are exempt from the page's CSP the same way they're exempt from its
 * CORS policy (see api-client.js's header), so this fetches the image
 * bytes through the same privileged channel and hands the <img> a local
 * data: URI (see this function's own header further down for why data:
 * specifically, not blob:) instead of the real cross-origin one -
 * nothing about the image ever goes through the page's own network
 * stack. Resolves null (never throws) on any failure, so a genuinely
 * broken URL still falls back to the placeholder glyph exactly as
 * before.
 *
 * Uses `responseType: "arraybuffer"`, not "blob" - a first attempt at
 * this used "blob" directly and every card still rendered an empty box
 * (no visible placeholder OR image), which points at this specific
 * manager returning something that satisfies `instanceof Blob` without
 * actually being a decodable one (an empty/malformed blob support gap
 * is a known rough edge across different GM implementations). arraybuffer
 * support is more universally solid, and building the Blob manually from
 * it here means this file controls the MIME type rather than trusting
 * whatever the manager inferred - the real bug behind v1.7.3's "fetched
 * OK but the browser could not decode the response as an image": this
 * hardcoded the Blob's type to image/webp regardless of what the bytes
 * actually were. futgg_card_image_mirror.py mirrors whatever
 * Content-Type FUT.GG's own response declared (see its fetch_and_mirror
 * comment - only DEFAULTS to image/webp if that header was missing), so
 * a card mirrored from a PNG or JPEG source got its real bytes correctly
 * fetched here and then wrapped in a Blob lying about its own type.
 * WebKit/Safari (the engine every device this has failed on is running)
 * decodes a `blob:` URL by its Blob's DECLARED type, not by sniffing the
 * bytes the way a normal network response can be - so a wrong type
 * reliably fails to decode even though the bytes are perfectly valid,
 * which is exactly the symptom reported. sniffImageMimeType() below
 * reads the real magic-number signature out of the fetched bytes
 * instead of trusting either the manager or a hardcoded guess.
 *
 * Resolves `{ url, detail }` - url is null on any failure, and detail is
 * a short always-on diagnostic string for WHY (mirrors api-client.js's
 * callGM()/describeGmError() pattern, for the same reason: no console on
 * the one device this keeps failing on).
 *
 * Builds a `data:` URI, NOT `URL.createObjectURL()` - the sniffed MIME
 * type fix above didn't change the outcome (still "fetched OK but could
 * not decode", now confirmed to be the DECODE-failure path specifically,
 * meaning a Blob with a correctly-sniffed type was built and STILL
 * refused). That points one level up: EA's page CSP may simply not list
 * `blob:` in its img-src allowlist at all, which fails an <img src=
 * "blob:..."> outright regardless of what's inside it. `data:` URIs are
 * far more commonly allowlisted than `blob:` even under a strict CSP
 * (no network fetch is even conceptually possible for one, unlike a
 * blob object URL), so this encodes the bytes as base64 and hands the
 * <img> a data: URI directly instead.
 */
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const CHUNK = 0x8000; // String.fromCharCode.apply has an argument-count ceiling on some engines
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function sniffImageMimeType(buffer) {
  const b = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 12));
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return "image/png";
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (
    b.length >= 12 &&
    b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 &&
    b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50
  ) {
    return "image/webp";
  }
  if (b.length >= 6 && b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return "image/gif";
  return null;
}
function fetchImageAsDataUrl(url) {
  return new Promise((resolve) => {
    const gmRequest = resolveGMRequest();
    if (!gmRequest) {
      resolve({ url: null, detail: "no GM_xmlhttpRequest / GM.xmlHttpRequest in this manager" });
      return;
    }
    const toResult = (response) => {
      // onload fires for ANY completed request regardless of HTTP status
      // - a 403/404's error body (XML/HTML/JSON, not image bytes) still
      // arrives here as a non-empty ArrayBuffer. Missing this status
      // check was the actual bug behind "0/12 loaded, 12 failed" with no
      // reason shown: every one of these was quietly building a Blob out
      // of an error page, which the <img> then failed to DECODE - a
      // different failure path (img.onerror below) that never recorded
      // a detail string. Checked here so a bad status is reported by
      // name instead of surfacing three steps later as an opaque decode
      // failure.
      const status = response && response.status;
      if (typeof status === "number" && status !== 0 && !(status >= 200 && status < 300)) {
        return { url: null, detail: `HTTP ${status} fetching image` };
      }
      const buffer = response && response.response;
      if (!(buffer instanceof ArrayBuffer)) {
        return { url: null, detail: `response.response was not an ArrayBuffer (got ${typeof buffer}, status ${status})` };
      }
      if (buffer.byteLength === 0) return { url: null, detail: `response body was 0 bytes (status ${status})` };
      const mimeType = sniffImageMimeType(buffer);
      if (!mimeType) {
        return { url: null, detail: `fetched ${buffer.byteLength} bytes but none of PNG/JPEG/WEBP/GIF signatures matched` };
      }
      return { url: `data:${mimeType};base64,${arrayBufferToBase64(buffer)}`, detail: null };
    };
    try {
      const maybePromise = gmRequest({
        method: "GET",
        url,
        responseType: "arraybuffer",
        onload: (response) => resolve(toResult(response)),
        onerror: (err) => resolve({ url: null, detail: `onerror: ${describeError(err)}` }),
        ontimeout: () => resolve({ url: null, detail: "ontimeout fired" }),
      });
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise
          .then((response) => resolve(toResult(response)))
          .catch((err) => resolve({ url: null, detail: `promise rejected: ${describeError(err)}` }));
      }
    } catch (err) {
      resolve({ url: null, detail: `gmRequest() threw: ${describeError(err)}` });
    }
  });
}

const KNOWN_TAB_LABELS = ["home", "squads", "transfers"];

// First real device test (v1.4.0) found nothing at all - no console
// access to say why. Two plausible causes get addressed here rather
// than guessed at blind: (1) the original matcher required an EXACT,
// case-sensitive text match per child, which breaks on anything the
// screenshots couldn't show (extra whitespace, a different case, a
// label wrapped across two text nodes); (2) `document.querySelectorAll`
// never descends into a shadow root, open or closed - if EA's nav bar
// is built as a web component (plausible for a component this old and
// this reused), the bar would be invisible to the original "body *"
// query even though it's visibly on screen. allElements() below walks
// open shadow roots too; a closed shadow root is a real dead end (same
// limitation this project's OWN panels rely on to stay unreachable by
// EA's page) and is recorded in the diagnostic rather than silently
// skipped.
function* allElements(root) {
  for (const el of root.querySelectorAll("*")) {
    yield el;
    if (el.shadowRoot) yield* allElements(el.shadowRoot);
  }
}

function normalizedText(el) {
  return (el.textContent || "").trim().toLowerCase();
}

// Populated by findNavBar() on every call (success or failure) so a
// caller with no console can still learn something concrete - see
// getNavTabDiagnostic() below.
let diagnostic = "not run yet";

/** Finds EA's bottom nav bar by locating a container whose direct
 * children collectively include text matching every label in
 * KNOWN_TAB_LABELS (case-insensitive, whitespace-trimmed) - the common
 * ancestor of "Home"/"Squads"/"Transfers" as sibling elements. Also
 * descends into open shadow roots (see allElements() above). Returns
 * null (never throws, never guesses a wrong container) if that pattern
 * isn't found anywhere reachable. */
function findNavBar() {
  let scanned = 0;
  let bestMatch = null;
  let bestMatchCount = 0;

  for (const el of allElements(document.documentElement)) {
    scanned++;
    if (el.children.length < KNOWN_TAB_LABELS.length) continue;
    const childTexts = Array.from(el.children).map(normalizedText);
    const matched = KNOWN_TAB_LABELS.filter((label) => childTexts.some((t) => t === label));
    if (matched.length === KNOWN_TAB_LABELS.length) {
      diagnostic = `found (scanned ${scanned} elements)`;
      return el;
    }
    if (matched.length > bestMatchCount) {
      bestMatchCount = matched.length;
      bestMatch = childTexts.filter((t) => t).slice(0, 6);
    }
  }

  diagnostic =
    bestMatchCount > 0
      ? `not found (scanned ${scanned}; closest had ${bestMatchCount}/${KNOWN_TAB_LABELS.length} labels: [${bestMatch.join(", ")}])`
      : `not found (scanned ${scanned}; no candidate matched any label - possibly inside a closed shadow root)`;
  return null;
}

/** Short diagnostic string for the last findNavBar() attempt - surfaced
 * in overlay.js since there is no console access on the one platform
 * (iOS) this has actually failed on. */
export function getNavTabDiagnostic() {
  return diagnostic;
}

let injected = false;
let panelHostEl = null;
let panelShadow = null;
let tabEl = null;
let cachedNavBar = null;

// Colors/radius/fonts below are lifted directly from the real site's own
// design tokens (frontend/src/v2/styles/tokens.css) and its opportunities/
// trade-finder CSS, so the panel reads as EAFC.Live rather than a
// different-looking bolt-on. Notably: the real site uses the IDENTICAL
// green for both "buy" and "strong_buy" signals (see trade-finder.css's
// .finder-signal-pill.signal-buy,.signal-strong_buy rule) - there is no
// separate blue "strong" treatment on the site, so the blue accent this
// panel used to have for "strong" is dropped entirely rather than kept.
const PANEL_STYLES = `
  :host { all: initial; }
  .eafc-live-full-panel {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    /* Bottom is set inline per-open (see openPanel()) to the real
       measured height of EA's nav bar, not 0 - a full inset:0 panel
       live-tested as covering the bar entirely, hiding this project's
       own tab along with EA's, leaving only the in-panel close button
       as a way back. Paletools' own panel (the explicit reference for
       this feature) keeps the bar visible underneath its content for
       exactly this reason. Falls back to a fixed guess if the bar
       can't be measured, rather than 0. */
    bottom: 64px;
    z-index: 2147483647;
    background: #08090a;
    color: #f3f3f2;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
  }
  .eafc-live-full-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: max(14px, env(safe-area-inset-top)) 16px 14px;
    border-bottom: 1px solid #222327;
    background: #111214;
  }
  .eafc-live-full-title { font-weight: 700; letter-spacing: 0.04em; font-size: 13px; color: #39ff8a; }
  .eafc-live-full-close {
    cursor: pointer; color: #8d8f95; font-size: 20px; line-height: 1;
    min-width: 36px; min-height: 36px; display: flex; align-items: center; justify-content: center;
    border-radius: 4px;
  }
  .eafc-live-full-close:active { background: rgba(255,255,255,0.06); }
  .eafc-live-full-body { flex: 1; overflow-y: auto; padding: 12px 16px max(16px, env(safe-area-inset-bottom)); }
  .eafc-live-opp-card {
    display: flex; gap: 14px; align-items: center;
    background: #141414; border: 1px solid #2c2c2c; border-left: 3px solid #39ff8a;
    border-radius: 4px; padding: 12px; margin-bottom: 10px;
    animation: eafc-live-card-in 0.25s ease-out both;
  }
  @keyframes eafc-live-card-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  /* Card art is portrait (FUT card proportions, not square) - a 54x54
     square box squeezed it down further than it needed to be and left
     a lot of empty padding around the actual card shape. Sized to the
     real aspect ratio instead so it reads as a recognizable card, not
     a thumbnail. */
  .eafc-live-opp-art {
    flex: 0 0 auto; width: 68px; height: 92px; object-fit: contain;
    background: rgba(255,255,255,0.03); border-radius: 4px;
  }
  .eafc-live-opp-art-placeholder {
    flex: 0 0 auto; width: 68px; height: 92px; border-radius: 4px;
    background: rgba(255,255,255,0.04); display: flex; align-items: center;
    justify-content: center; font-size: 22px; color: #5c5e64;
  }
  .eafc-live-opp-main { flex: 1; min-width: 0; }
  .eafc-live-opp-name {
    font-weight: 700; font-size: 14px; line-height: 1.25; margin-bottom: 6px;
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
  }
  .eafc-live-opp-row { display: flex; justify-content: space-between; font-size: 12.5px; color: #a2a2a2; padding: 2px 0; }
  .eafc-live-opp-row .label { color: #868686; }
  .eafc-live-opp-row .value { font-variant-numeric: tabular-nums; color: #f3f3f2; }
  .eafc-live-opp-row .value.profit { color: #39ff8a; font-weight: 600; }
  .eafc-live-opp-signal {
    display: inline-block; margin-top: 7px; padding: 4px 9px; border-radius: 4px;
    font-size: 10.5px; font-weight: 700; letter-spacing: 0.02em;
    background: rgba(57, 255, 138, 0.14); color: #39ff8a;
  }
  .eafc-live-opp-hint { color: #8d8f95; font-size: 11px; line-height: 1.35; margin-top: 4px; }
  .eafc-live-muted { color: #8d8f95; font-size: 12px; text-align: center; padding: 24px 0; }
  .eafc-live-debug { color: #5c5e64; font-size: 10px; text-align: center; font-family: 'JetBrains Mono', monospace; padding: 0 16px 16px; word-break: break-word; }
  .eafc-live-spinner {
    width: 22px; height: 22px; margin: 24px auto 10px;
    border: 2px solid #222327; border-top-color: #39ff8a; border-radius: 50%;
    animation: eafc-live-spin 0.7s linear infinite;
  }
  @keyframes eafc-live-spin { to { transform: rotate(360deg); } }
`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function ensurePanelMounted() {
  if (panelHostEl && panelHostEl.isConnected) return panelShadow;
  panelHostEl = document.createElement("div");
  panelHostEl.id = "eafc-live-nav-panel-root";
  panelShadow = panelHostEl.attachShadow({ mode: "closed" });
  const styleEl = document.createElement("style");
  styleEl.textContent = PANEL_STYLES;
  panelShadow.appendChild(styleEl);
  (document.body || document.documentElement).appendChild(panelHostEl);
  return panelShadow;
}

function closePanel() {
  if (!panelShadow) return;
  const existing = panelShadow.querySelector(".eafc-live-full-panel");
  if (existing) existing.remove();
  if (tabEl) tabEl.classList.remove("eafc-live-tab-active");
}

async function openPanel() {
  const shadow = ensurePanelMounted();
  const existing = shadow.querySelector(".eafc-live-full-panel");
  if (existing) existing.remove();

  const panel = el("div", "eafc-live-full-panel");
  // Real measured bar height beats the CSS fallback whenever it's
  // available - see PANEL_STYLES's comment on why this leaves a gap at
  // all rather than covering the full viewport.
  if (cachedNavBar) {
    const barHeight = cachedNavBar.getBoundingClientRect().height;
    if (barHeight > 0) panel.style.bottom = `${Math.round(barHeight)}px`;
  }
  const header = el("div", "eafc-live-full-header");
  header.appendChild(el("span", "eafc-live-full-title", "EAFC.LIVE OPPORTUNITIES"));
  const closeBtn = el("span", "eafc-live-full-close", "×");
  closeBtn.addEventListener("click", closePanel);
  header.appendChild(closeBtn);
  panel.appendChild(header);

  const body = el("div", "eafc-live-full-body");
  body.appendChild(el("div", "eafc-live-spinner"));
  body.appendChild(el("div", "eafc-live-muted", "Loading live opportunities…"));
  panel.appendChild(body);
  shadow.appendChild(panel);
  if (tabEl) tabEl.classList.add("eafc-live-tab-active");

  const result = await getOpportunities(20);
  body.innerHTML = "";

  if (result.status !== "success") {
    body.appendChild(el("div", "eafc-live-muted", "Could not load opportunities right now."));
    return;
  }

  const items = Array.isArray(result.data) ? result.data : result.data?.items || [];
  if (items.length === 0) {
    body.appendChild(el("div", "eafc-live-muted", "No live buy signals right now."));
    return;
  }

  // Tallies how art actually resolved, for the always-on footer line
  // below - the only way to tell "backend never gave us a URL for these
  // cards" from "the URL was given but the image request itself failed
  // (e.g. EA's page CSP blocking a cross-origin <img> load)" without any
  // console access on the device this has to be diagnosed from.
  const artStats = { withUrl: 0, loaded: 0, failed: 0, total: items.length, firstFailureDetail: null };

  items.forEach((item, index) => {
    const card = el("div", "eafc-live-opp-card");
    card.style.animationDelay = `${Math.min(index, 10) * 30}ms`;

    // composited_image_url is FUT.GG's own pre-composited card render
    // (background + cutout + rating/stats already baked in) mirrored to
    // our storage - see futgg_market.py's _serialize_player() comment on
    // why this field, specifically, is safe to drop straight into an
    // <img> with nothing drawn on top of it. Always reserves the art
    // slot (image OR placeholder) rather than collapsing the row when a
    // URL fails to load, so a broken image can never silently eat the
    // whole art column - see artStats above for surfacing WHY it failed.
    const artUrl = item.composited_image_url || item.image_url;
    const artPlaceholder = el("div", "eafc-live-opp-art-placeholder", "⚽");
    card.appendChild(artPlaceholder);
    if (artUrl) {
      artStats.withUrl++;
      // Fetched as a blob through the privileged GM channel, not a
      // direct <img src> - see fetchImageAsDataUrl()'s header for why
      // a plain cross-origin <img> tag failed for every single card on
      // the last live test (EA's page CSP, the same restriction this
      // project already routes JSON around).
      fetchImageAsDataUrl(artUrl).then(({ url: dataUrl, detail }) => {
        if (!dataUrl) {
          artStats.failed++;
          if (detail && !artStats.firstFailureDetail) artStats.firstFailureDetail = detail;
          return;
        }
        const img = document.createElement("img");
        img.className = "eafc-live-opp-art";
        img.src = dataUrl;
        img.alt = "";
        img.onload = () => artStats.loaded++;
        img.onerror = () => {
          // A correctly-typed data: URI failing to decode would mean the
          // fetched bytes themselves are corrupt, not a labeling problem
          // - still degrades to the placeholder rather than a blank box.
          artStats.failed++;
          if (!artStats.firstFailureDetail) {
            artStats.firstFailureDetail = "fetched OK, correct MIME type, but the browser still could not decode it";
          }
          img.replaceWith(el("div", "eafc-live-opp-art-placeholder", "⚽"));
        };
        artPlaceholder.replaceWith(img);
      });
    }

    const main = el("div", "eafc-live-opp-main");
    main.appendChild(el("div", "eafc-live-opp-name", [item.name, item.rarity].filter(Boolean).join(" — ")));
    const row = (label, value, valueClass) => {
      const r = el("div", "eafc-live-opp-row");
      r.appendChild(el("span", "label", label));
      r.appendChild(el("span", `value${valueClass ? " " + valueClass : ""}`, value));
      main.appendChild(r);
    };
    // A signed-out/non-Elite viewer never gets current_bin/recommended_buy_max
    // at all - the backend redacts those to current_bin_approx (rounded to
    // the nearest 1,000) for exactly this endpoint (see
    // backend/app/auth/access.py's preview_opportunity()). The userscript
    // has no way to carry an Elite member's site session into EA's page
    // context, so it is always this "locked" viewer, same as an anonymous
    // site visitor - showing a bare "-" here read as broken rather than as
    // the paywall doing its job.
    if (item.current_bin != null) {
      row("Current BIN", fmt(item.current_bin));
    } else if (item.current_bin_approx != null) {
      row("Current BIN", `~${fmt(item.current_bin_approx)}`);
    }
    const buyTarget = item.recommended_buy_max ?? item.buy_below;
    if (buyTarget != null) {
      row("Buy Below", fmt(buyTarget));
    }
    if (item.expected_profit_after_tax != null) {
      row("Expected Profit", `+${fmt(Math.round(item.expected_profit_after_tax))}`, "profit");
    } else if (item.locked) {
      main.appendChild(
        el("div", "eafc-live-opp-hint", item.headline_reason || "Buy target unlocks with Elite on the site.")
      );
    }
    const signal = el(
      "span",
      "eafc-live-opp-signal",
      String(item.signal || "buy").replace(/_/g, " ").toUpperCase()
    );
    main.appendChild(signal);
    card.appendChild(main);
    body.appendChild(card);
  });

  // Image load/error events settle asynchronously after this function
  // returns - a fixed delay is imprecise but good enough to answer the
  // one question this exists for (see artStats above): is a missing
  // card image a "backend gave no URL" gap or an "image request itself
  // failed" gap. Only shown at all when at least one card had a URL to
  // try, and only when something is actually off (a fully healthy page
  // says nothing, same as the nav tab diagnostic's "found" case staying
  // out of the way).
  setTimeout(() => {
    // Two distinct gaps this tells apart, since "art didn't render" was
    // reported live with zero images AND zero loaded/failed - which
    // only happens when the backend didn't send a URL for any of these
    // cards at all (a data gap), not a request/CSP failure (that would
    // show up as failed > 0 with withUrl > 0, the case already handled
    // above the "no URL" line).
    if (artStats.withUrl === 0 && artStats.total > 0) {
      body.appendChild(
        el("div", "eafc-live-muted", `Card art: none of these ${artStats.total} cards had an image URL from the backend.`)
      );
    } else if (artStats.failed > 0) {
      body.appendChild(
        el(
          "div",
          "eafc-live-muted",
          `Card art: ${artStats.loaded}/${artStats.withUrl} loaded, ${artStats.failed} failed to load.`
        )
      );
      if (artStats.firstFailureDetail) {
        body.appendChild(el("div", "eafc-live-debug", artStats.firstFailureDetail));
      }
    }
  }, 4500);
}

const TAB_CSS = `
  .eafc-live-nav-tab {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-width: 0;
    color: #39ff8a;
    cursor: pointer;
    /* EA's own tabs are presumably real interactive elements (<button>/
       <a>) - v1.4.0's clone worked because it inherited whatever made
       those tappable. This is a plain <div> built from scratch instead
       (needed to control its content directly - see the header comment
       on why cloning broke sizing), so it can land inside a container
       that sets pointer-events: none broadly and only re-enables it on
       real interactive tags. Forced back on here, on both the tab and
       its children, rather than assuming it's inherited correctly. */
    pointer-events: auto;
  }
  .eafc-live-nav-tab .eafc-live-nav-tab-icon { font-size: 18px; line-height: 1; pointer-events: none; }
  .eafc-live-nav-tab .eafc-live-nav-tab-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.02em;
    white-space: nowrap;
    pointer-events: none;
  }
  .eafc-live-nav-tab.eafc-live-tab-active { color: #fff; text-shadow: 0 0 8px rgba(57, 255, 138, 0.8); }
  .eafc-live-nav-tab:active { opacity: 0.7; }
`;

/** Forces every direct child of the bar (EA's own six tabs AND ours) to
 * share EQUAL width via inline `flex` - the actual fix for what broke
 * in v1.4.0. That attempt appended a real 7th flex child and let
 * whatever sizing rule EA's CSS already had (turned out to be a fixed
 * per-tab width, not flex-grow) squeeze all seven into the space
 * budgeted for six. Inline styles win over an external stylesheet's
 * rule of the same specificity, so explicitly setting `flex: 1 1 0%` on
 * every child - not just the new one - overrides that fixed-width rule
 * for the whole bar and forces a genuine even 1/7 split instead of
 * whatever the shrink algorithm was doing before. min-width: 0 keeps
 * flexbox from refusing to shrink a child below its content's natural
 * width, which is what over-wide labels/icons would otherwise fight. */
function equalizeTabWidths(navBar) {
  for (const child of navBar.children) {
    child.style.flex = "1 1 0%";
    child.style.minWidth = "0";
  }
}

/** Best-effort: injects the "EAFC.LIVE" tab as a genuine child of EA's
 * own nav bar once it can be found. Safe to call repeatedly (module-
 * level `injected` guard) - does nothing at all if the heuristic can't
 * confidently locate the nav bar, rather than inserting a broken tab. */
export function installNavTab() {
  if (injected) return true;
  const navBar = findNavBar();
  if (!navBar) return false; // will be retried by main.js's pollForNavTab()
  injected = true;
  cachedNavBar = navBar;

  const tab = document.createElement("div");
  tab.className = "eafc-live-nav-tab";
  tab.setAttribute("role", "button");
  tab.setAttribute("aria-label", "EAFC.Live opportunities");
  // Inline, not just the class rule below - inline style specificity
  // beats ANY selector-based CSS rule (short of !important), where the
  // class rule can still lose to something like a `.nav-bar > div`
  // child-combinator rule of EA's own that happens to be more specific
  // than a lone class selector. Belt and braces against a v1.6.1 fix
  // that clearly didn't hold on the device this keeps failing on.
  tab.style.pointerEvents = "auto";
  const icon = el("span", "eafc-live-nav-tab-icon", "⚡");
  const label = el("span", "eafc-live-nav-tab-label", "LIVE");
  tab.appendChild(icon);
  tab.appendChild(label);

  const styleEl = document.createElement("style");
  styleEl.textContent = TAB_CSS;
  document.head.appendChild(styleEl);

  let open = false;
  let lastToggleAt = 0;
  const toggle = () => {
    // touchend AND the browser's own synthesized click can both fire
    // for one physical tap - this collapses them into a single toggle
    // rather than open-then-immediately-close.
    const now = Date.now();
    if (now - lastToggleAt < 400) return;
    lastToggleAt = now;
    open = !open;
    if (open) openPanel();
    else closePanel();
  };
  // v1.6.1 already tried forcing pointer-events via a CSS class rule and
  // it made no difference on the device this was tested on - `click`
  // itself may simply never be reaching this element (some app shells
  // that hand-roll their own touch handling call preventDefault() on
  // touchend for elements THEY recognize, which can suppress the
  // browser's synthesized click for the whole interaction). touchend is
  // handled directly, independently of whether click ever arrives.
  tab.addEventListener(
    "touchend",
    (event) => {
      event.stopPropagation();
      event.preventDefault();
      toggle();
    },
    { passive: false }
  );
  tab.addEventListener("click", (event) => {
    event.stopPropagation();
    event.preventDefault();
    toggle();
  });

  // Any tap OUTSIDE our own tab/panel means the user is genuinely
  // navigating away in EA's own app - close our panel rather than
  // leaving it stuck on top of whatever page they went to.
  //
  // Was scoped to navBar's own "click" event. Live test: tapping
  // Transfers left the panel open. The same root cause the tab's own
  // tap handling already hit (see the touchend comment above this tab's
  // click listener) explains it - if this app suppresses the browser's
  // synthesized click broadly, a listener that ONLY hears "click" would
  // never fire for taps on EA's other tabs either, exactly matching
  // what was reported. Listens on document (not just navBar - a tap
  // could land on a route's own content, not only the bar) for both
  // "touchend" and "click", capture phase so it observes the tap before
  // any of EA's own handlers get a chance to stop it from bubbling.
  const maybeCloseOnOutsideTap = (event) => {
    if (event.target === tab || tab.contains(event.target)) return;
    if (panelHostEl && event.target === panelHostEl) return; // tap inside our own panel
    if (open) {
      open = false;
      closePanel();
    }
  };
  document.addEventListener("touchend", maybeCloseOnOutsideTap, { capture: true });
  document.addEventListener("click", maybeCloseOnOutsideTap, { capture: true });

  tabEl = tab;
  navBar.appendChild(tab);
  equalizeTabWidths(navBar);

  // EA's own SPA re-renders this bar on every route change (Home ->
  // Squads -> ...), and its reconciler only knows about the six tabs
  // IT put there - a foreign 7th node it doesn't track can end up
  // reordered arbitrarily as it reinserts its own children around ours.
  // Confirmed live: the tab's position drifted between screens even
  // though appendChild only ever runs once, here, at install. A
  // MutationObserver on navBar's childList re-pins it to the end
  // whenever anything changes the bar's children - skipped when it's
  // already last, both to avoid fighting a mutation we caused ourselves
  // and to avoid an observer->mutate->observer loop.
  const keepTabPinned = new MutationObserver(() => {
    // appendChild() moves an already-attached node rather than erroring,
    // so this re-pins it whether EA's reconciler merely reordered it or
    // detached it from the bar outright.
    if (navBar.lastElementChild !== tab) {
      navBar.appendChild(tab);
      equalizeTabWidths(navBar);
    }
  });
  keepTabPinned.observe(navBar, { childList: true });

  log("EAFC.LIVE nav tab installed");
  return true;
}
