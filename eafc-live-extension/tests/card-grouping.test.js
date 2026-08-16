// tests/card-grouping.test.js
// Pure-logic tests for src/card-grouping.js - the ISOLATED-world-only
// counterpart to card-detector.js's groupAuctionsByCardId (see that
// file's header for why it's a separate file rather than sharing
// card-detector.js across both manifest.json content_scripts entries).
// Mirrors the equivalent groupAuctionsByCardId cases in
// card-detector.test.js, since the logic is a deliberate duplicate.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { groupAuctionsByCardId } = require("../src/card-grouping.js");

test("groupAuctionsByCardId resolves a single source_card_id when every sanitized auction agrees", () => {
  const auctions = [
    { sourceCardId: 117672259, tradeId: 1, buyNowPrice: 1090000 },
    { sourceCardId: 117672259, tradeId: 2, buyNowPrice: 1110000 },
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

test("groupAuctionsByCardId returns null identity and empty auctions for an empty list", () => {
  const result = groupAuctionsByCardId([]);
  assert.equal(result.sourceCardId, null);
  assert.equal(result.auctions.length, 0);
});
