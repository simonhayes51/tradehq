// tests/card-detector.test.js
// Pure-logic tests for src/card-detector.js - run with:
//   node --test tests/
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { extractExactCardId, extractAuctionsFromResponse, detectCardIdentity } = require("../src/card-detector.js");

// Identity: EA resourceId 117672259 -> source_card_id 117672259
test("extractExactCardId uses resourceId as the exact card identity", () => {
  const id = extractExactCardId({ resourceId: 117672259, definitionId: 117672259, maskedDefId: 231747 });
  assert.equal(id, 117672259);
});

test("extractExactCardId falls back to definitionId when resourceId is absent", () => {
  const id = extractExactCardId({ definitionId: 117672259, maskedDefId: 231747 });
  assert.equal(id, 117672259);
});

test("extractExactCardId never uses maskedDefId alone", () => {
  const id = extractExactCardId({ maskedDefId: 231747 });
  assert.equal(id, null);
});

test("extractExactCardId returns null for garbage input", () => {
  assert.equal(extractExactCardId(null), null);
  assert.equal(extractExactCardId({}), null);
  assert.equal(extractExactCardId({ resourceId: "not-a-number" }), null);
});

test("extractAuctionsFromResponse parses a standard auctionInfo response", () => {
  const body = {
    auctionInfo: [
      {
        tradeId: 1001,
        itemData: { resourceId: 117672259, definitionId: 117672259, maskedDefId: 231747, rating: 97 },
        buyNowPrice: 1090000,
        currentBid: 1050000,
        startingBid: 950000,
        expires: 3184,
        tradeState: "active",
      },
      {
        tradeId: 1002,
        itemData: { resourceId: 117672259, definitionId: 117672259, maskedDefId: 231747, rating: 97 },
        buyNowPrice: 1110000,
        expires: 200,
      },
    ],
  };
  const auctions = extractAuctionsFromResponse(body);
  assert.equal(auctions.length, 2);
  assert.equal(auctions[0].sourceCardId, 117672259);
  assert.equal(auctions[0].buyNowPrice, 1090000);
  assert.equal(auctions[1].expires, 200);
});

test("extractAuctionsFromResponse tolerates a bare array body", () => {
  const body = [
    { tradeId: 1, itemData: { resourceId: 5 }, buyNowPrice: 1000 },
  ];
  const auctions = extractAuctionsFromResponse(body);
  assert.equal(auctions.length, 1);
  assert.equal(auctions[0].sourceCardId, 5);
});

test("extractAuctionsFromResponse tolerates a single-item body", () => {
  const body = { itemData: { resourceId: 5 }, buyNowPrice: 1000 };
  const auctions = extractAuctionsFromResponse(body);
  assert.equal(auctions.length, 1);
});

test("extractAuctionsFromResponse never throws on unrecognised shapes", () => {
  assert.deepEqual(extractAuctionsFromResponse(null), []);
  assert.deepEqual(extractAuctionsFromResponse(undefined), []);
  assert.deepEqual(extractAuctionsFromResponse("not json shaped"), []);
  assert.deepEqual(extractAuctionsFromResponse({ someOtherField: true }), []);
  assert.deepEqual(extractAuctionsFromResponse({ auctionInfo: [null, {}, { itemData: {} }] }), []);
});

test("extractAuctionsFromResponse drops entries with no identifiable card", () => {
  const body = {
    auctionInfo: [
      { tradeId: 1, itemData: { maskedDefId: 231747 }, buyNowPrice: 1000 }, // no resourceId/definitionId
      { tradeId: 2, itemData: { resourceId: 5 }, buyNowPrice: 2000 },
    ],
  };
  const auctions = extractAuctionsFromResponse(body);
  assert.equal(auctions.length, 1);
  assert.equal(auctions[0].tradeId, 2);
});

test("detectCardIdentity resolves a single source_card_id when every auction agrees", () => {
  const body = {
    auctionInfo: [
      { tradeId: 1, itemData: { resourceId: 117672259 }, buyNowPrice: 1090000 },
      { tradeId: 2, itemData: { resourceId: 117672259 }, buyNowPrice: 1110000 },
    ],
  };
  const result = detectCardIdentity(body);
  assert.equal(result.sourceCardId, 117672259);
  assert.equal(result.auctions.length, 2);
});

test("detectCardIdentity refuses to pick one card when the response mixes several", () => {
  const body = {
    auctionInfo: [
      { tradeId: 1, itemData: { resourceId: 111 }, buyNowPrice: 1000 },
      { tradeId: 2, itemData: { resourceId: 222 }, buyNowPrice: 2000 },
    ],
  };
  const result = detectCardIdentity(body);
  assert.equal(result.sourceCardId, null);
  assert.equal(Object.keys(result.groupedByCardId).length, 2);
});

test("detectCardIdentity distinguishes special versions sharing the same maskedDefId", () => {
  // Two different card versions of the same underlying player - same
  // maskedDefId, different resourceId. Must not collapse into one card.
  const body = {
    auctionInfo: [
      { tradeId: 1, itemData: { resourceId: 117672259, maskedDefId: 231747 }, buyNowPrice: 1090000 },
      { tradeId: 2, itemData: { resourceId: 55001234, maskedDefId: 231747 }, buyNowPrice: 60000 },
    ],
  };
  const result = detectCardIdentity(body);
  assert.equal(result.sourceCardId, null);
  assert.ok(result.groupedByCardId[117672259]);
  assert.ok(result.groupedByCardId[55001234]);
});

test("detectCardIdentity returns null identity and empty auctions for an empty response", () => {
  const result = detectCardIdentity({ auctionInfo: [] });
  assert.equal(result.sourceCardId, null);
  assert.equal(result.auctions.length, 0);
});
