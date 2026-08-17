// tests/list-badges.test.js
// Pure-logic tests only - the DOM-anchoring half of list-badges.js
// (findPriceAnchor, positioning) needs a real page and is not exercised
// here; see README.md's "what's confirmed vs still open" section.
import test from "node:test";
import assert from "node:assert/strict";
import { auctionKey, verdictFor } from "../src/list-badges.js";

function auction(overrides) {
  return { sourceCardId: 1, tradeId: 100, buyNowPrice: 50000, currentBid: 0, startingBid: 0, expires: null, tradeState: null, ...overrides };
}

test("auctionKey is stable and distinguishes different listings of the same card", () => {
  const a = auction({ tradeId: 1 });
  const b = auction({ tradeId: 2 });
  assert.notEqual(auctionKey(a), auctionKey(b));
  assert.equal(auctionKey(a), auctionKey(auction({ tradeId: 1 })));
});

test("verdictFor marks BUY only when the row's own price clears the target", () => {
  const intelligence = { signal: "strong_buy", buy_below: 60000 };
  assert.equal(verdictFor(auction({ buyNowPrice: 55000 }), intelligence).label, "BUY");
  assert.equal(verdictFor(auction({ buyNowPrice: 70000 }), intelligence), null);
});

test("verdictFor never marks BUY on insufficient_data, even at a very cheap price", () => {
  const intelligence = { signal: "insufficient_data", buy_below: null };
  assert.equal(verdictFor(auction({ buyNowPrice: 1 }), intelligence), null);
});

test("verdictFor marks AVOID for an avoid signal regardless of price", () => {
  const intelligence = { signal: "avoid", buy_below: null };
  const verdict = verdictFor(auction({ buyNowPrice: 999999 }), intelligence);
  assert.equal(verdict.tone, "avoid");
  assert.equal(verdict.label, "AVOID");
});

test("verdictFor returns null with no intelligence at all", () => {
  assert.equal(verdictFor(auction(), null), null);
});

test("verdictFor leaves a watch/hold signal unbadged", () => {
  const intelligence = { signal: "watch", buy_below: 60000 };
  assert.equal(verdictFor(auction({ buyNowPrice: 1 }), intelligence), null);
});
