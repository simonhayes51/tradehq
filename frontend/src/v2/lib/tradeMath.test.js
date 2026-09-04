// Tests for the shared EA-tax / break-even / rounding maths.
//
// The headline case is the real portfolio row that prompted this work: a
// Mbappé bought at 2,304,000 with a target of 2,425,500, which the
// portfolio presented as a "projected profit" of 225 coins. The
// arithmetic was never wrong; the presentation was. These tests pin both
// the arithmetic AND the classification that now drives the warning.
import { describe, expect, it } from "vitest";
import {
  breakEvenSalePrice,
  eaTax,
  evaluateOpenTrade,
  MARGINAL_NET_ROI,
  netProfit,
  netSaleProceeds,
  roundToEaIncrement,
  targetSalePrice,
  winRateDisplay,
} from "./tradeMath";

describe("EA tax", () => {
  it("matches close_trade_economics: proceeds are truncated, not rounded", () => {
    // int(sell * 0.95) on the backend. 1001 * 0.95 = 950.95 -> 950.
    expect(netSaleProceeds(1001)).toBe(950);
    expect(eaTax(1001)).toBe(51);
  });

  it("takes exactly 5% on a price that divides cleanly", () => {
    expect(netSaleProceeds(100000)).toBe(95000);
    expect(eaTax(100000)).toBe(5000);
  });

  it("returns null (not 0) for a missing or non-positive price", () => {
    expect(netSaleProceeds(null)).toBeNull();
    expect(netSaleProceeds(0)).toBeNull();
    expect(netSaleProceeds("nonsense")).toBeNull();
  });

  it("scales net profit by quantity", () => {
    // 10,000 buy, 12,000 sell -> 11,400 net -> 1,400 per card.
    expect(netProfit(10000, 12000, 1)).toBe(1400);
    expect(netProfit(10000, 12000, 3)).toBe(4200);
  });

  it("returns a negative number for a genuine loss after tax", () => {
    // A 3% pre-tax gain is a loss once EA takes 5%.
    expect(netProfit(100000, 103000)).toBe(-2150);
  });
});

describe("break-even", () => {
  it("is the lowest listable price that does not lose coins", () => {
    const buy = 100000;
    const be = breakEvenSalePrice(buy);
    // buy / 0.95 = 105,263.16, which sits in the 1,000-coin increment
    // band (>100,000), so the lowest LISTABLE break-even is 106,000.
    expect(be).toBe(106000);
    expect(netProfit(buy, be)).toBeGreaterThanOrEqual(0);
    // And the increment below it genuinely loses.
    expect(netProfit(buy, be - 1000)).toBeLessThan(0);
  });

  it("never rounds down into a loss", () => {
    for (const buy of [250, 900, 5000, 47500, 99000, 640000, 2304000]) {
      const be = breakEvenSalePrice(buy);
      expect(netProfit(buy, be)).toBeGreaterThanOrEqual(0);
    }
  });

  it("returns null with no buy price rather than inventing 0", () => {
    expect(breakEvenSalePrice(null)).toBeNull();
    expect(breakEvenSalePrice(0)).toBeNull();
  });
});

describe("EA price increments", () => {
  it("uses the bracket the price falls in", () => {
    expect(roundToEaIncrement(1_010, "up")).toBe(1100); // 100 band
    expect(roundToEaIncrement(47_600, "up")).toBe(47750); // 250 band
    expect(roundToEaIncrement(2_425_400, "up")).toBe(2430000); // 5000 band
  });

  it("clamps to EA's own price floor and ceiling", () => {
    expect(roundToEaIncrement(10, "down")).toBe(200);
    expect(roundToEaIncrement(99_000_000, "up")).toBe(5000000);
  });
});

describe("evaluateOpenTrade", () => {
  it("flags the Mbappe case as marginal, not as a projected return", () => {
    const result = evaluateOpenTrade({ buy: 2_304_000, target: 2_425_500, quantity: 1 });
    // The arithmetic the interface was already doing, unchanged.
    expect(result.netProfit).toBe(225);
    // 225 on a 2.3m stake is 0.0098% - nowhere near the 2% floor.
    expect(result.netRoi).toBeLessThan(MARGINAL_NET_ROI);
    expect(result.status).toBe("marginal");
    // And we can now say what a safe sale actually costs: the raw
    // break-even is 2,425,263, which rounds up to the next listable
    // price in the 5,000-coin band. The recorded target (2,425,500) is
    // not even a listable price - it came from the old hard-coded
    // 250-coin increment - which is the second half of why 225 coins of
    // "projected profit" was never real.
    expect(result.breakEven).toBe(2430000);
  });

  it("calls a genuinely profitable trade a profit", () => {
    const result = evaluateOpenTrade({ buy: 100_000, target: 130_000, quantity: 1 });
    expect(result.netProfit).toBe(23500);
    expect(result.status).toBe("profit");
  });

  it("classifies an exact break-even target as marginal, never as profit", () => {
    const buy = 100_000;
    const result = evaluateOpenTrade({ buy, target: breakEvenSalePrice(buy) });
    expect(result.netProfit).toBeGreaterThanOrEqual(0);
    // 700 coins on a 100,000 stake: 0.7%, under the 2% floor.
    expect(result.netRoi).toBeLessThan(MARGINAL_NET_ROI);
    expect(result.status).toBe("marginal");
  });

  it("classifies a target that loses money after tax as a loss", () => {
    const result = evaluateOpenTrade({ buy: 100_000, target: 103_000 });
    expect(result.netProfit).toBeLessThan(0);
    expect(result.status).toBe("loss");
  });

  it("does not fabricate a target or a profit when none was recorded", () => {
    const result = evaluateOpenTrade({ buy: 100_000, target: null });
    expect(result.status).toBe("no-target");
    expect(result.netProfit).toBeNull();
    // The break-even is still known - it depends only on the buy price.
    expect(result.breakEven).toBe(106000);
  });

  it("rounding never turns a sub-coin gain into an attractive positive", () => {
    // 1 coin of net profit on a 500k stake must not read as a return.
    const buy = 500_000;
    const target = breakEvenSalePrice(buy);
    const result = evaluateOpenTrade({ buy, target });
    expect(result.status).not.toBe("profit");
  });

  it("respects quantity when scaling the absolute-profit floor", () => {
    // 300 coins/card is marginal on one card and still marginal on ten,
    // because the ROI is what decides it.
    expect(evaluateOpenTrade({ buy: 100_000, target: 105_900, quantity: 1 }).status).toBe("marginal");
    expect(evaluateOpenTrade({ buy: 100_000, target: 105_900, quantity: 10 }).status).toBe("marginal");
  });
});

describe("targetSalePrice", () => {
  it("clears the requested net ROI after tax", () => {
    const buy = 50_000;
    const target = targetSalePrice(buy, 0.15);
    expect(netProfit(buy, target) / buy).toBeGreaterThanOrEqual(0.15);
  });
});

describe("winRateDisplay", () => {
  it("shows a raw count instead of 100% on one closed trade", () => {
    expect(winRateDisplay(1, 1)).toEqual({ display: "1 of 1", isRate: false, sample: 1 });
  });

  it("still shows a count at four closed trades", () => {
    expect(winRateDisplay(3, 4).display).toBe("3 of 4");
  });

  it("shows a percentage once there are enough closed trades", () => {
    expect(winRateDisplay(4, 5)).toEqual({ display: "80.0%", isRate: true, sample: 5 });
  });

  it("shows a dash, not 0%, with nothing closed", () => {
    expect(winRateDisplay(0, 0).display).toBe("—");
  });
});
