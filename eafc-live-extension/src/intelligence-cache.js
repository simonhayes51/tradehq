// src/intelligence-cache.js
//
// Pure caching/retry policy in front of a network fetch function. No
// DOM, no `fetch`, no extension APIs - unit-testable directly under Node
// (see tests/intelligence-cache.test.js) with a fake `fetchFn`. The only
// browser-specific piece (src/background.js) is a thin wrapper that
// supplies a real `fetchFn` and forwards chrome.runtime messages here.
//
// WHY THIS EXISTS SEPARATELY FROM background.js
// -------------------------------------------------------------------
// The original V1 cached every result - including network errors and
// backend 5xxs - for the full success TTL, which meant a single
// transient backend hiccup could suppress a real recommendation from
// ever being retried for minutes, on a market state that hadn't changed
// at all. Splitting the policy out into a pure module makes that
// behaviour a small, directly-testable contract instead of something
// buried inside async chrome.runtime plumbing:
//
//   - "success"        -> cached for successTtlMs.
//   - "not_found"       -> cached for notFoundTtlMs (short - a real fact
//                          about our catalogue, but not a recommendation
//                          that deserves a multi-minute shelf life).
//   - "network_error",
//     "server_error",
//     "timeout"         -> NEVER cached. The next call for the same card
//                          re-hits fetchFn, so a temporary outage cannot
//                          lock a card out of ever showing a result.
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.EAFCLive = root.EAFCLive || {};
    Object.assign(root.EAFCLive, factory());
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const RETRYABLE_STATUSES = ["network_error", "server_error", "timeout"];
  const CACHEABLE_STATUSES = ["success", "not_found"];

  /**
   * @param {(sourceCardId: number) => Promise<{status: string, data: any}>} fetchFn
   * @param {{successTtlMs?: number, notFoundTtlMs?: number, now?: () => number}} [options]
   */
  function createIntelligenceCache(fetchFn, options) {
    if (typeof fetchFn !== "function") {
      throw new TypeError("createIntelligenceCache requires a fetchFn");
    }
    const opts = options || {};
    const successTtlMs = opts.successTtlMs ?? 5 * 60 * 1000;
    const notFoundTtlMs = opts.notFoundTtlMs ?? 60 * 1000;
    const now = typeof opts.now === "function" ? opts.now : () => Date.now();

    // sourceCardId -> { expiresAt, result }
    const cache = new Map();
    // sourceCardId -> in-flight Promise, so a burst of near-simultaneous
    // detections for the same card (e.g. 20 auction rows in one search
    // result) coalesce into one backend call rather than one each.
    const inFlight = new Map();

    async function get(sourceCardId) {
      const id = Number(sourceCardId);
      if (!Number.isFinite(id) || id <= 0) {
        return { status: "network_error", data: null };
      }

      const cached = cache.get(id);
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
          cache.set(id, { expiresAt: now() + successTtlMs, result });
        } else if (result.status === "not_found") {
          cache.set(id, { expiresAt: now() + notFoundTtlMs, result });
        } else {
          // Retryable failure - explicitly evict any stale entry rather
          // than leaving one in place, so the very next get() re-fetches.
          cache.delete(id);
        }
        return result;
      })();

      inFlight.set(id, promise);
      return promise;
    }

    function clear() {
      cache.clear();
      inFlight.clear();
    }

    return { get, clear };
  }

  return { createIntelligenceCache, RETRYABLE_STATUSES, CACHEABLE_STATUSES };
});
