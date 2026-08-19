// src/nav-tab.js
//
// Adds a real "EAFC LIVE" tab to EA's own bottom navigation bar
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
// WHY A FLOATING, BODY-LEVEL BADGE - NOT A CLONED FLEX SIBLING, NOT AN
// ABSOLUTE CHILD OF THE BAR EITHER
// -------------------------------------------------------------------
// Two live-tested failures got here in order:
//   v1.4.0 cloned an existing tab item's DOM structure to inherit EA's
//   own icon size/spacing for free. EA's bar turned out to allot FIXED
//   width per tab (not flex-grow that reflows), so a real 7th child
//   squeezed all seven into the space budgeted for six - every icon
//   shrank, including EA's own.
//   v1.5.0 fixed that by taking the badge out of the flex flow with
//   `position: absolute` - but still nested inside navBar. That broke
//   two different ways on the next test: it rendered nowhere near the
//   bar's visible box (the matched container's true bounding box isn't
//   what the on-screen tab strip looks like), and taps didn't register
//   (an absolutely-positioned descendant is confined to ITS OWN
//   ancestor's stacking context regardless of z-index - a sibling page
//   region painted later can still sit above it).
// installNavTab() now appends the badge directly to <body> as
// `position: fixed`, exactly like this file's own openPanel() panel and
// overlay.js's panel already do - measured from the viewport, not an
// unpredictable ancestor box, and always its own top-level stacking
// context. findNavBar()'s match is still used, just only to read
// getBoundingClientRect() off of - to place the badge next to the real
// bar's on-screen position - not to host it.
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
import { getOpportunities } from "./api-client.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
function fmt(n) {
  return n === null || n === undefined ? "-" : NUMBER_FORMAT.format(n);
}

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
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

