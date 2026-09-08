import { describe, expect, it } from "vitest";
import { bucketApproximateSales } from "./ModalPriceChart";

const HOUR = 3600 * 1000;

describe("bucketApproximateSales", () => {
  it("collapses multiple approximate sales in the same chart bucket to one median point", () => {
    const base = Date.UTC(2026, 8, 8, 8, 0, 0);
    const points = [
      { time: base + 5 * 60 * 1000, price: 300, approximate: true },
      { time: base + 15 * 60 * 1000, price: 500, approximate: true },
      { time: base + 35 * 60 * 1000, price: 700, approximate: true },
      { time: base + 5 * HOUR, price: 900, approximate: true },
    ];

    const bucketed = bucketApproximateSales(points, 4);

    expect(bucketed).toHaveLength(2);
    expect(bucketed[0].price).toBe(500);
    expect(bucketed[1].price).toBe(900);
    expect(bucketed[0].time).toBe(points[1].time);
  });
});
