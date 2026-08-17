// tests/intelligence-cache.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { createIntelligenceCache } from "../src/intelligence-cache.js";

function makeClock(start) {
  let t = start;
  return { now: () => t, advance: (ms) => (t += ms) };
}

test("success results are cached for successTtlMs", async () => {
  let calls = 0;
  const clock = makeClock(0);
  const cache = createIntelligenceCache(
    async () => {
      calls += 1;
      return { status: "success", data: { signal: "buy" } };
    },
    { successTtlMs: 1000, now: clock.now }
  );
  await cache.get(1);
  await cache.get(1);
  assert.equal(calls, 1);
  clock.advance(1001);
  await cache.get(1);
  assert.equal(calls, 2);
});

test("not_found results use the shorter notFoundTtlMs", async () => {
  let calls = 0;
  const clock = makeClock(0);
  const cache = createIntelligenceCache(
    async () => {
      calls += 1;
      return { status: "not_found", data: null };
    },
    { successTtlMs: 100000, notFoundTtlMs: 500, now: clock.now }
  );
  await cache.get(1);
  clock.advance(600);
  await cache.get(1);
  assert.equal(calls, 2);
});

test("network_error/server_error/timeout are never cached - always retried", async () => {
  let calls = 0;
  const cache = createIntelligenceCache(async () => {
    calls += 1;
    return { status: "network_error", data: null };
  });
  await cache.get(1);
  await cache.get(1);
  assert.equal(calls, 2);
});

test("concurrent gets for the same card coalesce into one fetchFn call", async () => {
  let calls = 0;
  const cache = createIntelligenceCache(async () => {
    calls += 1;
    await new Promise((r) => setTimeout(r, 10));
    return { status: "success", data: {} };
  });
  await Promise.all([cache.get(1), cache.get(1), cache.get(1)]);
  assert.equal(calls, 1);
});

test("invalid card ids resolve to a network_error without calling fetchFn", async () => {
  let calls = 0;
  const cache = createIntelligenceCache(async () => {
    calls += 1;
    return { status: "success", data: {} };
  });
  const result = await cache.get(-1);
  assert.equal(result.status, "network_error");
  assert.equal(calls, 0);
});
