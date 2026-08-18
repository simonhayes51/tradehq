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
  .eafc-live-panel {
    position: fixed;
    /* EA's own bottom tab bar (Home/Squads/SBC/Transfers/Store/Club) sits
       roughly 64-72px tall on phone - a plain safe-area offset put this
       panel underneath/behind it. Cleared with a fixed floor, not just
       the safe-area inset. */
    bottom: max(76px, calc(env(safe-area-inset-bottom) + 68px));
    right: max(12px, env(safe-area-inset-right));
    z-index: 2147483647;
    width: min(88vw, 270px);
    max-height: 70vh;
    overflow-y: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    color: #e8ecf1;
    background: #14181f;
    border: 1px solid #2c3542;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
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
  .eafc-live-panel.collapsed .eafc-live-header { border-bottom: none; border-radius: 12px; }
  .eafc-live-panel.collapsed .eafc-live-body { display: none; }
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
let collapsed = false;

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
 *   "loading", "no-intelligence", "error", "result", "hidden"
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

  const controls = el("div", "eafc-live-controls");
  const statusClass =
    state.kind === "result" ? "connected" : state.kind === "loading" ? "" : "error";
  const statusText =
    state.kind === "loading" ? "LOADING" : state.kind === "result" ? "CONNECTED" : "NO DATA";
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

  if (state.kind === "loading") {
    body.appendChild(el("div", "eafc-live-muted", "Loading…"));
    panel.appendChild(body);
    root.appendChild(panel);
    return;
  }

  if (state.kind === "no-intelligence") {
    body.appendChild(el("div", "eafc-live-muted", "No data for this card."));
    panel.appendChild(body);
    root.appendChild(panel);
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
    panel.appendChild(body);
    root.appendChild(panel);
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

  panel.appendChild(body);
  root.appendChild(panel);
}
