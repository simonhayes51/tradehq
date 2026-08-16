// tests/umd-wrapper.test.js
//
// Regression test for a real bug found live-testing against the actual
// EA FC 26 Web App: card-detector.js/market-calc.js loaded and ran with
// no error, but never attached anything to window.EAFCLive -
// market-observer.js logged "card-detector unavailable" every time.
//
// Root cause: the original UMD wrapper checked
// `typeof module !== "undefined" && module.exports` FIRST to decide
// whether it was running under Node. EA's own bundled page JS leaves a
// global `module` behind (common leftover from vendor code built for
// CommonJS) - and since a content script executes against the SAME
// shared window/global object as the host page, `typeof module` sees
// that page-defined global too, tripping the wrapper into the
// module.exports branch and silently never populating window.EAFCLive.
//
// This test reproduces exactly that: a `window` global present (proving
// we're in a browser-like environment) ALONGSIDE a stray `module`
// global (simulating the page's leftover), and asserts the file still
// attaches to window.EAFCLive rather than silently doing nothing.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function loadInPolluted(relativePath, { withWindow, withImportScripts }) {
  const source = fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
  const sandbox = {
    console,
    // Simulates EA's page (or a bundler leftover) defining a global
    // `module` in the SAME scope a content script executes in - the
    // exact condition that broke this live. `module.exports` starts as
    // an empty object, same as a real but unused CommonJS shim.
    module: { exports: {} },
  };
  if (withWindow) sandbox.window = {};
  if (withImportScripts) sandbox.importScripts = () => {};
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return sandbox;
}

test("card-detector.js attaches to window.EAFCLive even with a polluted global `module` present", () => {
  const sandbox = loadInPolluted("src/card-detector.js", { withWindow: true, withImportScripts: false });
  assert.equal(typeof sandbox.window.EAFCLive, "object");
  assert.equal(typeof sandbox.window.EAFCLive.groupAuctionsByCardId, "function");
  assert.equal(typeof sandbox.window.EAFCLive.extractAuctionsFromResponse, "function");
  // Must NOT have taken the CommonJS branch instead.
  assert.deepEqual(sandbox.module.exports, {});
});

test("market-calc.js attaches to window.EAFCLive even with a polluted global `module` present", () => {
  const sandbox = loadInPolluted("src/market-calc.js", { withWindow: true, withImportScripts: false });
  assert.equal(typeof sandbox.window.EAFCLive.combineIntelligence, "function");
  assert.deepEqual(sandbox.module.exports, {});
});

test("intelligence-cache.js attaches to globalThis.EAFCLive in a service-worker-like context (importScripts, no window) even with a polluted global `module`", () => {
  const sandbox = loadInPolluted("src/intelligence-cache.js", { withWindow: false, withImportScripts: true });
  assert.equal(typeof sandbox.EAFCLive.createIntelligenceCache, "function");
  assert.deepEqual(sandbox.module.exports, {});
});

test("card-detector.js still exports via module.exports under real Node (no window, no importScripts)", () => {
  // The actual require()-based path every other test file in this repo
  // depends on - must keep working.
  delete require.cache[require.resolve("../src/card-detector.js")];
  const mod = require("../src/card-detector.js");
  assert.equal(typeof mod.groupAuctionsByCardId, "function");
});
