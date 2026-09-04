// src/v2/lib/tradeMath.js
//
// The one place the frontend does EA-tax, break-even, target and
// net-profit arithmetic.
//
// Before this file existed the same maths was written out inline in at
// least five places, and they did not agree with each other:
//
//   Club.jsx (open trades)  (Math.floor(target * .95) - buy) * qty
//   Club.jsx (close modal)  tax = Math.ceil(sell * .05) * qty
//   AddTrade.jsx            tax = Math.floor(sell * qty * 0.05)
//   SettingsContext.jsx     calcTax = Math.floor(sell * 0.05)
//   LogTradeModal.jsx       Math.ceil(buy * (1 + roi/100) / 0.95 / 250) * 250
//   HomeDashboard.jsx       Math.ceil((entry + gain) / .95 / 250) * 250
//
// Three different tax roundings and a hard-coded 250-coin listing
// increment that is only correct in one price band. Every one of those
// call sites now routes through here.
//
// The definitions mirror the backend, which is the source of truth:
//   app/services/trade_economics.py  (close_trade_economics - what is
//     actually persisted on a closed trade)
//   app/services/trading_math.py     (break_even_sale_price,
//     round_to_ea_increment, PRICE_INCREMENT_TABLE)
// If those change, change these - they are asserted against each other
// in tradeMath.test.js.

export const EA_TAX_RATE = 0.05;

// Mirrors app/services/trading_math.py's PRICE_INCREMENT_TABLE.
// (upperBoundInclusive, increment). Prices above the last threshold use
// the last bracket's increment. FLAGGED there as unverified for FC26;
// the same caveat applies here - it is used only to avoid suggesting a
// price EA would not accept, never to compute a profit figure.
const PRICE_INCREMENT_TABLE = [
  [1000, 50],
  [10000, 100],
  [50000, 250],
  [100000, 500],
  [200000, 1000],
  [1000000, 5000],
  [5000001, 10000],
];
const EA_MIN_PRICE = 200;
const EA_MAX_PRICE = 5000000;

function incrementFor(price) {
  for (const [bound, step] of PRICE_INCREMENT_TABLE) {
    if (price <= bound) return step;
  }
  return PRICE_INCREMENT_TABLE[PRICE_INCREMENT_TABLE.length - 1][1];
}

/**
 * Round a price to a value EA would actually accept as a listing.
 * direction: "up" | "down" | "nearest".
 */
export function roundToEaIncrement(price, direction = "nearest") {
  const value = Number(price);
  if (!Number.isFinite(value)) return null;
  const step = incrementFor(value);
  const quotient = value / step;
  let units;
  if (direction === "up") units = Math.ceil(quotient);
  else if (direction === "down") units = Math.floor(quotient);
  else units = Math.round(quotient);
  return Math.min(Math.max(units * step, EA_MIN_PRICE), EA_MAX_PRICE);
}

/**
 * What the seller actually receives for ONE card sold at `salePrice`.
 *
 * Truncated, exactly as app/services/trade_economics.py does with
 * `int(sell * (1 - EA_TAX_RATE))` - so a projected profit computed here
 * can never come out higher than the profit the backend will persist
 * when the same trade is closed at the same price.
 */
export function netSaleProceeds(salePrice) {
  const sale = Number(salePrice);
  if (!Number.isFinite(sale) || sale <= 0) return null;
  return Math.floor(sale * (1 - EA_TAX_RATE));
}

/** EA's cut on ONE card sold at `salePrice`. The complement of the above. */
export function eaTax(salePrice) {
  const sale = Number(salePrice);
  const net = netSaleProceeds(sale);
  if (net == null) return null;
  return sale - net;
}

/**
 * Net profit for `quantity` cards bought at `buyPrice`, sold at
 * `salePrice`. null when either price is missing - never 0, which would
 * read as a confident "this trade breaks even" answer.
 */
export function netProfit(buyPrice, salePrice, quantity = 1) {
  const buy = Number(buyPrice);
  const net = netSaleProceeds(salePrice);
  if (net == null || !Number.isFinite(buy) || buy <= 0) return null;
  const qty = Math.max(1, Number(quantity) || 1);
  return (net - buy) * qty;
}

/**
 * The lowest price this card can be SOLD at without losing coins.
 *
 * buy / 0.95, rounded UP to a listable price - rounding down would
 * produce a "break-even" that is actually a small loss.
 */
