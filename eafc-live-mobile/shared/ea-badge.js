// shared/ea-badge.js
//
// Phase 1 proof-of-concept payload only: proves the mobile shell can
// inject its own JavaScript into the real EA FC Web App page once it has
// loaded inside the in-app WebView, and that the result is visibly and
// unambiguously OUR OWN UI, not EA's. Nothing here touches the Transfer
// Market, reads any EA data, or does anything but paint a small badge.
//
// This is the mobile equivalent of the Chrome extension's
// eafc-live-extension/src/overlay.js - same "closed surface, own
// styling, never mistakable for an EA feature" principle, just far
// smaller because Phase 1 doesn't need the real overlay yet. Phase 2
// replaces/extends this file with the actual companion modules
// (sanitized market parsing, card detection, intelligence fetch,
// combine, render) reusing the same logic the Chrome extension already
// has in eafc-live-extension/src/{card-detector,market-calc,overlay}.js.
//
// Delivered to the page via CapgoCapacitorInappbrowser's executeScript()
// (see src/app.js) - conceptually the same role page-bridge.js /
// content.js play for the Chrome extension, just injected imperatively
// on a "page loaded" event instead of declared in a manifest.
(function () {
  "use strict";

  // Idempotency guard: executeScript() may run again on a later
  // navigation/reload event (see src/app.js's browserPageLoaded /
  // urlChangeEvent listeners) - never inject a second badge.
  if (window.__eafcLiveBadgeInjected) return;
  window.__eafcLiveBadgeInjected = true;

  var badge = document.createElement("div");
  badge.id = "eafc-live-mobile-badge";
  badge.setAttribute(
    "style",
    [
      "position:fixed",
      "right:12px",
      "bottom:calc(12px + env(safe-area-inset-bottom, 0px))",
      "z-index:2147483647",
      "display:flex",
      "align-items:center",
      "gap:6px",
      "padding:8px 12px",
      "border-radius:999px",
      "background:#14181f",
      "border:1px solid #2c3542",
      "box-shadow:0 6px 18px rgba(0,0,0,0.4)",
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif",
      "font-size:11px",
      "font-weight:700",
      "letter-spacing:0.04em",
      "color:#e8ecf1",
      "pointer-events:none", // Phase 1: display-only, never intercepts taps on EA's own UI.
    ].join(";")
  );

  var dot = document.createElement("span");
  dot.setAttribute(
    "style",
    "width:7px;height:7px;border-radius:50%;background:#5fd98a;display:inline-block;flex:none;"
  );

  var label = document.createElement("span");
  label.textContent = "EAFC LIVE • CONNECTED";

  badge.appendChild(dot);
  badge.appendChild(label);
  (document.body || document.documentElement).appendChild(badge);
})();
