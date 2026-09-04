// src/overlay.js
// Runs in the content script's ISOLATED world.
//
// Component 6 of the milestone: a small, unobtrusive panel - not a
// redesign of the Transfer Market UI. Rendered inside a closed Shadow
// DOM so its styles can never leak into (or be overridden by) the EA
// Web App's own CSS, and vice versa - and so nothing about it is
// selectable by, or visually mistakable for, an official EA element.
// The badge is explicitly labelled "EAFC.LIVE" (never "EA" alone) and
// uses its own colour language, not FUT's gold/rare card styling.
(function () {
  "use strict";

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
    network_error: "EAFC.Live backend unreachable right now.",
    server_error: "EAFC.Live backend returned an error.",
    timeout: "EAFC.Live backend timed out.",
  };

  const STYLES = `
    :host { all: initial; }
    .eafc-live-panel {
      position: fixed;
      bottom: 20px;
      right: 20px;
      z-index: 2147483647;
      width: 280px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      color: #e8ecf1;
      background: #14181f;
      border: 1px solid #2c3542;
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
      overflow: hidden;
    }
    .eafc-live-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      background: #1b2230;
      border-bottom: 1px solid #2c3542;
    }
    .eafc-live-brand {
      font-weight: 700;
      letter-spacing: 0.04em;
      font-size: 11px;
      color: #7fd8ff;
    }
    .eafc-live-status {
      font-size: 10px;
      color: #8a94a6;
    }
    .eafc-live-status.connected { color: #5fd98a; }
    .eafc-live-status.error { color: #e5734a; }
    .eafc-live-body { padding: 10px 12px 12px; }
    .eafc-live-card-name {
      font-weight: 600;
      margin-bottom: 6px;
      color: #ffffff;
    }
    .eafc-live-row {
      display: flex;
      justify-content: space-between;
      padding: 2px 0;
      color: #b7c0cf;
    }
    .eafc-live-row .label { color: #8a94a6; }
    .eafc-live-row .value { font-variant-numeric: tabular-nums; color: #e8ecf1; }
    .eafc-live-divider { height: 1px; background: #2c3542; margin: 8px 0; }
    .eafc-live-verdict {
      margin-top: 8px;
      padding: 6px 8px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 12px;
      text-align: center;
    }
    .eafc-live-verdict.confirmed { background: rgba(95, 217, 138, 0.15); color: #5fd98a; }
    .eafc-live-verdict.neutral { background: rgba(138, 148, 166, 0.12); color: #b7c0cf; }
    .eafc-live-muted { color: #6b7484; font-size: 11px; margin-top: 4px; }
    .eafc-live-debug { color: #4a90d9; font-size: 10px; margin-top: 6px; font-family: monospace; }
    .eafc-live-close {
      cursor: pointer;
      color: #6b7484;
      font-size: 14px;
      line-height: 1;
      padding: 2px 4px;
    }
    .eafc-live-close:hover { color: #e8ecf1; }
  `;

  let hostEl = null;
  let shadow = null;
  // The renderKey of the last panel the user explicitly closed - NOT a
  // page-lifetime flag. A subsequent render() for a DIFFERENT key (a
  // different card, or the same card with different live listings)
  // reopens the panel; only a repeat of the exact dismissed state stays
  // hidden. See content.js for how renderKey is derived.
  let dismissedKey = null;

  function ensureMounted() {
    if (hostEl) return shadow;
    hostEl = document.createElement("div");
    hostEl.id = "eafc-live-companion-root";
    // Closed shadow root: nothing on the EA page can traverse into it,
    // matching "do not make the overlay look like an official EA
    // feature" - it is not discoverable via the page's own DOM queries.
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
   *   "loading"          - card detected, intelligence not back yet
   *   "no-intelligence"  - backend has nothing for this card (404)
   *   "error"            - backend unreachable/timed out/5xx - distinct
   *                        from "no-intelligence" because this one is
   *                        expected to resolve on retry, not a fact
   *                        about the card
   *   "result"           - intelligence + combined live-market summary
   *   "hidden"           - remove the panel entirely
   *
   * `state.key`, when present, identifies this exact market situation
   * (see content.js's renderKey) - used only to remember "the user
   * closed THIS one", not to suppress unrelated future states.
   */
  function render(state) {
    if (!state || state.kind === "hidden") {
      clearPanel();
      return;
    }
    if (state.key && state.key === dismissedKey) {
      clearPanel();
      return;
    }

    const root = ensureMounted();
    clearPanel();

    const panel = el("div", "eafc-live-panel");

    const header = el("div", "eafc-live-header");
    header.appendChild(el("span", "eafc-live-brand", "EAFC.LIVE"));
    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "8px";
    const statusClass =
      state.kind === "result" ? "connected" : state.kind === "loading" ? "" : "error";
    const statusText =
      state.kind === "loading" ? "LOADING" : state.kind === "result" ? "CONNECTED" : "NO DATA";
    right.appendChild(el("span", `eafc-live-status ${statusClass}`.trim(), statusText));
    const closeBtn = el("span", "eafc-live-close", "×");
    closeBtn.addEventListener("click", () => {
      dismissedKey = state.key || null;
      clearPanel();
    });
    right.appendChild(closeBtn);
    header.appendChild(right);
    panel.appendChild(header);

    const body = el("div", "eafc-live-body");

    if (state.kind === "loading") {
      body.appendChild(el("div", "eafc-live-muted", "Loading EAFC.Live intelligence..."));
      panel.appendChild(body);
      root.appendChild(panel);
      return;
    }

    if (state.kind === "no-intelligence") {
      body.appendChild(el("div", "eafc-live-muted", "No intelligence available for this card."));
      panel.appendChild(body);
      root.appendChild(panel);
      return;
    }

    if (state.kind === "error") {
      const message = ERROR_STATUS_TEXT[state.status] || "EAFC.Live backend unavailable right now.";
      body.appendChild(el("div", "eafc-live-muted", message));
      body.appendChild(el("div", "eafc-live-muted", "Will retry automatically on the next market update."));
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

    const verdict = window.EAFCLive.verdictLabel(combined.reason, intelligence.signal);
    const verdictEl = el("div", `eafc-live-verdict ${verdict.tone}`);
    verdictEl.textContent = verdict.tone === "confirmed" ? `✓ ${verdict.text}` : verdict.text;
    body.appendChild(verdictEl);

    body.appendChild(
      el(
        "div",
        "eafc-live-muted",
        "Reference data from EAFC.Live · live listings read from this EA search"
      )
    );

    if (globalThis.EAFC_LIVE_DEBUG && intelligence.source_card_id) {
      body.appendChild(el("div", "eafc-live-debug", `source_card_id: ${intelligence.source_card_id}`));
    }

    panel.appendChild(body);
    root.appendChild(panel);
  }

  window.EAFCLive = window.EAFCLive || {};
  window.EAFCLive.overlay = { render };
})();