const PANEL_STYLES = `
  :host { all: initial; }
  .eafc-live-full-panel {
    position: fixed;
    inset: 0;
    z-index: 2147483647;
    background: #0b0e14;
    color: #e8ecf1;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
  }
  .eafc-live-full-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: max(14px, env(safe-area-inset-top)) 16px 14px;
    border-bottom: 1px solid #2c3542;
    background: #14181f;
  }
  .eafc-live-full-title { font-weight: 700; letter-spacing: 0.04em; font-size: 13px; color: #7fd8ff; }
  .eafc-live-full-close {
    cursor: pointer; color: #8a94a6; font-size: 20px; line-height: 1;
    min-width: 36px; min-height: 36px; display: flex; align-items: center; justify-content: center;
    border-radius: 8px;
  }
  .eafc-live-full-close:active { background: rgba(255,255,255,0.06); }
  .eafc-live-full-body { flex: 1; overflow-y: auto; padding: 12px 16px max(16px, env(safe-area-inset-bottom)); }
  .eafc-live-opp-card {
    display: flex; gap: 12px; align-items: center;
    background: #14181f; border: 1px solid #2c3542; border-left: 3px solid #5fd98a;
    border-radius: 10px; padding: 10px 12px; margin-bottom: 10px;
    animation: eafc-live-card-in 0.25s ease-out both;
  }
  .eafc-live-opp-card.strong { border-left-color: #7fd8ff; }
  @keyframes eafc-live-card-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  .eafc-live-opp-art {
    flex: 0 0 auto; width: 54px; height: 54px; object-fit: contain;
    background: rgba(255,255,255,0.03); border-radius: 6px;
  }
  .eafc-live-opp-art-placeholder {
    flex: 0 0 auto; width: 54px; height: 54px; border-radius: 6px;
    background: rgba(255,255,255,0.04); display: flex; align-items: center;
    justify-content: center; font-size: 20px; color: #3a4453;
  }
  .eafc-live-opp-main { flex: 1; min-width: 0; }
  .eafc-live-opp-name {
    font-weight: 700; font-size: 13px; margin-bottom: 4px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .eafc-live-opp-row { display: flex; justify-content: space-between; font-size: 11.5px; color: #b7c0cf; padding: 1px 0; }
  .eafc-live-opp-row .label { color: #8a94a6; }
  .eafc-live-opp-row .value { font-variant-numeric: tabular-nums; color: #e8ecf1; }
  .eafc-live-opp-row .value.profit { color: #5fd98a; font-weight: 600; }
  .eafc-live-opp-signal {
    display: inline-block; margin-top: 5px; padding: 3px 8px; border-radius: 5px;
    font-size: 10px; font-weight: 700; letter-spacing: 0.02em;
    background: rgba(95, 217, 138, 0.15); color: #5fd98a;
  }
  .eafc-live-opp-signal.strong { background: rgba(127, 216, 255, 0.15); color: #7fd8ff; }
  .eafc-live-muted { color: #6b7484; font-size: 12px; text-align: center; padding: 24px 0; }
  .eafc-live-spinner {
    width: 22px; height: 22px; margin: 24px auto 10px;
    border: 2px solid #2c3542; border-top-color: #7fd8ff; border-radius: 50%;
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
  const header = el("div", "eafc-live-full-header");
  header.appendChild(el("span", "eafc-live-full-title", "EAFC LIVE OPPORTUNITIES"));
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

  items.forEach((item, index) => {
    const isStrong = item.signal === "strong_buy";
    const card = el("div", `eafc-live-opp-card${isStrong ? " strong" : ""}`);
    card.style.animationDelay = `${Math.min(index, 10) * 30}ms`;

    // composited_image_url is FUT.GG's own pre-composited card render
    // (background + cutout + rating/stats already baked in) mirrored to
    // our storage - see futgg_market.py's _serialize_player() comment on
    // why this field, specifically, is safe to drop straight into an
    // <img> with nothing drawn on top of it. Falls back to a plain
    // placeholder glyph rather than a broken image icon if neither this
    // nor the cutout-only image_url is present for a card.
    const artUrl = item.composited_image_url || item.image_url;
    if (artUrl) {
      const img = document.createElement("img");
      img.className = "eafc-live-opp-art";
      img.src = artUrl;
      img.loading = "lazy";
      img.alt = "";
      img.onerror = () => img.remove();
      card.appendChild(img);
    } else {
      card.appendChild(el("div", "eafc-live-opp-art-placeholder", "⚽"));
    }

    const main = el("div", "eafc-live-opp-main");
    main.appendChild(el("div", "eafc-live-opp-name", [item.name, item.rarity].filter(Boolean).join(" — ")));
    const row = (label, value, valueClass) => {
      const r = el("div", "eafc-live-opp-row");
      r.appendChild(el("span", "label", label));
      r.appendChild(el("span", `value${valueClass ? " " + valueClass : ""}`, value));
      main.appendChild(r);
    };
    row("Current BIN", fmt(item.current_bin));
    row("Buy Below", fmt(item.recommended_buy_max ?? item.buy_below));
    if (item.expected_profit_after_tax != null) {
      row("Expected Profit", `+${fmt(Math.round(item.expected_profit_after_tax))}`, "profit");
    }
    const signal = el(
      "span",
      `eafc-live-opp-signal${isStrong ? " strong" : ""}`,
      String(item.signal || "buy").replace(/_/g, " ").toUpperCase()
    );
    main.appendChild(signal);
    card.appendChild(main);
    body.appendChild(card);
  });
}

const TAB_CSS = `
  .eafc-live-nav-tab {
    position: fixed;
    width: 46px;
    height: 46px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #1e2838, #0b0e14);
    border: 2px solid #7fd8ff;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55), 0 0 0 4px #0b0e14, 0 0 14px rgba(127, 216, 255, 0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
    line-height: 1;
    color: #7fd8ff;
    z-index: 2147483645;
    cursor: pointer;
    animation: eafc-live-tab-pulse 2.4s ease-in-out infinite;
  }
  .eafc-live-nav-tab:active { transform: scale(0.94); }
  .eafc-live-nav-tab.eafc-live-tab-active {
    background: radial-gradient(circle at 35% 30%, #244059, #0b0e14);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55), 0 0 0 4px #0b0e14, 0 0 20px rgba(127, 216, 255, 0.6);
    animation: none;
  }
  @keyframes eafc-live-tab-pulse {
    0%, 100% { box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55), 0 0 0 4px #0b0e14, 0 0 10px rgba(127, 216, 255, 0.25); }
    50% { box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55), 0 0 0 4px #0b0e14, 0 0 18px rgba(127, 216, 255, 0.55); }
  }
`;

function positionTab(tab, navBar) {
  const rect = navBar.getBoundingClientRect();
  tab.style.top = `${Math.max(8, rect.top - 18)}px`;
  tab.style.right = `${Math.max(8, window.innerWidth - rect.right + 10)}px`;
}

/** Best-effort: injects the floating "EAFC LIVE" badge once EA's nav bar
 * can be found. Safe to call repeatedly (module-level `injected` guard) -
 * does nothing at all if the heuristic can't confidently locate the nav
 * bar, rather than inserting a broken tab. */
export function installNavTab() {
  if (injected) return true;
  const navBar = findNavBar();
  if (!navBar) return false; // will be retried by main.js's pollForNavTab()
  injected = true;

  const tab = document.createElement("div");
  tab.className = "eafc-live-nav-tab";
  tab.textContent = "⚡";
  tab.setAttribute("role", "button");
  tab.setAttribute("aria-label", "EAFC Live opportunities");

  const styleEl = document.createElement("style");
  styleEl.textContent = TAB_CSS;
  document.head.appendChild(styleEl);

  let open = false;
  tab.addEventListener("click", (event) => {
    event.stopPropagation();
    event.preventDefault();
    open = !open;
    if (open) openPanel();
    else closePanel();
  });

  // Any OTHER tab in the same bar being tapped means the user is
  // genuinely navigating away in EA's own app - close our panel rather
  // than leaving it stuck on top of whatever page they went to. Capture
  // phase so this observes the click before EA's own handler can
  // possibly stop it from bubbling.
  navBar.addEventListener(
    "click",
    (event) => {
      if (event.target === tab || tab.contains(event.target)) return;
      if (open) {
        open = false;
        closePanel();
      }
    },
    { capture: true }
  );

  // Appended to document.body, NOT navBar - v1.5.0 nested the badge
  // inside navBar as `position: absolute`, which live-tested broken two
  // different ways: it rendered nowhere near the bar's own visible box
  // (the matched "nav bar" container's true bounding box isn't what its
  // on-screen tab strip looks like), and taps didn't reach it (an
  // absolutely-positioned descendant is confined to ITS OWN ancestor's
  // stacking context no matter how high its z-index is set - a sibling
  // page region painted later can still sit above it, same as an
  // overlay z-index only ever wins comparisons within one shared
  // stacking context, not globally). Being a direct child of <body>,
  // like overlay.js's and this file's own openPanel() panel already
  // are, sidesteps both problems: position:fixed measures from the
  // viewport, not an unpredictable ancestor box, and a body-level
  // sibling is always its own top-level stacking context, immune to
  // being buried under whatever EA happens to paint nearby.
  // getBoundingClientRect() on the matched nav bar still supplies the
  // real on-screen coordinates to sit next to - just without being
  // physically inside it.
  positionTab(tab, navBar);
  window.addEventListener("resize", () => positionTab(tab, navBar));

  tabEl = tab;
  (document.body || document.documentElement).appendChild(tab);
  log("EAFC LIVE nav badge installed");
  return true;
}
