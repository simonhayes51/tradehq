// src/overlay.js
//
// Component 6 of the milestone: a small, unobtrusive panel - not a
// redesign of the Transfer Market UI. Adapted from
// eafc-live-extension/src/overlay.js with the same rendering states and
// verdict text, plus responsive/touch changes for phone-sized screens
// (the Chrome extension only ever had to work on a desktop popup-window
// viewport):
//
//   - width uses min(92vw, 300px) instead of a fixed 280px, so it never
//     overflows a phone screen in portrait.
//   - a collapse toggle (tap the header) shrinks the panel to just its
//     header bar - useful on a phone where the panel would otherwise sit
//     over on-screen content for longer.
//   - close/collapse tap targets are >=32px, not the desktop-mouse-sized
//     14px `×` the extension used.
//   - `env(safe-area-inset-bottom)` padding so the panel clears an
//     iPhone's home-indicator area.
//
// Rendered inside a closed Shadow DOM so its styles can never leak into
// (or be overridden by) the EA Web App's own CSS, and vice versa - and
// so nothing about it is selectable by, or visually mistakable for, an
// official EA element. The badge is explicitly labelled "EAFC LIVE"
// (never "EA" alone) and uses its own colour language, not FUT's
// gold/rare card styling.
import { verdictLabel } from "./market-calc.js";
import { getNavTabDiagnostic } from "./nav-tab.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
function fmt(n) {
  return n === null || n === undefined ? "-" : NUMBER_FORMAT.format(n);
}

