// tests/lifecycle.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { initLifecycle, __resetForTests } from "../src/lifecycle.js";

test("initLifecycle only runs setup once across repeated calls (SPA duplicate-init protection)", () => {
  __resetForTests();
  let calls = 0;
  initLifecycle(() => (calls += 1));
  initLifecycle(() => (calls += 1));
  initLifecycle(() => (calls += 1));
  assert.equal(calls, 1);
});
