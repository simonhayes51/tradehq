// tests/intelligence-cache.test.js
// Pure-logic tests for src/intelligence-cache.js - the caching/retry
// policy that sits in front of the background worker's real fetch.
// Run with: node --test tests/
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createIntelligenceCache } = require("../src/intelligence-cache.js");

function makeClock(start = 0) {
  let t = start;
  return { now: () => t, advance: (ms) => { t += ms; } };
}

function countingFetch(results) {
  // `results` is an array of {status, data} consumed in order, one per
  // real call to fetchFn - lets a test script "first call fails, second
  // succeeds" etc.
  let calls = 0;
  const fn = async (id) => {
    calls++;
    const next = results[Math.min(calls - 1, results.length - 1)];
    return next;
  };
  fn.callCount = () => calls;
  return fn;
}

test("success is cached: repeated get() within TTL hits fetchFn once", async () => {
  const clock = makeClock();
  const fetchFn = countingFetch([{ status: "success", data: { signal: "buy" } }]);
  const cache = createIntelligenceCache(fetchFn, { successTtlMs: 1000, now: clock.now });

  const first = await cache.get(1);
  const second = await cache.get(1);
  assert.equal(fetchFn.callCount(), 1);
  assert.equal(first.status, "success");
  assert.deepEqual(second, first);
});

test("success cache expires after successTtlMs and re-fetches", async () => {
  const clock = makeClock();
  const fetchFn = countingFetch([
    { status: "success", data: { signal: "buy" } },
    { status: "success", data: { signal: "watch" } },
  ]);
  const cache = createIntelligenceCache(fetchFn, { successTtlMs: 1000, now: clock.now });

  await cache.get(1);
  clock.advance(1001);
  const second = await cache.get(1);
  assert.equal(fetchFn.callCount(), 2);
  assert.equal(second.data.signal, "watch");
});

test("not_found is cached, but for a shorter TTL than success", async () => {
  const clock = makeClock();
  const fetchFn = countingFetch([{ status: "not_found", data: null }]);
  const cache = createIntelligenceCache(fetchFn, {
    successTtlMs: 5 * 60 * 1000,
    notFoundTtlMs: 100,
    now: clock.now,
  });

  await cache.get(1);
  await cache.get(1);
  assert.equal(fetchFn.callCount(), 1); // still within notFoundTtlMs

  clock.advance(101);
  await cache.get(1);
  assert.equal(fetchFn.callCount(), 2); // expired, re-fetched
});

// Retryability: a transient failure must never lock a card out of
// retrying on the very next call - this is the core fix requested after
// the original implementation cached network/server errors for the
// full 5-minute success TTL.
for (const failureStatus of ["network_error", "server_error", "timeout"]) {
  test(`${failureStatus} is never cached - the next get() retries immediately`, async () => {
    const clock = makeClock();
    const fetchFn = countingFetch([
      { status: failureStatus, data: null },
      { status: "success", data: { signal: "strong_buy" } },
    ]);
    const cache = createIntelligenceCache(fetchFn, { successTtlMs: 5 * 60 * 1000, now: clock.now });

    const first = await cache.get(1);
    assert.equal(first.status, failureStatus);

    // No time has passed at all - if this were cached like a success,
    // the second call would return the same failure without re-fetching.
    const second = await cache.get(1);
    assert.equal(fetchFn.callCount(), 2);
    assert.equal(second.status, "success");
  });
}

test("a failure does not evict an existing good cache entry for a DIFFERENT card", async () => {
  const clock = makeClock();
  const fetchFn = async (id) => {
    if (id === 1) return { status: "success", data: { signal: "buy" } };
    return { status: "network_error", data: null };
  };
  const cache = createIntelligenceCache(fetchFn, { successTtlMs: 1000, now: clock.now });

  await cache.get(1);
  await cache.get(2); // fails, must not disturb card 1's cache entry
  const again = await cache.get(1);
  assert.equal(again.status, "success");
});

test("concurrent get() calls for the same card coalesce into one fetchFn call", async () => {
  const clock = makeClock();
  let resolveFetch;
  let calls = 0;
  const fetchFn = () => {
    calls++;
    return new Promise((resolve) => {
      resolveFetch = () => resolve({ status: "success", data: { signal: "buy" } });
    });
  };
  const cache = createIntelligenceCache(fetchFn, { successTtlMs: 1000, now: clock.now });

  const p1 = cache.get(1);
  const p2 = cache.get(1);
  resolveFetch();
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(calls, 1);
  assert.deepEqual(r1, r2);
});

test("invalid sourceCardId resolves to network_error without calling fetchFn", async () => {
  const fetchFn = countingFetch([{ status: "success", data: {} }]);
  const cache = createIntelligenceCache(fetchFn, {});
  const result = await cache.get(-5);
  assert.equal(result.status, "network_error");
  assert.equal(fetchFn.callCount(), 0);
});

test("clear() empties the cache so the next get() re-fetches", async () => {
  const clock = makeClock();
  const fetchFn = countingFetch([
    { status: "success", data: { signal: "buy" } },
    { status: "success", data: { signal: "watch" } },
  ]);
  const cache = createIntelligenceCache(fetchFn, { successTtlMs: 999999, now: clock.now });

  await cache.get(1);
  cache.clear();
  const second = await cache.get(1);
  assert.equal(fetchFn.callCount(), 2);
  assert.equal(second.data.signal, "watch");
});
