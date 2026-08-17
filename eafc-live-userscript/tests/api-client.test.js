// tests/api-client.test.js
//
// api-client.js's getCardIntelligence() is built on GM_xmlhttpRequest, a
// userscript-manager-injected global that doesn't exist under Node - it
// is stubbed here on globalThis exactly the way a real userscript
// manager would provide it, so the response-handling/timeout logic can
// be exercised without a real userscript manager or network call.
import test from "node:test";
import assert from "node:assert/strict";

function stubGM(handler) {
  globalThis.GM_xmlhttpRequest = handler;
}

test("a 200 response with valid JSON resolves status success", async () => {
  stubGM((opts) => opts.onload({ status: 200, responseText: JSON.stringify({ signal: "buy" }) }));
  const { getCardIntelligence } = await import(`../src/api-client.js?case=success`);
  const result = await getCardIntelligence(117672259);
  assert.equal(result.status, "success");
  assert.equal(result.data.signal, "buy");
});

test("a 404 response resolves status not_found with null data", async () => {
  stubGM((opts) => opts.onload({ status: 404, responseText: "" }));
  const { getCardIntelligence } = await import(`../src/api-client.js?case=notfound`);
  const result = await getCardIntelligence(9999999);
  assert.equal(result.status, "not_found");
  assert.equal(result.data, null);
});

test("a 500 response resolves status server_error", async () => {
  stubGM((opts) => opts.onload({ status: 500, responseText: "" }));
  const { getCardIntelligence } = await import(`../src/api-client.js?case=servererror`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "server_error");
});

test("ontimeout resolves status timeout, not a cached failure", async () => {
  stubGM((opts) => opts.ontimeout());
  const { getCardIntelligence } = await import(`../src/api-client.js?case=timeout`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "timeout");
});

test("onerror resolves status network_error", async () => {
  stubGM((opts) => opts.onerror());
  const { getCardIntelligence } = await import(`../src/api-client.js?case=network`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "network_error");
});

test("missing GM_xmlhttpRequest fails harmlessly instead of throwing", async () => {
  globalThis.GM_xmlhttpRequest = undefined;
  globalThis.GM = undefined;
  const { getCardIntelligence } = await import(`../src/api-client.js?case=missing`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "network_error");
});

test("falls back to the namespaced GM.xmlHttpRequest when the legacy global is absent", async () => {
  // Confirmed live: some userscript managers (e.g. the iOS "Userscripts"
  // app) only expose GM.xmlHttpRequest, not the legacy GM_xmlhttpRequest
  // global - this is the exact case that produced a silent
  // "backend unreachable" overlay in production before this fallback
  // was added.
  globalThis.GM_xmlhttpRequest = undefined;
  globalThis.GM = {
    xmlHttpRequest(opts) {
      opts.onload({ status: 200, responseText: JSON.stringify({ signal: "strong_buy" }) });
    },
  };
  const { getCardIntelligence } = await import(`../src/api-client.js?case=namespaced`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "success");
  assert.equal(result.data.signal, "strong_buy");
  globalThis.GM = undefined;
});

test("falls back to GM.xmlHttpRequest when it only resolves a Promise (no callbacks fired)", async () => {
  globalThis.GM_xmlhttpRequest = undefined;
  globalThis.GM = {
    xmlHttpRequest() {
      return Promise.resolve({ status: 404, responseText: "" });
    },
  };
  const { getCardIntelligence } = await import(`../src/api-client.js?case=namespaced-promise`);
  const result = await getCardIntelligence(1);
  assert.equal(result.status, "not_found");
  globalThis.GM = undefined;
});

test("never forwards EA session data - only the numeric card id reaches the URL", async () => {
  let capturedUrl = null;
  stubGM((opts) => {
    capturedUrl = opts.url;
    assert.equal(opts.headers.Accept, "application/json");
    assert.equal("headers" in opts && Object.keys(opts.headers).length, 1);
    opts.onload({ status: 200, responseText: "{}" });
  });
  const { getCardIntelligence } = await import(`../src/api-client.js?case=nocreds`);
  await getCardIntelligence(117672259);
  assert.equal(capturedUrl, "https://api.eafc.live/api/v2/companion/card/117672259");
});
