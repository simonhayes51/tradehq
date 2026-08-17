// tests/card-detector.test.js
// Pure-logic tests for src/card-detector.js - run with: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import {
  extractExactCardId,
  extractAuctionsFromResponse,
  groupAuctionsByCardId,
  detectCardIdentity,
} from "../src/card-detector.js";

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
    ],
  };
  const auctions = extractAuctionsFromResponse(body);
  assert.equal(auctions.length, 1);
  assert.equal(auctions[0].sourceCardId, 117672259);
});

test("extractAuctionsFromResponse never throws on unrecognised shapes", () => {
  assert.deepEqual(extractAuctionsFromResponse(null), []);
  assert.deepEqual(extractAuctionsFromResponse(undefined), []);
  assert.deepEqual(extractAuctionsFromResponse("not json shaped"), []);
  assert.deepEqual(extractAuctionsFromResponse({ auctionInfo: [null, {}, { itemData: {} }] }), []);
});

test("normalizeAuction output contains only the sanitized field set", () => {
  const auctions = extractAuctionsFromResponse({
    auctionInfo: [
      {
        tradeId: 1001,
        itemData: { resourceId: 117672259, maskedDefId: 231747, rating: 97, itemType: "player" },
        buyNowPrice: 1090000,
        sellerEstablished: 0,
      },
    ],
  });
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
  assert.equal("maskedDefId" in auctions[0], false);
  assert.equal("itemType" in auctions[0], false);
  assert.equal("sellerEstablished" in auctions[0], false);
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

test("groupAuctionsByCardId tolerates missing/malformed input harmlessly", () => {
  assert.deepEqual(groupAuctionsByCardId(null).auctions, []);
  assert.deepEqual(groupAuctionsByCardId(undefined).auctions, []);
  assert.equal(groupAuctionsByCardId([null, {}, { sourceCardId: "nope" }]).auctions.length, 0);
});

test("full pipeline resolves definitionId fallback identity end-to-end", () => {
  const body = {
    auctionInfo: [{ tradeId: 1, itemData: { definitionId: 117672259, maskedDefId: 231747 }, buyNowPrice: 1090000 }],
  };
  const result = groupAuctionsByCardId(extractAuctionsFromResponse(body));
  assert.equal(result.sourceCardId, 117672259);
});

test("full pipeline never resolves identity from maskedDefId-only auctions", () => {
  const body = { auctionInfo: [{ tradeId: 1, itemData: { maskedDefId: 231747 }, buyNowPrice: 1090000 }] };
  const sanitized = extractAuctionsFromResponse(body);
  assert.equal(sanitized.length, 0);
});

test("full pipeline fails harmlessly on a malformed EA response", () => {
  for (const malformed of [null, undefined, "<html>not json</html>", 42, { auctionInfo: "not-an-array" }]) {
    const sanitized = extractAuctionsFromResponse(malformed);
    assert.deepEqual(sanitized, []);
    assert.equal(groupAuctionsByCardId(sanitized).sourceCardId, null);
  }
});
