// tests/card-detector.test.js
// Pure-logic tests for src/card-detector.js - run with:
//   node --test tests/
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  extractExactCardId,
  extractAuctionsFromResponse,
  groupAuctionsByCardId,
  detectCardIdentity,
} = require("../src/card-detector.js");

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

// ---------------------------------------------------------------------
// Sanitization: the payload page-bridge.js dispatches across the MAIN ->
// isolated-world boundary must contain ONLY the agreed field set - never
// itemData, maskedDefId, headers, or anything else EA's response happens
// to carry.
// ---------------------------------------------------------------------

test("normalizeAuction output contains only the sanitized field set", () => {
  const auctions = extractAuctionsFromResponse({
    auctionInfo: [
      {
        tradeId: 1001,
        itemData: {
          resourceId: 117672259,
          definitionId: 117672259,
          maskedDefId: 231747,
          rating: 97,
          itemType: "player",
          discardValue: 150,
        },
        buyNowPrice: 1090000,
        currentBid: 1050000,
        startingBid: 950000,
        expires: 3184,
        tradeState: "active",
        sellerEstablished: 0,
        watched: false,
      },
    ],
  });
  assert.equal(auctions.length, 1);
  const keys = Object.keys(auctions[0]).sort();
  assert.deepEqual(keys, [
    "buyNowPrice",
    "currentBid",
    "expires",
    "rating",
    "sourceCardId",
    "startingBid",
    "tradeId",
    "tradeState",
  ]);
  // Explicitly never present, even though the raw entry carried them.
  assert.equal("maskedDefId" in auctions[0], false);
  assert.equal("itemType" in auctions[0], false);
  assert.equal("discardValue" in auctions[0], false);
  assert.equal("sellerEstablished" in auctions[0], false);
  assert.equal("itemData" in auctions[0], false);
});

// ---------------------------------------------------------------------
// groupAuctionsByCardId - what market-observer.js now runs over the
// already-sanitized payload it receives from page-bridge.js, with no
// access to (or need for) the raw EA response.
// ---------------------------------------------------------------------

test("groupAuctionsByCardId resolves a single source_card_id when every sanitized auction agrees", () => {
  const auctions = [
    { sourceCardId: 117672259, tradeId: 1, buyNowPrice: 1090000, rating: 97, currentBid: 0, startingBid: 0, expires: null, tradeState: null },
    { sourceCardId: 117672259, tradeId: 2, buyNowPrice: 1110000, rating: 97, currentBid: 0, startingBid: 0, expires: null, tradeState: null },
  ];
  const result = groupAuctionsByCardId(auctions);
  assert.equal(result.sourceCardId, 117672259);
  assert.equal(result.auctions.length, 2);
});

test("groupAuctionsByCardId refuses to pick one card from a mixed sanitized payload", () => {
  const auctions = [
    { sourceCardId: 111, buyNowPrice: 1000 },
    { sourceCardId: 222, buyNowPrice: 2000 },
  ];
  const result = groupAuctionsByCardId(auctions);
  assert.equal(result.sourceCardId, null);
  assert.equal(Object.keys(result.groupedByCardId).length, 2);
});

test("groupAuctionsByCardId tolerates missing/malformed input harmlessly", () => {
  assert.deepEqual(groupAuctionsByCardId(null).auctions, []);
  assert.deepEqual(groupAuctionsByCardId(undefined).auctions, []);
  assert.equal(groupAuctionsByCardId([null, {}, { sourceCardId: "nope" }]).auctions.length, 0);
});

// ---------------------------------------------------------------------
// End-to-end identity resolution through the full response -> sanitized
// auctions -> grouping pipeline (mirrors what page-bridge.js +
// market-observer.js do across the two worlds).
// ---------------------------------------------------------------------

test("full pipeline resolves definitionId fallback identity end-to-end", () => {
  const body = {
    auctionInfo: [
      { tradeId: 1, itemData: { definitionId: 117672259, maskedDefId: 231747 }, buyNowPrice: 1090000 },
    ],
  };
  const sanitized = extractAuctionsFromResponse(body);
  const result = groupAuctionsByCardId(sanitized);
  assert.equal(result.sourceCardId, 117672259);
});

test("full pipeline never resolves identity from maskedDefId-only auctions", () => {
  const body = {
    auctionInfo: [{ tradeId: 1, itemData: { maskedDefId: 231747 }, buyNowPrice: 1090000 }],
  };
  const sanitized = extractAuctionsFromResponse(body);
  assert.equal(sanitized.length, 0);
  const result = groupAuctionsByCardId(sanitized);
  assert.equal(result.sourceCardId, null);
  assert.equal(result.auctions.length, 0);
});

test("full pipeline fails harmlessly on a malformed EA response", () => {
  for (const malformed of [null, undefined, "<html>not json</html>", 42, { auctionInfo: "not-an-array" }]) {
    const sanitized = extractAuctionsFromResponse(malformed);
    assert.deepEqual(sanitized, []);
    const result = groupAuctionsByCardId(sanitized);
    assert.equal(result.sourceCardId, null);
  }
});