export function breakEvenSalePrice(buyPrice) {
  const buy = Number(buyPrice);
  if (!Number.isFinite(buy) || buy <= 0) return null;
  return roundToEaIncrement(buy / (1 - EA_TAX_RATE), "up");
}

/**
 * A sell target that clears `buyPrice` by `minimumNetRoi` (as a
 * fraction, e.g. 0.15 for 15%), rounded up to a listable price.
 * Mirrors trading_math.strategy_target_price.
 */
export function targetSalePrice(buyPrice, minimumNetRoi) {
  const buy = Number(buyPrice);
  const roi = Number(minimumNetRoi);
  if (!Number.isFinite(buy) || buy <= 0 || !Number.isFinite(roi)) return null;
  return roundToEaIncrement((buy * (1 + roi)) / (1 - EA_TAX_RATE), "up");
}

// --- Break-even credibility -------------------------------------------
//
// The bug this exists for: a real portfolio row showed Mbappé bought at
// 2,304,000 with a target of 2,425,500 and a "projected profit" of 225
// coins. The arithmetic was right - floor(2,425,500 x 0.95) = 2,304,225 -
// but 225 coins on a 2.3m stake is 0.0098%, i.e. break-even dressed up
// as a return. One bad listing, one price tick, and it is a loss.
//
// THRESHOLD (a judgement call, documented so it can be argued with):
// a target is "marginal" when the net profit is less than 2% of the
// coins staked, OR less than 500 coins in absolute terms on small
// stakes. 2% is deliberately just above the noise floor of a single EA
// price increment at most price bands, and comfortably below any margin
// the recommendation engine itself would call a trade
// (strategy_config's minimum net ROI gates sit far above it).
export const MARGINAL_NET_ROI = 0.02;
export const MARGINAL_ABSOLUTE_PROFIT = 500;

/**
 * The full economics of an open position, in one object, so every
 * surface shows the same numbers.
 *
 *   { stake, breakEven, netProfit, netRoi, status }
 *
 * status is one of:
 *   "no-target"  - no target price to evaluate; we do not invent one
 *   "loss"       - the target sells at a loss after tax
 *   "marginal"   - profitable, but at or barely above break-even
 *   "profit"     - a genuine return
 */
export function evaluateOpenTrade({ buy, target, quantity = 1 }) {
  const buyPrice = Number(buy);
  const qty = Math.max(1, Number(quantity) || 1);
  const stake = Number.isFinite(buyPrice) && buyPrice > 0 ? buyPrice * qty : null;
  const breakEven = breakEvenSalePrice(buyPrice);
  const targetPrice = Number(target);
  const hasTarget = Number.isFinite(targetPrice) && targetPrice > 0;

  if (!hasTarget || stake == null) {
    return { stake, breakEven, netProfit: null, netRoi: null, status: "no-target" };
  }

  const profit = netProfit(buyPrice, targetPrice, qty);
  const roi = profit == null || !stake ? null : profit / stake;
  let status;
  if (profit == null) status = "no-target";
  else if (profit <= 0) status = "loss";
  else if (roi < MARGINAL_NET_ROI && profit < MARGINAL_ABSOLUTE_PROFIT * qty) status = "marginal";
  else if (roi < MARGINAL_NET_ROI) status = "marginal";
  else status = "profit";

  return { stake, breakEven, netProfit: profit, netRoi: roi, status };
}

// --- Win rate ---------------------------------------------------------
//
// "100% win rate" from a single closed trade is not a statistic, it is a
// coin flip with a percent sign. Below this many closed trades we show
// the raw count ("1 of 1 profitable") instead of a rate.
export const MIN_WIN_RATE_SAMPLE = 5;

/**
 * @returns { display, isRate, sample } - `display` is ready to render.
 */
export function winRateDisplay(wins, closed) {
  const total = Number(closed) || 0;
  const won = Number(wins) || 0;
  if (total <= 0) return { display: "—", isRate: false, sample: 0 };
  if (total < MIN_WIN_RATE_SAMPLE) {
    return { display: `${won} of ${total}`, isRate: false, sample: total };
  }
  return { display: `${((won / total) * 100).toFixed(1)}%`, isRate: true, sample: total };
}
