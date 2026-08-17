// src/app.js
// Landing screen logic for EAFC Live Mobile - Phase 1 proof of concept.
// Bundled to www/app.js by scripts/build.mjs (see that file for why a
// build step exists at all for what's otherwise a plain-JS project,
// matching eafc-live-extension's no-bundler style everywhere else).
//
// Responsibilities, and nothing else in Phase 1:
//   1. Render the landing screen (already in www/index.html).
//   2. On "Launch Web App", open the REAL EA FC Web App URL in a native
//      in-app WebView with a persistent (non-ephemeral) session store,
//      so EA owns login/authentication entirely - this file never sees
//      a password, cookie, or session token, only page-load/URL events.
//   3. Once that WebView has loaded an actual Web App page (not an EA
//      login/account redirect), inject shared/ea-badge.js - the
//      "EAFC LIVE • CONNECTED" proof-of-injection badge.
//
// SECURITY: nothing in this file (or shared/ea-badge.js) ever reads,
// stores, or transmits an EA credential. `persistWebViewData: true`
// only controls whether EA's OWN cookies persist inside the platform
// WebView's own storage (same as Safari/Chrome remembering a login) -
// this app never receives those cookies itself; they stay inside the
// native WebView, owned by EA's session, exactly as required.
import { InAppBrowser, ToolBarType, CloseAction } from "@capgo/capacitor-inappbrowser";
import EA_BADGE_SCRIPT from "virtual:ea-badge-script";

// No locale segment: EA's own web-app entry point is expected to
// redirect to the visitor's locale on its own (this is unverified
// against a real device/session - see README.md's "still needs
// verification" list. If EA requires an explicit locale segment
// instead, change this one constant).
const EA_WEB_APP_URL = "https://www.ea.com/ea-sports-fc/ultimate-team/web-app/";

// Matches the same "this is genuinely the Web App, not an EA login/
// account page" test the Chrome extension's manifest.json content_script
// `matches` pattern encodes (`*ultimate-team*web-app*`) - deliberately
// loose (substring, not full path) since EA's real URL structure
// (locale segments, query params) is only confirmed for the Chrome
// extension's desktop case, not yet for mobile.
const EA_WEB_APP_URL_PATTERN = /ultimate-team.*web-app/i;

let listenersReady = false;
let activeWebviewId = null;

function setStatus(text) {
  const el = document.getElementById("launch-status");
  if (el) el.textContent = text || "";
}

async function injectBadgeIfWebApp(url, id) {
  if (!url || !EA_WEB_APP_URL_PATTERN.test(url)) return;
  try {
    await InAppBrowser.executeScript({ code: EA_BADGE_SCRIPT, id });
    setStatus("EAFC Live badge injected.");
  } catch (err) {
    // Fail harmlessly, same principle as every Chrome-extension file:
    // an injection failure must never be presented as if it broke the
    // EA Web App itself, which the user can keep using regardless.
    console.error("[EAFC Live] badge injection failed:", err);
    setStatus("Could not inject EAFC Live badge (see console).");
  }
}

function ensureListeners() {
  if (listenersReady) return;
  listenersReady = true;

  InAppBrowser.addListener("browserPageLoaded", (event) => {
    activeWebviewId = event.id;
    injectBadgeIfWebApp(event.url, event.id);
  });

  // SPA-style in-app navigation (EA's Web App does not fully reload
  // between most screens) may not always re-fire browserPageLoaded -
  // re-checking on every URL change is what "survive normal SPA
  // navigation where practical" means here. injectBadgeIfWebApp's own
  // idempotency guard (see ea-badge.js) makes a duplicate call harmless.
  InAppBrowser.addListener("urlChangeEvent", (event) => {
    injectBadgeIfWebApp(event.url, event.id);
  });

  InAppBrowser.addListener("closeEvent", () => {
    activeWebviewId = null;
    setStatus("");
  });
}

async function launchWebApp() {
  const button = document.getElementById("launch-btn");
  if (button) button.disabled = true;
  setStatus("Opening EA FC Web App…");
  ensureListeners();

  try {
    const { id } = await InAppBrowser.openWebView({
      url: EA_WEB_APP_URL,
      title: "EA SPORTS FC 26",
      toolbarType: ToolBarType.NAVIGATION,
      showReloadButton: true,
      closeAction: CloseAction.CLOSE,
      // Default is already true; explicit so this line is the one
      // place a reviewer needs to check to confirm EA's session
      // persists across app restarts, same as a normal browser tab.
      persistWebViewData: true,
    });
    activeWebviewId = id;
  } catch (err) {
    console.error("[EAFC Live] failed to open EA Web App:", err);
    setStatus("Could not open the EA Web App (see console).");
  } finally {
    if (button) button.disabled = false;
  }
}

document.getElementById("launch-btn")?.addEventListener("click", () => {
  launchWebApp();
});
