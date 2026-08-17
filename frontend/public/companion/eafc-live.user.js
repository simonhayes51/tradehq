// ==UserScript==
// @name         EAFC Live Companion
// @namespace    https://app.eafc.live/
// @version      1.0.1
// @description  Read-only EAFC Live trading intelligence overlay for the EA SPORTS FC 26 Web App Transfer Market. Never buys, bids, lists, or automates anything - observation only.
// @author       EAFC Live
// @match        https://www.ea.com/*/ea-sports-fc/ultimate-team/web-app/*
// @match        https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*
// @connect      api.eafc.live
// @grant        GM_xmlhttpRequest
// @grant        GM.xmlHttpRequest
// @inject-into  page
// @run-at       document-start
// @downloadURL  https://app.eafc.live/companion/eafc-live.user.js
// @updateURL    https://app.eafc.live/companion/eafc-live.user.js
// ==/UserScript==

(() => {
  // src/card-detector.js
  function toPositiveInt(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
  }
  function extractExactCardId(itemData) {
    if (!itemData || typeof itemData !== "object") return null;
    return toPositiveInt(itemData.resourceId) || toPositiveInt(itemData.definitionId) || null;
  }
  function normalizeAuction(entry) {
    if (!entry || typeof entry !== "object") return null;
    const item = entry.itemData || entry.item || {};
    const sourceCardId = extractExactCardId(item);
    if (sourceCardId === null) return null;
    return {
      sourceCardId,
      tradeId: toPositiveInt(entry.tradeId ?? item.id) ?? null,
      rating: Number.isFinite(Number(item.rating)) ? Number(item.rating) : null,
      buyNowPrice: toPositiveInt(entry.buyNowPrice) ?? 0,
      currentBid: toPositiveInt(entry.currentBid) ?? 0,
      startingBid: toPositiveInt(entry.startingBid) ?? 0,
      // Seconds remaining, as EA reports it. 0/undefined is treated as
      // "unknown", never as "expired now".
      expires: toPositiveInt(entry.expires),
      tradeState: typeof entry.tradeState === "string" ? entry.tradeState : null
    };
  }
  function extractAuctionsFromResponse(body) {
    if (!body || typeof body !== "object") return [];
    let rawList;
    if (Array.isArray(body.auctionInfo)) {
      rawList = body.auctionInfo;
    } else if (Array.isArray(body)) {
      rawList = body;
    } else if (body.itemData || body.buyNowPrice !== void 0) {
      rawList = [body];
    } else {
      rawList = [];
    }
    const out = [];
    for (const entry of rawList) {
      const normalized = normalizeAuction(entry);
      if (normalized) out.push(normalized);
    }
    return out;
  }
  function groupAuctionsByCardId(auctions) {
    const list = Array.isArray(auctions) ? auctions.filter((a) => a && Number.isFinite(a.sourceCardId)) : [];
    const groupedByCardId = {};
    for (const auction of list) {
      (groupedByCardId[auction.sourceCardId] = groupedByCardId[auction.sourceCardId] || []).push(auction);
    }
    const distinctIds = Object.keys(groupedByCardId);
    const sourceCardId = distinctIds.length === 1 ? Number(distinctIds[0]) : null;
    return { sourceCardId, auctions: list, groupedByCardId };
  }

  // src/market-observer.js
  var FC26_RE = /\/ut\/game\/fc26(\/|$)/;
  var TRANSFERMARKET_RE = /\/transfermarket(\/|$|\?)/;
  function log(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }
  var onDetected = null;
  var installed = false;
  function onCardDetected(callback) {
    onDetected = callback;
  }
  function handleResponseBody(url, bodyText) {
    let body = null;
    try {
      body = bodyText ? JSON.parse(bodyText) : null;
    } catch (_err) {
      return;
    }
    let auctions;
    try {
      auctions = extractAuctionsFromResponse(body);
    } catch (err) {
      log("card extraction failed, dropping response:", err && err.message);
      return;
    }
    if (!auctions || auctions.length === 0) {
      log("no identifiable auctions in this response");
      return;
    }
    let result;
    try {
      result = groupAuctionsByCardId(auctions);
    } catch (err) {
      log("card grouping failed, ignoring response:", err && err.message);
      return;
    }
    log(`${result.auctions.length} auction(s) detected`);
    if (result.sourceCardId) {
      log("exact card ID:", result.sourceCardId);
    } else {
      log("response contains multiple distinct card IDs; not treating as a single-card view");
    }
    if (typeof onDetected === "function") {
      try {
        onDetected({ ...result, url });
      } catch (err) {
        log("onCardDetected callback threw:", err && err.message);
      }
    }
  }
  function installMarketObserver() {
    if (installed) return;
    installed = true;
    const originalFetch = window.fetch;
    if (typeof originalFetch === "function") {
      window.fetch = function eafcLiveFetch(input, init) {
        const promise = originalFetch.apply(this, arguments);
        try {
          const url = typeof input === "string" ? input : input && input.url;
          if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {
            promise.then((response) => {
              response.clone().text().then((text) => handleResponseBody(url, text)).catch(() => {
              });
            }).catch(() => {
            });
          }
        } catch (err) {
          log("fetch hook observation failed (request unaffected):", err && err.message);
        }
        return promise;
      };
    }
    const OriginalXHR = window.XMLHttpRequest;
    if (OriginalXHR) {
      const originalOpen = OriginalXHR.prototype.open;
      const originalSend = OriginalXHR.prototype.send;
      OriginalXHR.prototype.open = function eafcLiveOpen(method, url) {
        try {
          this.__eafcLiveUrl = String(url);
        } catch (_err) {
          this.__eafcLiveUrl = null;
        }
        return originalOpen.apply(this, arguments);
      };
      OriginalXHR.prototype.send = function eafcLiveSend() {
        const url = this.__eafcLiveUrl;
        if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {
          this.addEventListener("loadend", function() {
            try {
              handleResponseBody(url, this.responseText);
            } catch (err) {
              log("XHR hook observation failed (request unaffected):", err && err.message);
            }
          });
        }
        return originalSend.apply(this, arguments);
      };
    }
    console.log("[EAFC Live] market observer installed - watching window.fetch/XHR for /ut/game/fc26 responses", {
      href: location.href,
      inIframe: window !== window.top
    });
  }

  // src/config.js
  var CONFIG = {
    // Base URL of the EAFC Live backend's v2 API
    // (backend/app/routers/v2/companion.py mounts /companion/card/{id}
    // under this prefix).
    API_BASE_URL: "https://api.eafc.live/api/v2",
    // How long a SUCCESSFUL companion/card/{id} response is reused before
    // re-fetching. Keeps a Transfer Market search returning 20+ auctions
    // for the same card from triggering 20+ backend calls.
    SUCCESS_CACHE_TTL_MS: 5 * 60 * 1e3,
    // How long a definitive 404 ("we have never seen this card") is
    // negative-cached. A transient failure (network error, 5xx, timeout)
    // is NEVER cached at all - see intelligence-cache.js.
    NOT_FOUND_CACHE_TTL_MS: 60 * 1e3,
    // Network timeout for the companion API call, so a slow/unreachable
    // backend can never hang the overlay or the page.
    REQUEST_TIMEOUT_MS: 6e3
  };

  // src/intelligence-cache.js
  function createIntelligenceCache(fetchFn, options) {
    if (typeof fetchFn !== "function") {
      throw new TypeError("createIntelligenceCache requires a fetchFn");
    }
    const opts = options || {};
    const successTtlMs = opts.successTtlMs ?? 5 * 60 * 1e3;
    const notFoundTtlMs = opts.notFoundTtlMs ?? 60 * 1e3;
    const now = typeof opts.now === "function" ? opts.now : () => Date.now();
    const cache2 = /* @__PURE__ */ new Map();
    const inFlight = /* @__PURE__ */ new Map();
    async function get(sourceCardId) {
      const id = Number(sourceCardId);
      if (!Number.isFinite(id) || id <= 0) {
        return { status: "network_error", data: null };
      }
      const cached = cache2.get(id);
      if (cached && cached.expiresAt > now()) {
        return cached.result;
      }
      const pending = inFlight.get(id);
      if (pending) return pending;
      const promise = (async () => {
        let result;
        try {
          result = await fetchFn(id);
        } finally {
          inFlight.delete(id);
        }
        if (!result || typeof result.status !== "string") {
          result = { status: "network_error", data: null };
        }
        if (result.status === "success") {
          cache2.set(id, { expiresAt: now() + successTtlMs, result });
        } else if (result.status === "not_found") {
          cache2.set(id, { expiresAt: now() + notFoundTtlMs, result });
        } else {
          cache2.delete(id);
        }
        return result;
      })();
      inFlight.set(id, promise);
      return promise;
    }
    function clear() {
      cache2.clear();
      inFlight.clear();
    }
    return { get, clear };
  }

  // src/api-client.js
  function log2(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }
  function resolveGMRequest() {
    if (typeof GM_xmlhttpRequest === "function") return GM_xmlhttpRequest;
    if (typeof GM !== "undefined" && GM && typeof GM.xmlHttpRequest === "function") {
      return GM.xmlHttpRequest.bind(GM);
    }
    return null;
  }
  function callGM(gmRequest, details) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const settleResolve = (response) => {
        if (settled) return;
        settled = true;
        resolve(response);
      };
      const settleReject = (kind) => {
        if (settled) return;
        settled = true;
        reject({ kind });
      };
      let maybePromise;
      try {
        maybePromise = gmRequest({
          ...details,
          onload: settleResolve,
          onerror: () => settleReject("network_error"),
          ontimeout: () => settleReject("timeout"),
          onabort: () => settleReject("network_error")
        });
      } catch (err) {
        settleReject("network_error");
        return;
      }
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise.then(settleResolve).catch(() => settleReject("network_error"));
      }
    });
  }
  async function fetchIntelligence(sourceCardId) {
    const gmRequest = resolveGMRequest();
    if (!gmRequest) {
      log2("no GM_xmlhttpRequest / GM.xmlHttpRequest available in this userscript manager");
      return { status: "network_error", data: null };
    }
    const url = `${CONFIG.API_BASE_URL}/companion/card/${sourceCardId}`;
    let response;
    try {
      response = await callGM(gmRequest, {
        method: "GET",
        url,
        timeout: CONFIG.REQUEST_TIMEOUT_MS,
        headers: { Accept: "application/json" }
      });
    } catch (err) {
      if (err && err.kind === "timeout") {
        log2("companion API request timed out for card", sourceCardId);
        return { status: "timeout", data: null };
      }
      log2("companion API request failed for card", sourceCardId);
      return { status: "network_error", data: null };
    }
    const status = response.status;
    if (status === 404) {
      log2("no intelligence available for card", sourceCardId);
      return { status: "not_found", data: null };
    }
    if (status >= 500 || !(status >= 200 && status < 300)) {
      log2("companion API unexpected status", status, "for card", sourceCardId);
      return { status: "server_error", data: null };
    }
    try {
      const data = JSON.parse(response.responseText);
      log2("intelligence loaded for card", sourceCardId, data && data.signal);
      return { status: "success", data };
    } catch (err) {
      log2("companion API returned unparsable JSON for card", sourceCardId, ":", err && err.message);
      return { status: "server_error", data: null };
    }
  }
  var cache = createIntelligenceCache(fetchIntelligence, {
    successTtlMs: CONFIG.SUCCESS_CACHE_TTL_MS,
    notFoundTtlMs: CONFIG.NOT_FOUND_CACHE_TTL_MS
  });
  function getCardIntelligence(sourceCardId) {
    return cache.get(sourceCardId);
  }

  // src/market-calc.js
  var EA_TAX = 0.05;
  function isBuyable(auction) {
    return !!auction && Number.isFinite(auction.buyNowPrice) && auction.buyNowPrice > 0;
  }
  function cheapestBin(auctions) {
    const buyable = (auctions || []).filter(isBuyable);
    if (buyable.length === 0) return null;
    return buyable.reduce((min, a) => Math.min(min, a.buyNowPrice), Infinity);
  }
  function countAtOrBelow(auctions, threshold) {
    if (threshold === null || threshold === void 0) return 0;
    return (auctions || []).filter((a) => isBuyable(a) && a.buyNowPrice <= threshold).length;
  }
  function countBelow(auctions, threshold) {
    if (threshold === null || threshold === void 0) return 0;
    return (auctions || []).filter((a) => isBuyable(a) && a.buyNowPrice < threshold).length;
  }
  function nearestExpiry(auctions) {
    const withExpiry = (auctions || []).filter(
      (a) => a && Number.isFinite(a.expires) && a.expires > 0
    );
    if (withExpiry.length === 0) return null;
    return withExpiry.reduce((min, a) => Math.min(min, a.expires), Infinity);
  }
  function afterTaxProfit(buyPrice, sellPrice) {
    if (!Number.isFinite(buyPrice) || !Number.isFinite(sellPrice)) return null;
    const netProceeds = sellPrice * (1 - EA_TAX);
    return Math.round(netProceeds - buyPrice);
  }
  var BUY_SIGNALS = /* @__PURE__ */ new Set(["buy", "strong_buy"]);
  function combineIntelligence(auctions, intelligence) {
    const list = auctions || [];
    const cheapest = cheapestBin(list);
    const listingCount = list.filter(isBuyable).length;
    const nearestExpirySeconds = nearestExpiry(list);
    if (!intelligence) {
      return {
        hasIntelligence: false,
        cheapestBin: cheapest,
        listingCount,
        underBuyBelowCount: 0,
        underFairValueCount: 0,
        nearestExpirySeconds,
        potentialProfitAfterTax: null,
        opportunityConfirmed: false,
        reason: "no_intelligence"
      };
    }
    const target = intelligence.buy_below ?? intelligence.recommended_buy_max ?? null;
    const fairValue = intelligence.fair_value ?? null;
    const sellTarget = intelligence.recommended_sell_target ?? fairValue;
    const underBuyBelowCount = countAtOrBelow(list, target);
    const underFairValueCount = countBelow(list, fairValue);
    const potentialProfitAfterTax = cheapest !== null && sellTarget !== null ? afterTaxProfit(cheapest, sellTarget) : null;
    const isBuySignal = BUY_SIGNALS.has(intelligence.signal);
    let opportunityConfirmed = false;
    let reason;
    if (!isBuySignal) {
      reason = "no_buy_signal";
    } else if (cheapest === null) {
      reason = "no_live_listings";
    } else if (target === null) {
      reason = "no_target_price";
    } else if (cheapest <= target) {
      opportunityConfirmed = true;
      reason = "confirmed";
    } else {
      reason = "above_target";
    }
    return {
      hasIntelligence: true,
      cheapestBin: cheapest,
      listingCount,
      underBuyBelowCount,
      underFairValueCount,
      nearestExpirySeconds,
      potentialProfitAfterTax,
      opportunityConfirmed,
      reason
    };
  }
  function verdictLabel(reason, signal) {
    switch (reason) {
      case "confirmed":
        return { text: "OPPORTUNITY CONFIRMED", tone: "confirmed" };
      case "above_target":
        return { text: "NO LIVE LISTING BELOW TARGET", tone: "neutral" };
      case "no_live_listings":
        return { text: "NO LIVE BIN LISTINGS", tone: "neutral" };
      case "no_target_price":
        return { text: "NO BUY TARGET AVAILABLE", tone: "neutral" };
      case "no_intelligence":
        return { text: "NO EAFC LIVE INTELLIGENCE", tone: "neutral" };
      case "no_buy_signal": {
        const label = signal ? String(signal).replace(/_/g, " ").toUpperCase() : null;
        return {
          text: label ? `LIVE MARKET \u2014 ${label} (NOT A BUY)` : "LIVE MARKET \u2014 NO BUY RECOMMENDATION",
          tone: "neutral"
        };
      }
      default:
        return { text: "NO LIVE LISTING BELOW TARGET", tone: "neutral" };
    }
  }

  // src/overlay.js
  var NUMBER_FORMAT = new Intl.NumberFormat("en-US");
  function fmt(n) {
    return n === null || n === void 0 ? "-" : NUMBER_FORMAT.format(n);
  }
  function fmtExpiry(seconds) {
    if (seconds === null || seconds === void 0) return null;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  var ERROR_STATUS_TEXT = {
    network_error: "EAFC Live backend unreachable right now.",
    server_error: "EAFC Live backend returned an error.",
    timeout: "EAFC Live backend timed out."
  };
  var STYLES = `
  :host { all: initial; }
  .eafc-live-panel {
    position: fixed;
    bottom: max(16px, env(safe-area-inset-bottom));
    right: max(16px, env(safe-area-inset-right));
    z-index: 2147483647;
    width: min(92vw, 300px);
    max-height: 80vh;
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
  var hostEl = null;
  var shadow = null;
  var dismissedKey = null;
  var collapsed = false;
  function ensureMounted() {
    if (hostEl && hostEl.isConnected) return shadow;
    hostEl = document.createElement("div");
    hostEl.id = "eafc-live-companion-root";
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
    if (text !== void 0) node.textContent = text;
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
    const panel = el("div", `eafc-live-panel${collapsed ? " collapsed" : ""}`);
    const header = el("div", "eafc-live-header");
    header.appendChild(el("span", "eafc-live-brand", "EAFC LIVE"));
    const controls = el("div", "eafc-live-controls");
    const statusClass = state.kind === "result" ? "connected" : state.kind === "loading" ? "" : "error";
    const statusText = state.kind === "loading" ? "LOADING" : state.kind === "result" ? "CONNECTED" : "NO DATA";
    controls.appendChild(el("span", `eafc-live-status ${statusClass}`.trim(), statusText));
    const collapseBtn = el("span", "eafc-live-btn", collapsed ? "\u25B4" : "\u25BE");
    collapseBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      collapsed = !collapsed;
      render(state);
    });
    controls.appendChild(collapseBtn);
    const closeBtn = el("span", "eafc-live-btn", "\xD7");
    closeBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      dismissedKey = state.key || null;
      clearPanel();
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
      body.appendChild(el("div", "eafc-live-muted", "Loading EAFC Live intelligence..."));
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
      const message = ERROR_STATUS_TEXT[state.status] || "EAFC Live backend unavailable right now.";
      body.appendChild(el("div", "eafc-live-muted", message));
      body.appendChild(el("div", "eafc-live-muted", "Will retry automatically on the next market update."));
      panel.appendChild(body);
      root.appendChild(panel);
      return;
    }
    const { player, intelligence, combined } = state;
    if (player && (player.name || player.rarity)) {
      const title = [player.name, player.rarity].filter(Boolean).join(" \u2014 ");
      body.appendChild(el("div", "eafc-live-card-name", title));
    }
    if (intelligence.fair_value !== null && intelligence.fair_value !== void 0) {
      addRow(body, "Fair Value", fmt(intelligence.fair_value));
    }
    if (intelligence.buy_below !== null && intelligence.buy_below !== void 0) {
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
    if (combined.potentialProfitAfterTax !== null && combined.potentialProfitAfterTax !== void 0) {
      addRow(body, "Potential Profit", fmt(combined.potentialProfitAfterTax));
    }
    const verdict = verdictLabel(combined.reason, intelligence.signal);
    const verdictEl = el("div", `eafc-live-verdict ${verdict.tone}`);
    verdictEl.textContent = verdict.tone === "confirmed" ? `\u2713 ${verdict.text}` : verdict.text;
    body.appendChild(verdictEl);
    body.appendChild(
      el("div", "eafc-live-muted", "Reference data from EAFC Live \xB7 live listings read from this EA search")
    );
    if (window.EAFC_LIVE_DEBUG && intelligence.source_card_id) {
      body.appendChild(el("div", "eafc-live-debug", `source_card_id: ${intelligence.source_card_id}`));
    }
    panel.appendChild(body);
    root.appendChild(panel);
  }

  // src/lifecycle.js
  var initialized = false;
  function initLifecycle(setup2) {
    if (initialized) return;
    initialized = true;
    setup2();
  }

  // src/main.js
  function log3(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
  }
  var RETRYABLE_STATUSES = /* @__PURE__ */ new Set(["network_error", "server_error", "timeout"]);
  var lastRenderKey = null;
  function renderKeyFor(sourceCardId, auctions) {
    return `${sourceCardId}:${auctions.length}:${cheapestBin(auctions)}`;
  }
  async function handleDetection(result) {
    const { sourceCardId, auctions } = result;
    if (!sourceCardId) {
      return;
    }
    const key = renderKeyFor(sourceCardId, auctions);
    if (key === lastRenderKey) return;
    log3("exact card ID:", sourceCardId);
    render({ kind: "loading", key });
    const intelResult = await getCardIntelligence(sourceCardId);
    if (RETRYABLE_STATUSES.has(intelResult.status)) {
      log3("intelligence request failed:", intelResult.status);
      render({ kind: "error", status: intelResult.status, key });
      return;
    }
    lastRenderKey = key;
    if (intelResult.status === "not_found") {
      render({ kind: "no-intelligence", key });
      return;
    }
    const intelligence = intelResult.data;
    const combined = combineIntelligence(auctions, intelligence);
    log3("intelligence loaded, signal:", intelligence.signal);
    log3("cheapest BIN:", combined.cheapestBin);
    log3(`${combined.underBuyBelowCount} listing(s) under target`);
    render({
      kind: "result",
      key,
      player: {
        name: intelligence.name,
        rarity: intelligence.rarity,
        rating: intelligence.rating
      },
      intelligence,
      combined
    });
  }
  function setup() {
    onCardDetected(handleDetection);
    installMarketObserver();
    console.log("[EAFC Live] Companion userscript ready", { href: location.href });
  }
  initLifecycle(setup);
})();
