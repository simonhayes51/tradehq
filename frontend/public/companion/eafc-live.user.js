// ==UserScript==
// @name         EAFC.Live Companion
// @namespace    https://app.eafc.live/
// @version      1.9.0
// @description  Read-only EAFC.Live trading intelligence overlay for the EA SPORTS FC 26 Web App Transfer Market. Never buys, bids, lists, or automates anything - observation only.
// @author       EAFC.Live
// @match        https://www.ea.com/*/ea-sports-fc/ultimate-team/web-app/*
// @match        https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*
// @connect      api.eafc.live
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        GM.xmlHttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM.getValue
// @grant        GM.setValue
// @run-at       document-start
// @downloadURL  https://app.eafc.live/companion/eafc-live.user.js
// @updateURL    https://app.eafc.live/companion/eafc-live.user.js
// ==/UserScript==

(() => {
  // src/card-detector.js
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

  // page-hook-source-ns:virtual:page-hook-source
  var virtual_page_hook_source_default = '(() => {\n  // src/card-detector.js\n  function toPositiveInt(value) {\n    const n = Number(value);\n    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;\n  }\n  function extractExactCardId(itemData) {\n    if (!itemData || typeof itemData !== "object") return null;\n    return toPositiveInt(itemData.resourceId) || toPositiveInt(itemData.definitionId) || null;\n  }\n  function normalizeAuction(entry) {\n    if (!entry || typeof entry !== "object") return null;\n    const item = entry.itemData || entry.item || {};\n    const sourceCardId = extractExactCardId(item);\n    if (sourceCardId === null) return null;\n    return {\n      sourceCardId,\n      tradeId: toPositiveInt(entry.tradeId ?? item.id) ?? null,\n      rating: Number.isFinite(Number(item.rating)) ? Number(item.rating) : null,\n      buyNowPrice: toPositiveInt(entry.buyNowPrice) ?? 0,\n      currentBid: toPositiveInt(entry.currentBid) ?? 0,\n      startingBid: toPositiveInt(entry.startingBid) ?? 0,\n      // Seconds remaining, as EA reports it. 0/undefined is treated as\n      // "unknown", never as "expired now".\n      expires: toPositiveInt(entry.expires),\n      tradeState: typeof entry.tradeState === "string" ? entry.tradeState : null\n    };\n  }\n  function extractAuctionsFromResponse(body) {\n    if (!body || typeof body !== "object") return [];\n    let rawList;\n    if (Array.isArray(body.auctionInfo)) {\n      rawList = body.auctionInfo;\n    } else if (Array.isArray(body)) {\n      rawList = body;\n    } else if (body.itemData || body.buyNowPrice !== void 0) {\n      rawList = [body];\n    } else {\n      rawList = [];\n    }\n    const out = [];\n    for (const entry of rawList) {\n      const normalized = normalizeAuction(entry);\n      if (normalized) out.push(normalized);\n    }\n    return out;\n  }\n\n  // src/page-hook.js\n  var FC26_RE = /\\/ut\\/game\\/fc26(\\/|$)/;\n  var TRANSFERMARKET_RE = /\\/transfermarket(\\/|$|\\?)/;\n  var EVENT_NAME = "eafc-live:transfermarket";\n  function log(...args) {\n    if (window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);\n  }\n  function dispatchSanitizedAuctions(url, bodyText) {\n    let body = null;\n    try {\n      body = bodyText ? JSON.parse(bodyText) : null;\n    } catch (_err) {\n      return;\n    }\n    let auctions;\n    try {\n      auctions = extractAuctionsFromResponse(body);\n    } catch (err) {\n      log("card extraction failed in page context, dropping response:", err && err.message);\n      return;\n    }\n    if (!auctions || auctions.length === 0) return;\n    try {\n      window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { url, auctions } }));\n    } catch (err) {\n      log("failed to dispatch sanitized market event:", err && err.message);\n    }\n  }\n  (function installPageHook() {\n    const originalFetch = window.fetch;\n    if (typeof originalFetch === "function") {\n      window.fetch = function eafcLiveFetch(input, init) {\n        const promise = originalFetch.apply(this, arguments);\n        try {\n          const url = typeof input === "string" ? input : input && input.url;\n          if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {\n            promise.then((response) => {\n              response.clone().text().then((text) => dispatchSanitizedAuctions(url, text)).catch(() => {\n              });\n            }).catch(() => {\n            });\n          }\n        } catch (err) {\n          log("fetch hook observation failed (request unaffected):", err && err.message);\n        }\n        return promise;\n      };\n    }\n    const OriginalXHR = window.XMLHttpRequest;\n    if (OriginalXHR) {\n      const originalOpen = OriginalXHR.prototype.open;\n      const originalSend = OriginalXHR.prototype.send;\n      OriginalXHR.prototype.open = function eafcLiveOpen(method, url) {\n        try {\n          this.__eafcLiveUrl = String(url);\n        } catch (_err) {\n          this.__eafcLiveUrl = null;\n        }\n        return originalOpen.apply(this, arguments);\n      };\n      OriginalXHR.prototype.send = function eafcLiveSend() {\n        const url = this.__eafcLiveUrl;\n        if (url && FC26_RE.test(url) && TRANSFERMARKET_RE.test(url)) {\n          this.addEventListener("loadend", function() {\n            try {\n              dispatchSanitizedAuctions(url, this.responseText);\n            } catch (err) {\n              log("XHR hook observation failed (request unaffected):", err && err.message);\n            }\n          });\n        }\n        return originalSend.apply(this, arguments);\n      };\n    }\n    console.log("[EAFC.Live] page hook installed - observing window.fetch/XHR for /ut/game/fc26 responses", {\n      href: location.href,\n      inIframe: window !== window.top\n    });\n  })();\n})();\n';

  // src/market-observer.js
  var EVENT_NAME = "eafc-live:transfermarket";
  function log(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }
  var onDetected = null;
  var installed = false;
  function onCardDetected(callback) {
    onDetected = callback;
  }
  function handleSanitizedAuctions(url, auctions) {
    if (!Array.isArray(auctions) || auctions.length === 0) return;
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
    window.addEventListener(EVENT_NAME, (event) => {
      const detail = event && event.detail;
      if (!detail) return;
      handleSanitizedAuctions(detail.url, detail.auctions);
    });
    try {
      const script = document.createElement("script");
      script.textContent = virtual_page_hook_source_default;
      (document.head || document.documentElement).appendChild(script);
      script.remove();
    } catch (err) {
      console.error("[EAFC.Live] failed to inject page hook - market observation disabled:", err);
    }
  }

  // src/config.js
  var CONFIG = {
    // Base URL of the EAFC.Live backend's v2 API
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
    // backend can never hang the overlay or the page. This is a single
    // fast per-card lookup, so a short timeout is correct here.
    REQUEST_TIMEOUT_MS: 6e3,
    // Network timeout for GET /opportunities specifically. That endpoint
    // is backed by the same segmented candidate scan + evaluate_card()
    // pass documented across backend/app/routers/v2/futgg_market.py - on a
    // cold in-process cache it can legitimately take 10-20+ seconds, the
    // same reasoning behind the website frontend's own
    // SCAN_REQUEST_OPTS vs REQUEST_OPTS split (see
    // frontend/src/v2/hooks/useFutggMarket.js). Reusing REQUEST_TIMEOUT_MS
    // for this endpoint made the nav-tab panel time out and report "Could
    // not load opportunities right now" well before the backend was
    // actually done - the panel just never showed trades.
    SCAN_REQUEST_TIMEOUT_MS: 2e4
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
  function getCompanionToken() {
    try {
      let stored = null;
      if (typeof GM_getValue === "function") {
        stored = GM_getValue("companionToken", null);
      } else if (typeof GM !== "undefined" && GM && typeof GM.getValue === "function") {
        stored = globalThis.__EAFC_LIVE_TOKEN__ || null;
      }
      if (!stored) return "";
      const entry = typeof stored === "string" ? JSON.parse(stored) : stored;
      if (!entry || !entry.token) return "";
      if (entry.expiresAt && entry.expiresAt * 1e3 <= Date.now()) return "";
      return entry.token;
    } catch {
      return "";
    }
  }
  function log2(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
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
      const settleReject = (kind, detail) => {
        if (settled) return;
        settled = true;
        reject({ kind, detail });
      };
      let maybePromise;
      try {
        maybePromise = gmRequest({
          ...details,
          onload: settleResolve,
          onerror: (err) => settleReject("network_error", `onerror: ${describeGmError(err)}`),
          ontimeout: () => settleReject("timeout", "ontimeout fired"),
          onabort: () => settleReject("network_error", "onabort fired")
        });
      } catch (err) {
        settleReject("network_error", `gmRequest() threw: ${err && err.message}`);
        return;
      }
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise.then(settleResolve).catch((err) => settleReject("network_error", `promise rejected: ${describeGmError(err)}`));
      }
    });
  }
  function describeGmError(err) {
    if (!err) return "no error info given";
    if (typeof err === "string") return err;
    if (err.error || err.message || err.statusText) {
      return String(err.error || err.message || err.statusText);
    }
    try {
      return JSON.stringify(err).slice(0, 120);
    } catch (_e) {
      return String(err);
    }
  }
  async function getJson(url, label, timeoutMs = CONFIG.REQUEST_TIMEOUT_MS) {
    const gmRequest = resolveGMRequest();
    if (!gmRequest) {
      const detail = "no GM_xmlhttpRequest / GM.xmlHttpRequest in this manager";
      log2(detail);
      return { status: "network_error", data: null, detail };
    }
    let response;
    try {
      response = await callGM(gmRequest, {
        method: "GET",
        url,
        timeout: timeoutMs,
        headers: {
          Accept: "application/json",
          // The bearer token is the RELIABLE path. withCredentials below is
          // the convenient one, and it is the one that cannot be trusted:
          // support for that flag differs BETWEEN userscript managers, and
          // this file already documents one live case where two managers
          // disagreed about a GM API (see the GM.xmlHttpRequest note above).
          // A header cannot be declined by any of them.
          ...getCompanionToken() ? { Authorization: `Bearer ${getCompanionToken()}` } : {}
        },
        // Sends the EAFC.Live session cookie so an Elite member's overlay
        // shows their trade plan. The companion endpoint used to be
        // ungated, so this request being anonymous still returned the full
        // plan; now an anonymous request gets the locked payload instead,
        // which the overlay renders as a prompt rather than as an error.
        // Never the EA session - this is a cross-origin request to
        // api.eafc.live, not to EA.
        withCredentials: true
      });
    } catch (err) {
      const detail = err && err.detail || "unknown request failure";
      if (err && err.kind === "timeout") {
        log2(`${label} request timed out`, detail);
        return { status: "timeout", data: null, detail };
      }
      log2(`${label} request failed`, detail);
      return { status: "network_error", data: null, detail };
    }
    const status = response.status;
    if (status === 0) {
      const detail = `status 0 (request blocked before reaching the network - likely missing @connect approval or a manager-level permission)`;
      log2(detail);
      return { status: "network_error", data: null, detail };
    }
    if (status === 404) {
      log2(`${label}: not found`);
      return { status: "not_found", data: null };
    }
    if (status >= 500 || !(status >= 200 && status < 300)) {
      const detail = `HTTP ${status}`;
      log2(`${label} unexpected status`, status);
      return { status: "server_error", data: null, detail };
    }
    try {
      const data = JSON.parse(response.responseText);
      log2(`${label} loaded`);
      return { status: "success", data };
    } catch (err) {
      const detail = `unparsable JSON: ${err && err.message}`;
      log2(`${label} returned unparsable JSON:`, err && err.message);
      return { status: "server_error", data: null, detail };
    }
  }
  function fetchIntelligence(sourceCardId) {
    return getJson(`${CONFIG.API_BASE_URL}/companion/card/${sourceCardId}`, `companion card ${sourceCardId}`);
  }
  var cache = createIntelligenceCache(fetchIntelligence, {
    successTtlMs: CONFIG.SUCCESS_CACHE_TTL_MS,
    notFoundTtlMs: CONFIG.NOT_FOUND_CACHE_TTL_MS
  });
  function getCardIntelligence(sourceCardId) {
    return cache.get(sourceCardId);
  }
  function getOpportunities(pageSize = 20) {
    return getJson(
      `${CONFIG.API_BASE_URL}/opportunities?page_size=${pageSize}`,
      "opportunities feed",
      CONFIG.SCAN_REQUEST_TIMEOUT_MS
    );
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
        return { text: "NO EAFC.LIVE INTELLIGENCE", tone: "neutral" };
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

  // src/nav-tab.js
  var NUMBER_FORMAT = new Intl.NumberFormat("en-US");
  function fmt(n) {
    return n === null || n === void 0 ? "-" : NUMBER_FORMAT.format(n);
  }
  function log3(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }
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
  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const CHUNK = 32768;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary);
  }
  function sniffImageMimeType(buffer) {
    const b = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 12));
    if (b.length >= 8 && b[0] === 137 && b[1] === 80 && b[2] === 78 && b[3] === 71) return "image/png";
    if (b.length >= 3 && b[0] === 255 && b[1] === 216 && b[2] === 255) return "image/jpeg";
    if (b.length >= 12 && b[0] === 82 && b[1] === 73 && b[2] === 70 && b[3] === 70 && b[8] === 87 && b[9] === 69 && b[10] === 66 && b[11] === 80) {
      return "image/webp";
    }
    if (b.length >= 6 && b[0] === 71 && b[1] === 73 && b[2] === 70) return "image/gif";
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
          ontimeout: () => resolve({ url: null, detail: "ontimeout fired" })
        });
        if (maybePromise && typeof maybePromise.then === "function") {
          maybePromise.then((response) => resolve(toResult(response))).catch((err) => resolve({ url: null, detail: `promise rejected: ${describeError(err)}` }));
        }
      } catch (err) {
        resolve({ url: null, detail: `gmRequest() threw: ${describeError(err)}` });
      }
    });
  }
  var KNOWN_TAB_LABELS = ["home", "squads", "transfers"];
  function* allElements(root) {
    for (const el3 of root.querySelectorAll("*")) {
      yield el3;
      if (el3.shadowRoot) yield* allElements(el3.shadowRoot);
    }
  }
  function normalizedText(el3) {
    return (el3.textContent || "").trim().toLowerCase();
  }
  var diagnostic = "not run yet";
  function findNavBar() {
    let scanned = 0;
    let bestMatch = null;
    let bestMatchCount = 0;
    for (const el3 of allElements(document.documentElement)) {
      scanned++;
      if (el3.children.length < KNOWN_TAB_LABELS.length) continue;
      const childTexts = Array.from(el3.children).map(normalizedText);
      const matched = KNOWN_TAB_LABELS.filter((label) => childTexts.some((t) => t === label));
      if (matched.length === KNOWN_TAB_LABELS.length) {
        diagnostic = `found (scanned ${scanned} elements)`;
        return el3;
      }
      if (matched.length > bestMatchCount) {
        bestMatchCount = matched.length;
        bestMatch = childTexts.filter((t) => t).slice(0, 6);
      }
    }
    diagnostic = bestMatchCount > 0 ? `not found (scanned ${scanned}; closest had ${bestMatchCount}/${KNOWN_TAB_LABELS.length} labels: [${bestMatch.join(", ")}])` : `not found (scanned ${scanned}; no candidate matched any label - possibly inside a closed shadow root)`;
    return null;
  }
  function getNavTabDiagnostic() {
    return diagnostic;
  }
  var injected = false;
  var panelHostEl = null;
  var panelShadow = null;
  var tabEl = null;
  var cachedNavBar = null;
  var PANEL_STYLES = `
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
    if (text !== void 0) node.textContent = text;
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
    const shadow2 = ensurePanelMounted();
    const existing = shadow2.querySelector(".eafc-live-full-panel");
    if (existing) existing.remove();
    const panel = el("div", "eafc-live-full-panel");
    if (cachedNavBar) {
      const barHeight = cachedNavBar.getBoundingClientRect().height;
      if (barHeight > 0) panel.style.bottom = `${Math.round(barHeight)}px`;
    }
    const header = el("div", "eafc-live-full-header");
    header.appendChild(el("span", "eafc-live-full-title", "EAFC.LIVE OPPORTUNITIES"));
    const closeBtn = el("span", "eafc-live-full-close", "\xD7");
    closeBtn.addEventListener("click", closePanel);
    header.appendChild(closeBtn);
    panel.appendChild(header);
    const body = el("div", "eafc-live-full-body");
    body.appendChild(el("div", "eafc-live-spinner"));
    body.appendChild(el("div", "eafc-live-muted", "Loading live opportunities\u2026"));
    panel.appendChild(body);
    shadow2.appendChild(panel);
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
    const artStats = { withUrl: 0, loaded: 0, failed: 0, total: items.length, firstFailureDetail: null };
    items.forEach((item, index) => {
      const card = el("div", "eafc-live-opp-card");
      card.style.animationDelay = `${Math.min(index, 10) * 30}ms`;
      const artUrl = item.composited_image_url || item.image_url;
      const artPlaceholder = el("div", "eafc-live-opp-art-placeholder", "\u26BD");
      card.appendChild(artPlaceholder);
      if (artUrl) {
        artStats.withUrl++;
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
            artStats.failed++;
            if (!artStats.firstFailureDetail) {
              artStats.firstFailureDetail = "fetched OK, correct MIME type, but the browser still could not decode it";
            }
            img.replaceWith(el("div", "eafc-live-opp-art-placeholder", "\u26BD"));
          };
          artPlaceholder.replaceWith(img);
        });
      }
      const main = el("div", "eafc-live-opp-main");
      main.appendChild(el("div", "eafc-live-opp-name", [item.name, item.rarity].filter(Boolean).join(" \u2014 ")));
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
        "eafc-live-opp-signal",
        String(item.signal || "buy").replace(/_/g, " ").toUpperCase()
      );
      main.appendChild(signal);
      card.appendChild(main);
      body.appendChild(card);
    });
    setTimeout(() => {
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
  var TAB_CSS = `
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
  function equalizeTabWidths(navBar) {
    for (const child of navBar.children) {
      child.style.flex = "1 1 0%";
      child.style.minWidth = "0";
    }
  }
  function installNavTab() {
    if (injected) return true;
    const navBar = findNavBar();
    if (!navBar) return false;
    injected = true;
    cachedNavBar = navBar;
    const tab = document.createElement("div");
    tab.className = "eafc-live-nav-tab";
    tab.setAttribute("role", "button");
    tab.setAttribute("aria-label", "EAFC.Live opportunities");
    tab.style.pointerEvents = "auto";
    const icon = el("span", "eafc-live-nav-tab-icon", "\u26A1");
    const label = el("span", "eafc-live-nav-tab-label", "LIVE");
    tab.appendChild(icon);
    tab.appendChild(label);
    const styleEl = document.createElement("style");
    styleEl.textContent = TAB_CSS;
    document.head.appendChild(styleEl);
    let open = false;
    let lastToggleAt = 0;
    const toggle = () => {
      const now = Date.now();
      if (now - lastToggleAt < 400) return;
      lastToggleAt = now;
      open = !open;
      if (open) openPanel();
      else closePanel();
    };
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
    const maybeCloseOnOutsideTap = (event) => {
      if (event.target === tab || tab.contains(event.target)) return;
      if (panelHostEl && event.target === panelHostEl) return;
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
    const keepTabPinned = new MutationObserver(() => {
      if (navBar.lastElementChild !== tab) {
        navBar.appendChild(tab);
        equalizeTabWidths(navBar);
      }
    });
    keepTabPinned.observe(navBar, { childList: true });
    log3("EAFC.LIVE nav tab installed");
    return true;
  }

  // src/overlay.js
  var NUMBER_FORMAT2 = new Intl.NumberFormat("en-US");
  function fmt2(n) {
    return n === null || n === void 0 ? "-" : NUMBER_FORMAT2.format(n);
  }
  function fmtExpiry(seconds) {
    if (seconds === null || seconds === void 0) return null;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  var ERROR_STATUS_TEXT = {
    network_error: "Backend unreachable.",
    server_error: "Backend error.",
    timeout: "Backend timed out."
  };
  var STYLES = `
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
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    color: #f3f3f2;
    background: #111214;
    border: 1px solid #222327;
    border-radius: 4px;
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
    background: #17181b;
    border-bottom: 1px solid #222327;
    border-radius: 4px 4px 0 0;
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
    color: #39ff8a;
  }
  .eafc-live-status { font-size: 10px; color: #8d8f95; }
  .eafc-live-status.connected { color: #39ff8a; }
  .eafc-live-status.error { color: #ff5c5c; }
  .eafc-live-body { padding: 10px 12px 14px; }
  .eafc-live-card-name { font-weight: 600; margin-bottom: 6px; color: #f3f3f2; }
  .eafc-live-row { display: flex; justify-content: space-between; padding: 3px 0; color: #a2a2a2; }
  .eafc-live-row .label { color: #8d8f95; }
  .eafc-live-row .value { font-variant-numeric: tabular-nums; color: #f3f3f2; }
  .eafc-live-divider { height: 1px; background: #222327; margin: 8px 0; }
  .eafc-live-verdict {
    margin-top: 8px;
    padding: 8px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 12px;
    text-align: center;
  }
  .eafc-live-verdict.confirmed { background: rgba(57, 255, 138, 0.14); color: #39ff8a; }
  .eafc-live-verdict.neutral { background: rgba(141, 143, 149, 0.12); color: #a2a2a2; }
  .eafc-live-muted { color: #8d8f95; font-size: 11px; margin-top: 4px; }
  .eafc-live-debug { color: #5c5e64; font-size: 10px; margin-top: 6px; font-family: 'JetBrains Mono', monospace; }
  .eafc-live-controls { display: flex; align-items: center; gap: 4px; }
  .eafc-live-btn {
    cursor: pointer;
    color: #8d8f95;
    font-size: 16px;
    line-height: 1;
    min-width: 32px;
    min-height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
  }
  .eafc-live-btn:hover, .eafc-live-btn:active { color: #f3f3f2; background: rgba(255,255,255,0.06); }
`;
  var hostEl = null;
  var shadow = null;
  var collapsed = true;
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
  function el2(tag, className, text) {
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
    const row = el2("div", "eafc-live-row");
    row.appendChild(el2("span", "label", label));
    row.appendChild(el2("span", "value", value));
    body.appendChild(row);
  }
  function render(state) {
    if (!state || state.kind === "hidden") {
      clearPanel();
      return;
    }
    const root = ensureMounted();
    clearPanel();
    const panel = el2("div", `eafc-live-panel${collapsed ? " collapsed" : ""}`);
    const header = el2("div", "eafc-live-header");
    header.appendChild(el2("span", "eafc-live-brand", "EAFC.LIVE"));
    const statusClass = state.kind === "result" ? "connected" : state.kind === "loading" || state.kind === "watching" ? "" : "error";
    const statusText = state.kind === "loading" ? "LOADING" : state.kind === "result" ? "CONNECTED" : state.kind === "watching" ? "WATCHING" : "NO DATA";
    const collapsedIcon = el2("span", "eafc-live-collapsed-icon", "\u26A1");
    collapsedIcon.style.color = statusClass === "connected" ? "#39ff8a" : statusClass === "error" ? "#ff5c5c" : "#8d8f95";
    header.appendChild(collapsedIcon);
    const controls = el2("div", "eafc-live-controls");
    controls.appendChild(el2("span", `eafc-live-status ${statusClass}`.trim(), statusText));
    const collapseBtn = el2("span", "eafc-live-btn", collapsed ? "\u25B4" : "\u25BE");
    collapseBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      collapsed = !collapsed;
      render(state);
    });
    controls.appendChild(collapseBtn);
    const closeBtn = el2("span", "eafc-live-btn", "\xD7");
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
    const body = el2("div", "eafc-live-body");
    const finish = () => {
      if (!collapsed) {
        body.appendChild(el2("div", "eafc-live-debug", `nav tab: ${getNavTabDiagnostic()}`));
      }
      panel.appendChild(body);
      root.appendChild(panel);
    };
    if (state.kind === "watching") {
      const text = state.cardsChecked == null ? "Watching this search." : state.badgeCount > 0 ? `${state.cardsChecked} card${state.cardsChecked === 1 ? "" : "s"} checked, ${state.badgeCount} flagged.` : `${state.cardsChecked} card${state.cardsChecked === 1 ? "" : "s"} checked, nothing actionable right now.`;
      body.appendChild(el2("div", "eafc-live-muted", text));
      if (!collapsed && state.badgeDiagnostic && state.badgeCount > 0) {
        body.appendChild(el2("div", "eafc-live-debug", state.badgeDiagnostic));
      }
      finish();
      return;
    }
    if (state.kind === "loading") {
      body.appendChild(el2("div", "eafc-live-muted", "Loading\u2026"));
      finish();
      return;
    }
    if (state.kind === "no-intelligence") {
      body.appendChild(el2("div", "eafc-live-muted", "No data for this card."));
      finish();
      return;
    }
    if (state.kind === "error") {
      const message = ERROR_STATUS_TEXT[state.status] || "Backend unavailable.";
      body.appendChild(el2("div", "eafc-live-muted", message));
      if (state.detail) {
        body.appendChild(el2("div", "eafc-live-debug", state.detail));
      }
      finish();
      return;
    }
    const { player, intelligence, combined } = state;
    if (player && (player.name || player.rarity)) {
      const title = [player.name, player.rarity].filter(Boolean).join(" \u2014 ");
      body.appendChild(el2("div", "eafc-live-card-name", title));
    }
    if (intelligence.fair_value !== null && intelligence.fair_value !== void 0) {
      addRow(body, "Fair Value", fmt2(intelligence.fair_value));
    }
    if (intelligence.buy_below !== null && intelligence.buy_below !== void 0) {
      addRow(body, "Buy Below", fmt2(intelligence.buy_below));
    }
    addRow(body, "Signal", (intelligence.signal || "unknown").replace(/_/g, " ").toUpperCase());
    body.appendChild(el2("div", "eafc-live-divider"));
    addRow(body, "Live Cheapest", combined.cheapestBin !== null ? fmt2(combined.cheapestBin) : "no listings");
    addRow(body, "Live Listings", String(combined.listingCount));
    if (combined.hasIntelligence) {
      addRow(body, "Below Buy Target", String(combined.underBuyBelowCount));
    }
    const expiry = fmtExpiry(combined.nearestExpirySeconds);
    if (expiry) addRow(body, "Nearest Expiry", expiry);
    if (combined.potentialProfitAfterTax !== null && combined.potentialProfitAfterTax !== void 0) {
      addRow(body, "Potential Profit", fmt2(combined.potentialProfitAfterTax));
    }
    const verdict = verdictLabel(combined.reason, intelligence.signal);
    const verdictEl = el2("div", `eafc-live-verdict ${verdict.tone}`);
    verdictEl.textContent = verdict.tone === "confirmed" ? `\u2713 ${verdict.text}` : verdict.text;
    body.appendChild(verdictEl);
    if (window.EAFC_LIVE_DEBUG && intelligence.source_card_id) {
      body.appendChild(el2("div", "eafc-live-debug", `source_card_id: ${intelligence.source_card_id}`));
    }
    finish();
  }

  // src/list-badges.js
  var NUMBER_FORMAT3 = new Intl.NumberFormat("en-US");
  var VERIFY_INTERVAL_MS = 500;
  var badges = /* @__PURE__ */ new Map();
  var verifyTimer = null;
  var lastRowDiagnostic = "no badge inserted yet";
  function log4(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }
  function getListBadgesDiagnostic() {
    return lastRowDiagnostic;
  }
  function auctionKey(auction) {
    return auction.tradeId != null ? `t:${auction.tradeId}` : `c:${auction.sourceCardId}:${auction.buyNowPrice}`;
  }
  function findPriceAnchor(priceText) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node2) {
        return node2.nodeValue && node2.nodeValue.trim() === priceText ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    const candidates = [];
    let node;
    while (node = walker.nextNode()) candidates.push(node);
    if (candidates.length === 0) return null;
    for (const candidate of candidates) {
      let el3 = candidate.parentElement;
      for (let depth = 0; el3 && depth < 6; depth++, el3 = el3.parentElement) {
        const text = el3.textContent || "";
        if (/buy\s*now/i.test(text)) return candidate;
      }
    }
    return candidates[0];
  }
  var MAX_PLAUSIBLE_ROW_HEIGHT_PX = 320;
  function findRowContainer(anchorNode) {
    let el3 = anchorNode.parentElement;
    let best = el3;
    for (let depth = 0; el3 && depth < 14; depth++, el3 = el3.parentElement) {
      const rect = el3.getBoundingClientRect();
      if (rect.height <= 0) continue;
      if (rect.height > MAX_PLAUSIBLE_ROW_HEIGHT_PX) break;
      best = el3;
    }
    return best;
  }
  function verdictFor(auction, intelligence) {
    if (!intelligence) return null;
    const combined = combineIntelligence([auction], intelligence);
    if (combined.reason === "confirmed") return { tone: "buy", label: "BUY" };
    if (intelligence.signal === "avoid") return { tone: "avoid", label: "AVOID" };
    return null;
  }
  function makeBadgeEl(verdict, onClick) {
    const el3 = document.createElement("button");
    el3.type = "button";
    el3.textContent = verdict.label === "BUY" ? "\u2713 BUY" : "\u26A0 AVOID";
    const bg = verdict.tone === "buy" ? "rgba(57, 255, 138, 0.14)" : "rgba(255, 92, 92, 0.14)";
    const fg = verdict.tone === "buy" ? "#39ff8a" : "#ff5c5c";
    const border = verdict.tone === "buy" ? "#39ff8a" : "#ff5c5c";
    el3.style.cssText = [
      "position:absolute",
      "top:6px",
      "right:6px",
      "z-index:2147483645",
      "display:inline-flex",
      "align-items:center",
      "pointer-events:auto",
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif",
      "font-size:10px",
      "font-weight:700",
      "letter-spacing:0.02em",
      "padding:3px 6px",
      "border-radius:4px",
      "cursor:pointer",
      "line-height:1",
      "white-space:nowrap",
      `background:${bg}`,
      `color:${fg}`,
      `border:1px solid ${border}`,
      "box-shadow:0 2px 6px rgba(0,0,0,0.35)"
    ].join(";");
    el3.addEventListener("click", (event) => {
      event.stopPropagation();
      event.preventDefault();
      onClick();
    });
    return el3;
  }
  var MAX_MISSING_TICKS = 8;
  function verifyBadge(entry) {
    const { anchorNode, priceText } = entry;
    const stillValid = anchorNode && anchorNode.parentElement && anchorNode.parentElement.isConnected && anchorNode.nodeValue != null && anchorNode.nodeValue.trim() === priceText;
    if (stillValid) {
      entry.missingTicks = 0;
      return;
    }
    const freshAnchor = findPriceAnchor(priceText);
    if (freshAnchor) {
      const row = findRowContainer(freshAnchor);
      if (getComputedStyle(row).position === "static") row.style.position = "relative";
      row.appendChild(entry.el);
      entry.anchorNode = freshAnchor;
      entry.missingTicks = 0;
      return;
    }
    entry.missingTicks = (entry.missingTicks || 0) + 1;
  }
  function verifyAll() {
    for (const [key, entry] of badges) {
      verifyBadge(entry);
      if (entry.missingTicks >= MAX_MISSING_TICKS) {
        entry.el.remove();
        badges.delete(key);
      }
    }
  }
  function startVerifyLoop() {
    if (verifyTimer) return;
    verifyTimer = setInterval(verifyAll, VERIFY_INTERVAL_MS);
  }
  function openOverlayFor(auction, sameCardAuctions, intelligence) {
    const combined = combineIntelligence(sameCardAuctions, intelligence);
    render({
      kind: "result",
      key: `list-badge:${auction.sourceCardId}:${Date.now()}`,
      player: { name: intelligence.name, rarity: intelligence.rarity, rating: intelligence.rating },
      intelligence,
      combined
    });
  }
  async function renderListBadges(auctions) {
    if (!Array.isArray(auctions) || auctions.length === 0) return { cardsChecked: 0, badgeCount: 0 };
    startVerifyLoop();
    const byCard = /* @__PURE__ */ new Map();
    for (const auction of auctions) {
      if (!byCard.has(auction.sourceCardId)) byCard.set(auction.sourceCardId, []);
      byCard.get(auction.sourceCardId).push(auction);
    }
    await Promise.all(
      [...byCard.keys()].map(async (cardId) => {
        const intelResult = await getCardIntelligence(cardId);
        if (intelResult.status !== "success") return;
        const intelligence = intelResult.data;
        const sameCardAuctions = byCard.get(cardId);
        for (const auction of sameCardAuctions) {
          const key = auctionKey(auction);
          if (badges.has(key)) continue;
          const verdict = verdictFor(auction, intelligence);
          if (!verdict) continue;
          const priceText = NUMBER_FORMAT3.format(auction.buyNowPrice);
          const anchorNode = findPriceAnchor(priceText);
          if (!anchorNode) {
            log4("list-badges: no DOM match for price", priceText, "- skipping this row's badge");
            continue;
          }
          const row = findRowContainer(anchorNode);
          if (getComputedStyle(row).position === "static") row.style.position = "relative";
          const el3 = makeBadgeEl(verdict, () => openOverlayFor(auction, sameCardAuctions, intelligence));
          row.appendChild(el3);
          const entry = { el: el3, auction, anchorNode, priceText, verdict, missingTicks: 0 };
          badges.set(key, entry);
          const rowRect = row.getBoundingClientRect();
          lastRowDiagnostic = `row: ${Math.round(rowRect.width)}x${Math.round(rowRect.height)}px @(${Math.round(rowRect.left)},${Math.round(rowRect.top)})`;
        }
      })
    );
    return { cardsChecked: byCard.size, badgeCount: badges.size };
  }

  // src/lifecycle.js
  var initialized = false;
  function initLifecycle(setup2) {
    if (initialized) return;
    initialized = true;
    setup2();
  }

  // src/main.js
  function log5(...args) {
    if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
  }
  var RETRYABLE_STATUSES = /* @__PURE__ */ new Set(["network_error", "server_error", "timeout"]);
  var lastRenderKey = null;
  function renderKeyFor(sourceCardId, auctions) {
    return `${sourceCardId}:${auctions.length}:${cheapestBin(auctions)}`;
  }
  async function handleDetection(result) {
    const { sourceCardId, auctions } = result;
    const badgeResult = renderListBadges(auctions).catch((err) => {
      log5("list badge rendering failed:", err && err.message);
      return null;
    });
    if (!sourceCardId) {
      const badges2 = await badgeResult;
      render({
        kind: "watching",
        key: `watching:${auctions.length}`,
        cardsChecked: badges2 ? badges2.cardsChecked : null,
        badgeCount: badges2 ? badges2.badgeCount : null,
        badgeDiagnostic: getListBadgesDiagnostic()
      });
      return;
    }
    const key = renderKeyFor(sourceCardId, auctions);
    if (key === lastRenderKey) return;
    log5("exact card ID:", sourceCardId);
    render({ kind: "loading", key });
    const intelResult = await getCardIntelligence(sourceCardId);
    if (RETRYABLE_STATUSES.has(intelResult.status)) {
      log5("intelligence request failed:", intelResult.status, intelResult.detail);
      render({ kind: "error", status: intelResult.status, detail: intelResult.detail, key });
      return;
    }
    lastRenderKey = key;
    if (intelResult.status === "not_found") {
      render({ kind: "no-intelligence", key });
      return;
    }
    const intelligence = intelResult.data;
    const combined = combineIntelligence(auctions, intelligence);
    log5("intelligence loaded, signal:", intelligence.signal);
    log5("cheapest BIN:", combined.cheapestBin);
    log5(`${combined.underBuyBelowCount} listing(s) under target`);
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
  function pollForNavTab() {
    const timer = setInterval(() => {
      if (installNavTab()) clearInterval(timer);
    }, 2e3);
    setTimeout(() => clearInterval(timer), 5 * 60 * 1e3);
  }
  function setup() {
    onCardDetected(handleDetection);
    installMarketObserver();
    pollForNavTab();
    console.log("[EAFC.Live] Companion userscript ready", { href: location.href });
  }
  initLifecycle(setup);
})();