function fmtExpiry(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

const ERROR_STATUS_TEXT = {
  network_error: "Backend unreachable.",
  server_error: "Backend error.",
  timeout: "Backend timed out.",
};

const STYLES = `
  :host { all: initial; }
  /* Bottom-anchored (any fixed corner, really) inevitably collides with
     SOME EA control eventually - the bottom tab bar on a results
     screen, a Reset/Search button on a filters screen, different things
     on different screens we don't control the layout of. The vertical
     middle of the right edge is the one spot that's consistently empty
     across every EA screen actually seen live (search results, filters,
     item detail) - anchoring there instead of chasing each individual
     overlap. */
  .eafc-live-panel {
    position: fixed;
    top: 50%;
    transform: translateY(-50%);
    right: max(10px, env(safe-area-inset-right));
    z-index: 2147483647;
    width: min(88vw, 270px);
    max-height: 60vh;
    overflow-y: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    color: #e8ecf1;
    background: #14181f;
    border: 1px solid #2c3542;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  }
  /* Collapsed: a small icon-only circle, not a full-width bar - even if
     it does end up near a real EA control, it's small enough not to
     cover it. This is the DEFAULT resting state, not just what "x"
     leaves behind. */
  .eafc-live-panel.collapsed {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    overflow: visible;
  }
  .eafc-live-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    background: #1b2230;
    border-bottom: 1px solid #2c3542;
    border-radius: 12px 12px 0 0;
    cursor: pointer;
    user-select: none;
  }
  .eafc-live-panel.collapsed .eafc-live-header {
    width: 44px;
    height: 44px;
    padding: 0;
    border-bottom: none;
    border-radius: 50%;
    justify-content: center;
  }
  .eafc-live-panel.collapsed .eafc-live-body { display: none; }
  .eafc-live-panel.collapsed .eafc-live-controls { display: none; }
  .eafc-live-collapsed-icon { display: none; font-size: 18px; }
  .eafc-live-panel.collapsed .eafc-live-collapsed-icon { display: block; }
  .eafc-live-panel.collapsed .eafc-live-brand { display: none; }
  .eafc-live-brand {
    font-weight: 700;
    letter-spacing: 0.04em;
    font-size: 11px;
    color: #7fd8ff;
  }
  .eafc-live-status { font-size: 10px; color: #8a94a6; }
  .eafc-live-status.connected { color: #5fd98a; }
  .eafc-live-status.error { color: #e5734a; }
  .eafc-live-body { padding: 10px 12px 14px; }
  .eafc-live-card-name { font-weight: 600; margin-bottom: 6px; color: #ffffff; }
  .eafc-live-row { display: flex; justify-content: space-between; padding: 3px 0; color: #b7c0cf; }
  .eafc-live-row .label { color: #8a94a6; }
  .eafc-live-row .value { font-variant-numeric: tabular-nums; color: #e8ecf1; }
  .eafc-live-divider { height: 1px; background: #2c3542; margin: 8px 0; }
  .eafc-live-verdict {
    margin-top: 8px;
    padding: 8px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 12px;
    text-align: center;
  }
  .eafc-live-verdict.confirmed { background: rgba(95, 217, 138, 0.15); color: #5fd98a; }
  .eafc-live-verdict.neutral { background: rgba(138, 148, 166, 0.12); color: #b7c0cf; }
  .eafc-live-muted { color: #6b7484; font-size: 11px; margin-top: 4px; }
  .eafc-live-debug { color: #4a90d9; font-size: 10px; margin-top: 6px; font-family: monospace; }
  .eafc-live-controls { display: flex; align-items: center; gap: 4px; }
  .eafc-live-btn {
    cursor: pointer;
    color: #8a94a6;
    font-size: 16px;
    line-height: 1;
    min-width: 32px;
    min-height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
  }
  .eafc-live-btn:hover, .eafc-live-btn:active { color: #e8ecf1; background: rgba(255,255,255,0.06); }
`;

let hostEl = null;
let shadow = null;
// "×" used to fully hide the panel via a dismissedKey that only the
// EXACT same market state would clear - in practice that meant closing
// it once could make it unrecoverable (no visible way back short of a
// new detection event with a different key). "×" now just collapses to
// the header pill instead, same as tapping the header - always tappable
// to reopen, never gone for good.
// Starts collapsed (a small dot, not a panel) - live feedback was that
// an automatically-expanded panel was "in the way" and "too wordy" on
// every single detection. The dot alone still conveys the key signal at
// a glance (color = connected/confirmed vs error vs neutral); expanding
// is now always an explicit tap, never something sprung on the user.
let collapsed = true;

function ensureMounted() {
  if (hostEl && hostEl.isConnected) return shadow;
  hostEl = document.createElement("div");
  hostEl.id = "eafc-live-companion-root";
  // Closed shadow root: nothing on the EA page can traverse into it.
  shadow = hostEl.attachShadow({ mode: "closed" });
  const styleEl = document.createElement("style");
  styleEl.textContent = STYLES;
  shadow.appendChild(styleEl);
  (document.body || document.documentElement).appendChild(hostEl);
  return shadow;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clearPanel() {
  if (!shadow) return;
  const existing = shadow.querySelector(".eafc-live-panel");
  if (existing) existing.remove();
}

function addRow(body, label, value) {
  const row = el("div", "eafc-live-row");
  row.appendChild(el("span", "label", label));
  row.appendChild(el("span", "value", value));
  body.appendChild(row);
}

/**
 * Renders one of a small set of states. `state.kind` is one of:
 *   "loading", "no-intelligence", "error", "result", "watching", "hidden"
 * "watching" is the multi-card-page presence indicator (see main.js) -
 * badges (list-badges.js) carry the actual per-row signal there; this
 * just proves the script is alive when a page has nothing to flag.
 * `state.key`, when present, identifies this exact market situation -
 * used only to remember "the user closed THIS one".
 */
export function render(state) {
  if (!state || state.kind === "hidden") {
    clearPanel();
    return;
  }

  const root = ensureMounted();
  clearPanel();

  const panel = el("div", `eafc-live-panel${collapsed ? " collapsed" : ""}`);

  const header = el("div", "eafc-live-header");
  header.appendChild(el("span", "eafc-live-brand", "EAFC LIVE"));

  const statusClass =
    state.kind === "result" ? "connected" : state.kind === "loading" || state.kind === "watching" ? "" : "error";
  const statusText =
    state.kind === "loading" ? "LOADING" :
    state.kind === "result" ? "CONNECTED" :
    state.kind === "watching" ? "WATCHING" : "NO DATA";

  // The collapsed resting state shows only this - a colored dot (no
  // text) so there's still an at-a-glance signal without expanding.
  const collapsedIcon = el("span", "eafc-live-collapsed-icon", "⚡");
  collapsedIcon.style.color =
    statusClass === "connected" ? "#5fd98a" : statusClass === "error" ? "#e5734a" : "#8a94a6";
  header.appendChild(collapsedIcon);

  const controls = el("div", "eafc-live-controls");
  controls.appendChild(el("span", `eafc-live-status ${statusClass}`.trim(), statusText));

  const collapseBtn = el("span", "eafc-live-btn", collapsed ? "▴" : "▾");
  collapseBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    collapsed = !collapsed;
    render(state);
  });
  controls.appendChild(collapseBtn);

  const closeBtn = el("span", "eafc-live-btn", "×");
  closeBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    collapsed = true;
    render(state);
  });
  controls.appendChild(closeBtn);

  header.appendChild(controls);
  header.addEventListener("click", () => {
    collapsed = !collapsed;
    render(state);
  });
  panel.appendChild(header);

  const body = el("div", "eafc-live-body");

  // Surfaces findNavBar()'s outcome (see nav-tab.js) on every expanded
  // render, not just once - the nav tab's own failure mode is silent by
  // design (no broken tab), so without this line there would be NO way
  // to tell "not injected yet" from "will never match" on a device with
  // no attached console. Only shown expanded, not on the collapsed dot,
  // to avoid permanently cluttering the default resting state.
  const finish = () => {
    if (!collapsed) {
      body.appendChild(el("div", "eafc-live-debug", `nav tab: ${getNavTabDiagnostic()}`));
    }
    panel.appendChild(body);
    root.appendChild(panel);
  };

  if (state.kind === "watching") {
    const text =
      state.cardsChecked == null
        ? "Watching this search."
        : state.badgeCount > 0
          ? `${state.cardsChecked} card${state.cardsChecked === 1 ? "" : "s"} checked, ${state.badgeCount} flagged.`
          : `${state.cardsChecked} card${state.cardsChecked === 1 ? "" : "s"} checked, nothing actionable right now.`;
    body.appendChild(el("div", "eafc-live-muted", text));
    // Same "no console access" reasoning as the nav-tab diagnostic below
    // - only shown when at least one badge was actually attempted, since
    // an empty page has nothing to report.
    if (!collapsed && state.badgeDiagnostic && state.badgeCount > 0) {
      body.appendChild(el("div", "eafc-live-debug", state.badgeDiagnostic));
    }
    finish();
    return;
  }

  if (state.kind === "loading") {
    body.appendChild(el("div", "eafc-live-muted", "Loading…"));
    finish();
    return;
  }

  if (state.kind === "no-intelligence") {
    body.appendChild(el("div", "eafc-live-muted", "No data for this card."));
    finish();
    return;
  }

  if (state.kind === "error") {
    const message = ERROR_STATUS_TEXT[state.status] || "Backend unavailable.";
    body.appendChild(el("div", "eafc-live-muted", message));
    // Always shown (not gated behind EAFC_LIVE_DEBUG) - on iOS there is
    // no attached console without a Mac, so this on-screen line is the
    // only debugging channel available for diagnosing a real failure.
    // See api-client.js's callGM()/fetchIntelligence() for what sets it.
    if (state.detail) {
      body.appendChild(el("div", "eafc-live-debug", state.detail));
    }
    finish();
    return;
  }

  // state.kind === "result"
  const { player, intelligence, combined } = state;

  if (player && (player.name || player.rarity)) {
    const title = [player.name, player.rarity].filter(Boolean).join(" — ");
    body.appendChild(el("div", "eafc-live-card-name", title));
  }

  if (intelligence.fair_value !== null && intelligence.fair_value !== undefined) {
    addRow(body, "Fair Value", fmt(intelligence.fair_value));
  }
  if (intelligence.buy_below !== null && intelligence.buy_below !== undefined) {
    addRow(body, "Buy Below", fmt(intelligence.buy_below));
  }
  addRow(body, "Signal", (intelligence.signal || "unknown").replace(/_/g, " ").toUpperCase());

  body.appendChild(el("div", "eafc-live-divider"));

  addRow(body, "Live Cheapest", combined.cheapestBin !== null ? fmt(combined.cheapestBin) : "no listings");
  addRow(body, "Live Listings", String(combined.listingCount));
  if (combined.hasIntelligence) {
    addRow(body, "Below Buy Target", String(combined.underBuyBelowCount));
  }
  const expiry = fmtExpiry(combined.nearestExpirySeconds);
  if (expiry) addRow(body, "Nearest Expiry", expiry);
  if (combined.potentialProfitAfterTax !== null && combined.potentialProfitAfterTax !== undefined) {
    addRow(body, "Potential Profit", fmt(combined.potentialProfitAfterTax));
  }

  const verdict = verdictLabel(combined.reason, intelligence.signal);
  const verdictEl = el("div", `eafc-live-verdict ${verdict.tone}`);
  verdictEl.textContent = verdict.tone === "confirmed" ? `✓ ${verdict.text}` : verdict.text;
  body.appendChild(verdictEl);

  if (window.EAFC_LIVE_DEBUG && intelligence.source_card_id) {
    body.appendChild(el("div", "eafc-live-debug", `source_card_id: ${intelligence.source_card_id}`));
  }

  finish();
}
