// src/intelligence-cache.js
//
// Pure caching/retry policy in front of a network fetch function. No
// DOM, no network APIs of its own - unit-testable directly under Node
// with a fake `fetchFn`. Ported verbatim (logic unchanged) from
// eafc-live-extension/src/intelligence-cache.js, minus the UMD wrapper.
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

export const RETRYABLE_STATUSES = ["network_error", "server_error", "timeout"];
export const CACHEABLE_STATUSES = ["success", "not_found"];

/**
 * @param {(sourceCardId: number) => Promise<{status: string, data: any}>} fetchFn
 * @param {{successTtlMs?: number, notFoundTtlMs?: number, now?: () => number}} [options]
 */
export function createIntelligenceCache(fetchFn, options) {
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
  // detections for the same card coalesce into one backend call.
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
