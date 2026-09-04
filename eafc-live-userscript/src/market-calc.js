// src/market-calc.js
//
// Pure logic: combine live EA auctions (from card-detector.js) with the
// EAFC.Live backend's intelligence object (from api-client.js) into the
// numbers the overlay shows. No DOM, no network.
//
// Ported verbatim (logic unchanged) from
// eafc-live-extension/src/market-calc.js, minus the UMD wrapper (see
// card-detector.js's header for why that isn't needed in a single-context
// userscript bundle).
//
// Every EA-tax/profit number here uses the SAME 5% figure the backend's
// app/services/trading_math.py::EA_TAX applies, so a profit shown here is
// never computed on different assumptions than the backend recommendation
// it's supposed to be confirming.

// Mirrors backend/app/services/trading_math.py's EA_TAX.
export const EA_TAX = 0.05;

function isBuyable(auction) {
  return !!auction && Number.isFinite(auction.buyNowPrice) && auction.buyNowPrice > 0;
}

/** Lowest positive BIN across the given auctions, or null if none. */
export function cheapestBin(auctions) {
  const buyable = (auctions || []).filter(isBuyable);
  if (buyable.length === 0) return null;
  return buyable.reduce((min, a) => Math.min(min, a.buyNowPrice), Infinity);
}

/** Count of BIN listings at or below `threshold` (threshold inclusive). */
export function countAtOrBelow(auctions, threshold) {
  if (threshold === null || threshold === undefined) return 0;
  return (auctions || []).filter((a) => isBuyable(a) && a.buyNowPrice <= threshold).length;
}

/** Count of BIN listings strictly below `threshold`. */
export function countBelow(auctions, threshold) {
  if (threshold === null || threshold === undefined) return 0;
  return (auctions || []).filter((a) => isBuyable(a) && a.buyNowPrice < threshold).length;
}

/** Soonest `expires` (seconds remaining) among the given auctions, or null. */
export function nearestExpiry(auctions) {
  const withExpiry = (auctions || []).filter(
    (a) => a && Number.isFinite(a.expires) && a.expires > 0
  );
  if (withExpiry.length === 0) return null;
  return withExpiry.reduce((min, a) => Math.min(min, a.expires), Infinity);
}

/** Net-of-tax profit selling at `sellPrice` after buying at `buyPrice`. */
export function afterTaxProfit(buyPrice, sellPrice) {
  if (!Number.isFinite(buyPrice) || !Number.isFinite(sellPrice)) return null;
  const netProceeds = sellPrice * (1 - EA_TAX);
  return Math.round(netProceeds - buyPrice);
}

const BUY_SIGNALS = new Set(["buy", "strong_buy"]);

/**
 * Combine live EA auctions with the backend's CardIntelligence object
 * into the summary the overlay renders.
 *
 * IMPORTANT - what this does NOT do: it never invents a signal. Live
 * market data with no backend intelligence produces
 * opportunityConfirmed=false and a "no intelligence" reason, never a BUY
 * on the strength of the live listings alone - the historical
 * recommendation is the only thing allowed to say BUY/STRONG BUY/etc,
 * this function only asks "does the live market confirm it right now".
 */
export function combineIntelligence(auctions, intelligence) {
  const list = auctions || [];
  const cheapest = cheapestBin(list);
  const listingCount = list.filter(isBuyable).length;
  const nearestExpirySeconds = nearestExpiry(list);

  if (!intelligence) {
    return {
      hasIntelligence: false,
      cheapestBin: cheapest,
      listingCount,
      underBuyBelowCount: 0,
      underFairValueCount: 0,
      nearestExpirySeconds,
      potentialProfitAfterTax: null,
      opportunityConfirmed: false,
      reason: "no_intelligence",
    };
  }

  const target = intelligence.buy_below ?? intelligence.recommended_buy_max ?? null;
  const fairValue = intelligence.fair_value ?? null;
  const sellTarget = intelligence.recommended_sell_target ?? fairValue;

  const underBuyBelowCount = countAtOrBelow(list, target);
  const underFairValueCount = countBelow(list, fairValue);

  const potentialProfitAfterTax =
    cheapest !== null && sellTarget !== null ? afterTaxProfit(cheapest, sellTarget) : null;

  const isBuySignal = BUY_SIGNALS.has(intelligence.signal);
  let opportunityConfirmed = false;
  let reason;

  if (!isBuySignal) {
    reason = "no_buy_signal";
  } else if (cheapest === null) {
    reason = "no_live_listings";
  } else if (target === null) {
    reason = "no_target_price";
  } else if (cheapest <= target) {
    opportunityConfirmed = true;
    reason = "confirmed";
  } else {
    reason = "above_target";
  }

  return {
    hasIntelligence: true,
    cheapestBin: cheapest,
    listingCount,
    underBuyBelowCount,
    underFairValueCount,
    nearestExpirySeconds,
    potentialProfitAfterTax,
    opportunityConfirmed,
    reason,
  };
}

/**
 * Maps a combineIntelligence() `reason` (plus the backend's raw
 * `signal`) to the text/tone the overlay renders. Never returns a
 * "confirmed"/buy-flavoured tone unless reason is literally "confirmed" -
 * live data alone still cannot manufacture a buy recommendation here
 * either.
 */
export function verdictLabel(reason, signal) {
  switch (reason) {
    case "confirmed":
      return { text: "OPPORTUNITY CONFIRMED", tone: "confirmed" };
    case "above_target":
      return { text: "NO LIVE LISTING BELOW TARGET", tone: "neutral" };
    case "no_live_listings":
      return { text: "NO LIVE BIN LISTINGS", tone: "neutral" };
    case "no_target_price":
      return { text: "NO BUY TARGET AVAILABLE", tone: "neutral" };
    case "no_intelligence":
      return { text: "NO EAFC.LIVE INTELLIGENCE", tone: "neutral" };
    case "no_buy_signal": {
      const label = signal ? String(signal).replace(/_/g, " ").toUpperCase() : null;
      return {
        text: label ? `LIVE MARKET — ${label} (NOT A BUY)` : "LIVE MARKET — NO BUY RECOMMENDATION",
        tone: "neutral",
      };
    }
    default:
      return { text: "NO LIVE LISTING BELOW TARGET", tone: "neutral" };
  }
}
