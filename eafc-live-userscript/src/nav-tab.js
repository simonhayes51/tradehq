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
// WHY A CLONE OF EA'S OWN LAST TAB, NOT A HAND-STYLED BUTTON
// -------------------------------------------------------------------
// Cloning an existing tab item's DOM structure (cloneNode, which never
// copies JS event listeners - only markup/CSS) means the new tab
// automatically inherits EA's own current icon size, spacing, active/
// inactive colors, and safe-area handling, without this file having to
// guess or hand-maintain any of it. Only the label text and icon glyph
// are replaced.
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

const KNOWN_TAB_LABELS = ["Home", "Squads", "Transfers"];

/** Finds EA's bottom nav bar by locating a container whose direct
 * children collectively include text matching every label in
 * KNOWN_TAB_LABELS - the common ancestor of "Home"/"Squads"/"Transfers"
 * as sibling elements. Returns null (never throws, never guesses a
 * wrong container) if that pattern isn't found. */
function findNavBar() {
  const candidates = document.querySelectorAll("body *");
  for (const el of candidates) {
    if (el.children.length < KNOWN_TAB_LABELS.length) continue;
    const childTexts = Array.from(el.children).map((c) => (c.textContent || "").trim());
    const hasAll = KNOWN_TAB_LABELS.every((label) => childTexts.some((t) => t === label));
    if (hasAll) return el;
  }
  return null;
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
    background: #14181f; border: 1px solid #2c3542; border-radius: 10px;
    padding: 12px; margin-bottom: 10px;
  }
  .eafc-live-opp-name { font-weight: 700; font-size: 14px; margin-bottom: 6px; }
  .eafc-live-opp-row { display: flex; justify-content: space-between; font-size: 12px; color: #b7c0cf; padding: 2px 0; }
  .eafc-live-opp-row .label { color: #8a94a6; }
  .eafc-live-opp-row .value { font-variant-numeric: tabular-nums; color: #e8ecf1; }
  .eafc-live-opp-signal {
    display: inline-block; margin-top: 6px; padding: 3px 8px; border-radius: 5px;
    font-size: 10px; font-weight: 700; letter-spacing: 0.02em;
    background: rgba(95, 217, 138, 0.15); color: #5fd98a;
  }
  .eafc-live-muted { color: #6b7484; font-size: 12px; text-align: center; padding: 24px 0; }
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
  body.appendChild(el("div", "eafc-live-muted", "Loading…"));
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

  for (const item of items) {
    const card = el("div", "eafc-live-opp-card");
    card.appendChild(el("div", "eafc-live-opp-name", [item.name, item.rarity].filter(Boolean).join(" — ")));
    const row = (label, value) => {
      const r = el("div", "eafc-live-opp-row");
      r.appendChild(el("span", "label", label));
      r.appendChild(el("span", "value", value));
      card.appendChild(r);
    };
    row("Current BIN", fmt(item.current_bin));
    row("Buy Below", fmt(item.recommended_buy_max ?? item.buy_below));
    if (item.expected_profit_after_tax != null) row("Expected Profit", fmt(Math.round(item.expected_profit_after_tax)));
    const signal = el("span", "eafc-live-opp-signal", String(item.signal || "buy").replace(/_/g, " ").toUpperCase());
    card.appendChild(signal);
    body.appendChild(card);
  }
}

const TAB_CSS = `
  .eafc-live-tab-active :is(span, div, p, svg, img) { color: #7fd8ff !important; }
`;

/** Best-effort: injects the "EAFC LIVE" tab once EA's nav bar can be
 * found. Safe to call repeatedly (module-level `injected` guard) - does
 * nothing at all if the heuristic can't confidently locate the nav bar,
 * rather than inserting a broken tab. */
export function installNavTab() {
  if (injected) return true;
  const navBar = findNavBar();
  if (!navBar) return false; // will be retried by main.js's pollForNavTab()
  injected = true;

  const templateTab = navBar.lastElementChild;
  const tab = templateTab.cloneNode(true);
  tab.removeAttribute("id");
  tab.classList.add("eafc-live-nav-tab");

  // Replace whatever text/icon the cloned template tab had with ours,
  // touching only text nodes and any <svg>/<img> icon - never removing
  // elements this project doesn't understand the purpose of, since the
  // clone's structure (spacing, sizing, active-state wiring on the
  // PARENT container which we don't touch) is what makes it look native.
  const walker = document.createTreeWalker(tab, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  let n;
  while ((n = walker.nextNode())) textNodes.push(n);
  if (textNodes.length > 0) {
    textNodes[0].nodeValue = "EAFC LIVE";
    for (let i = 1; i < textNodes.length; i++) textNodes[i].nodeValue = "";
  }
  for (const icon of tab.querySelectorAll("svg, img")) icon.remove();
  const iconSpan = document.createElement("span");
  iconSpan.textContent = "⚡";
  iconSpan.style.fontSize = "18px";
  tab.insertBefore(iconSpan, tab.firstChild);

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

  tabEl = tab;
  navBar.appendChild(tab);
  log("EAFC LIVE nav tab installed");
  return true;
}
