// tests/market-calc.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { cheapestBin, countAtOrBelow, combineIntelligence, verdictLabel } from "../src/market-calc.js";

function auction(buyNowPrice, extra) {
  return { sourceCardId: 1, buyNowPrice, currentBid: 0, startingBid: 0, expires: null, tradeState: null, ...extra };
}

test("cheapestBin picks the lowest positive BIN", () => {
  assert.equal(cheapestBin([auction(1000), auction(500), auction(0)]), 500);
});

test("cheapestBin returns null with no buyable listings", () => {
  assert.equal(cheapestBin([]), null);
  assert.equal(cheapestBin([auction(0)]), null);
});

test("countAtOrBelow counts listings under the target, inclusive", () => {
  assert.equal(countAtOrBelow([auction(100), auction(200), auction(300)], 200), 2);
});

test("combineIntelligence: no backend intelligence never confirms an opportunity", () => {
  const combined = combineIntelligence([auction(50000)], null);
  assert.equal(combined.hasIntelligence, false);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_intelligence");
});

test("combineIntelligence: insufficient_data signal never confirms, even with a cheap live BIN", () => {
  const intelligence = { signal: "insufficient_data", buy_below: null, fair_value: null };
  const combined = combineIntelligence([auction(1000)], intelligence);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_buy_signal");
});

test("combineIntelligence: strong_buy + live BIN under target confirms the opportunity", () => {
  const intelligence = { signal: "strong_buy", buy_below: 76000, fair_value: 90000, recommended_sell_target: 95000 };
  const combined = combineIntelligence([auction(71000), auction(80000)], intelligence);
  assert.equal(combined.opportunityConfirmed, true);
  assert.equal(combined.reason, "confirmed");
  assert.equal(combined.cheapestBin, 71000);
  assert.equal(combined.underBuyBelowCount, 1);
});

test("combineIntelligence: buy signal but live BIN above target does not confirm", () => {
  const intelligence = { signal: "buy", buy_below: 50000 };
  const combined = combineIntelligence([auction(60000)], intelligence);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "above_target");
});

test("combineIntelligence: buy signal with no live listings does not confirm", () => {
  const intelligence = { signal: "strong_buy", buy_below: 50000 };
  const combined = combineIntelligence([], intelligence);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_live_listings");
});

test("verdictLabel only ever uses the confirmed tone for reason='confirmed'", () => {
  assert.equal(verdictLabel("confirmed", "strong_buy").tone, "confirmed");
  for (const reason of ["above_target", "no_live_listings", "no_target_price", "no_intelligence", "no_buy_signal"]) {
    assert.notEqual(verdictLabel(reason, "buy").tone, "confirmed");
  }
});

test("verdictLabel surfaces the backend signal for a non-buy recommendation", () => {
  const label = verdictLabel("no_buy_signal", "insufficient_data");
  assert.match(label.text, /INSUFFICIENT DATA/);
  assert.match(label.text, /NOT A BUY/);
});
