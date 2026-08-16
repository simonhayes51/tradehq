// tests/market-calc.test.js
// Pure-logic tests for src/market-calc.js - run with:
//   node --test tests/
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  cheapestBin,
  countAtOrBelow,
  countBelow,
  afterTaxProfit,
  combineIntelligence,
  verdictLabel,
} = require("../src/market-calc.js");

function auction(buyNowPrice, extra) {
  return { sourceCardId: 1, buyNowPrice, currentBid: 0, startingBid: 0, expires: null, ...extra };
}

// Cheapest BIN: given 1090000, 1110000, 1120000 -> 1090000
test("cheapestBin picks the lowest positive BIN", () => {
  const auctions = [auction(1120000), auction(1090000), auction(1110000)];
  assert.equal(cheapestBin(auctions), 1090000);
});

test("cheapestBin ignores zero/missing BINs (bid-only listings)", () => {
  const auctions = [auction(0), auction(1090000)];
  assert.equal(cheapestBin(auctions), 1090000);
});

test("cheapestBin returns null when there are no buyable listings", () => {
  assert.equal(cheapestBin([auction(0)]), null);
  assert.equal(cheapestBin([]), null);
});

// Buy threshold: buy_below=1150000 over 1090000/1110000/1160000 -> 2 below target
test("countAtOrBelow counts listings at or under the target", () => {
  const auctions = [auction(1090000), auction(1110000), auction(1160000)];
  assert.equal(countAtOrBelow(auctions, 1150000), 2);
});

test("countBelow is strict (does not count a listing exactly at the threshold)", () => {
  const auctions = [auction(1150000), auction(1140000)];
  assert.equal(countBelow(auctions, 1150000), 1);
  assert.equal(countAtOrBelow(auctions, 1150000), 2);
});

// No opportunity: every live BIN above buy_below -> confirmation false
test("combineIntelligence reports no confirmation when every BIN is above target", () => {
  const auctions = [auction(1200000), auction(1250000)];
  const intelligence = { signal: "strong_buy", buy_below: 1150000, fair_value: 1275000, recommended_sell_target: 1275000 };
  const combined = combineIntelligence(auctions, intelligence);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "above_target");
  assert.equal(combined.cheapestBin, 1200000);
});

test("combineIntelligence confirms when the cheapest live BIN clears the target on a buy signal", () => {
  const auctions = [auction(1090000), auction(1110000), auction(1200000)];
  const intelligence = {
    signal: "strong_buy",
    buy_below: 1150000,
    fair_value: 1275000,
    recommended_sell_target: 1275000,
  };
  const combined = combineIntelligence(auctions, intelligence);
  assert.equal(combined.opportunityConfirmed, true);
  assert.equal(combined.reason, "confirmed");
  assert.equal(combined.cheapestBin, 1090000);
  assert.equal(combined.underBuyBelowCount, 2);
});

// Missing recommendation: live data alone must never manufacture a BUY.
test("combineIntelligence never confirms an opportunity with no backend intelligence", () => {
  const auctions = [auction(1000), auction(2000)];
  const combined = combineIntelligence(auctions, null);
  assert.equal(combined.hasIntelligence, false);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_intelligence");
});

test("combineIntelligence never confirms when intelligence exists but is not a buy signal", () => {
  const auctions = [auction(1000)]; // cheap, but the recommendation says avoid/hold
  const intelligence = { signal: "avoid", buy_below: 5000, fair_value: 3000 };
  const combined = combineIntelligence(auctions, intelligence);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_buy_signal");
});

test("combineIntelligence handles zero live listings safely", () => {
  const intelligence = { signal: "buy", buy_below: 1150000, fair_value: 1275000 };
  const combined = combineIntelligence([], intelligence);
  assert.equal(combined.cheapestBin, null);
  assert.equal(combined.opportunityConfirmed, false);
  assert.equal(combined.reason, "no_live_listings");
});

test("afterTaxProfit matches EA's flat 5% sale tax", () => {
  // Sell 1275000, buy 1090000: net proceeds 1275000*0.95=1211250, profit 121250
  assert.equal(afterTaxProfit(1090000, 1275000), 121250);
});

test("afterTaxProfit returns null for non-numeric input rather than NaN", () => {
  assert.equal(afterTaxProfit(null, 1000), null);
  assert.equal(afterTaxProfit(1000, undefined), null);
});

// ---------------------------------------------------------------------
// verdictLabel - reason-aware overlay text. Every non-confirmed reason
// used to render the same "NO LIVE LISTING BELOW TARGET" string; each
// must now get its own accurate label, and only "confirmed" is allowed
// the confirmed/buy-flavoured tone.
// ---------------------------------------------------------------------

test("verdictLabel: confirmed", () => {
  const v = verdictLabel("confirmed", "strong_buy");
  assert.equal(v.text, "OPPORTUNITY CONFIRMED");
  assert.equal(v.tone, "confirmed");
});

test("verdictLabel: above_target", () => {
  const v = verdictLabel("above_target", "buy");
  assert.equal(v.text, "NO LIVE LISTING BELOW TARGET");
  assert.equal(v.tone, "neutral");
});

test("verdictLabel: no_live_listings", () => {
  const v = verdictLabel("no_live_listings", "buy");
  assert.equal(v.text, "NO LIVE BIN LISTINGS");
  assert.equal(v.tone, "neutral");
});

test("verdictLabel: no_target_price", () => {
  const v = verdictLabel("no_target_price", "buy");
  assert.equal(v.text, "NO BUY TARGET AVAILABLE");
});

test("verdictLabel: no_intelligence", () => {
  const v = verdictLabel("no_intelligence", undefined);
  assert.equal(v.text, "NO EAFC LIVE INTELLIGENCE");
});

test("verdictLabel: no_buy_signal shows the backend's actual signal", () => {
  const v = verdictLabel("no_buy_signal", "hold");
  assert.match(v.text, /^LIVE MARKET — HOLD/);
  assert.equal(v.tone, "neutral");
});

test("verdictLabel: no_buy_signal falls back to a generic label with no signal", () => {
  const v = verdictLabel("no_buy_signal", undefined);
  assert.equal(v.text, "LIVE MARKET — NO BUY RECOMMENDATION");
});

test("verdictLabel: unknown reason degrades to the neutral default rather than throwing", () => {
  const v = verdictLabel("something_new_from_a_future_change", "buy");
  assert.equal(v.tone, "neutral");
});

test("combineIntelligence reason always round-trips through verdictLabel without throwing", () => {
  const cases = [
    [[], null],
    [[{ buyNowPrice: 1000 }], { signal: "avoid", buy_below: 500 }],
    [[{ buyNowPrice: 1000 }], { signal: "buy", buy_below: null }],
    [[{ buyNowPrice: 0 }], { signal: "buy", buy_below: 500 }],
    [[{ buyNowPrice: 100 }], { signal: "buy", buy_below: 500 }],
  ];
  for (const [auctions, intelligence] of cases) {
    const combined = combineIntelligence(auctions, intelligence);
    const v = verdictLabel(combined.reason, intelligence && intelligence.signal);
    assert.ok(typeof v.text === "string" && v.text.length > 0);
    if (v.tone === "confirmed") {
      assert.equal(combined.reason, "confirmed");
    }
  }
});
