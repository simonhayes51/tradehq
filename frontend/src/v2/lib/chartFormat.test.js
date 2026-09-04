// The chart maths behind the Player Detail correction pass.
//
// The completed-sales chart scaled to the raw min/max of the series, so
// a single outlier print flattened every real sale into a band a few
// pixels tall against the bottom of the frame. These pin the robust
// domain, the median/typical-range indicator, and the tick thinning that
// stops labels overprinting.
import { describe, expect, it } from "vitest";
import {
  compactCoins,
  evenTicks,
  humaniseSpan,
  percentileOf,
  salesStats,
  tickFormatterForSpan,
} from "./chartFormat";

describe("salesStats", () => {
  it("keeps an outlier from flattening the rest of the series", () => {
    // Nineteen sales clustered around 10,000, plus one at 200,000.
    const values = [...Array.from({ length: 19 }, (_, i) => 9800 + i * 25), 200000];
    const stats = salesStats(values);
    // The domain follows the cluster, not the spike.
    expect(stats.domain[1]).toBeLessThan(50000);
    expect(stats.domain[0]).toBeLessThanOrEqual(9800);
    // The spike is disclosed, not silently cropped.
    expect(stats.outliers).toBe(1);
  });

  it("reports the median and the middle half of sales", () => {
    const stats = salesStats([100, 200, 300, 400, 500]);
    expect(stats.median).toBe(300);
    expect(stats.q1).toBe(200);
    expect(stats.q3).toBe(400);
  });

  it("never returns a zero-height domain when every sale is the same price", () => {
    const stats = salesStats([5000, 5000, 5000, 5000]);
    expect(stats.domain[1]).toBeGreaterThan(stats.domain[0]);
  });

  it("returns null rather than guessing from a single sale", () => {
    expect(salesStats([5000])).toBeNull();
    expect(salesStats([])).toBeNull();
  });

  it("ignores junk values instead of poisoning the scale with NaN", () => {
    const stats = salesStats([1000, 1100, null, undefined, "x", 0, -5, 1200]);
    expect(stats.count).toBe(3);
    expect(stats.domain.every(Number.isFinite)).toBe(true);
  });
});

describe("percentileOf", () => {
  it("interpolates between ranks", () => {
    expect(percentileOf([0, 10], 0.5)).toBe(5);
  });
});

describe("axis ticks", () => {
  it("emits a fixed small number of ticks however dense the series is", () => {
    const points = Array.from({ length: 400 }, (_, i) => ({ time: 1000 + i }));
    expect(evenTicks(points)).toHaveLength(5);
  });

  it("returns undefined for a series too short to have an axis", () => {
    expect(evenTicks([{ time: 1 }])).toBeUndefined();
    expect(evenTicks([])).toBeUndefined();
  });

  it("collapses to one tick when every point shares a timestamp", () => {
    expect(evenTicks([{ time: 5 }, { time: 5 }])).toEqual([5]);
  });
});

describe("tick formatting", () => {
  it("uses clock time when the whole window sits inside a day", () => {
    const format = tickFormatterForSpan(6 * 3600 * 1000);
    expect(format(Date.UTC(2026, 0, 1, 13, 30))).toMatch(/\d{2}:\d{2}/);
  });

  it("uses day labels across a longer span", () => {
    const format = tickFormatterForSpan(5 * 24 * 3600 * 1000);
    expect(format(Date.UTC(2026, 0, 1))).toMatch(/[A-Za-z]{3}/);
  });
});

describe("compactCoins", () => {
  it("keeps large coin figures short enough for an axis", () => {
    expect(compactCoins(950)).toBe("950");
    expect(compactCoins(12500)).toBe("13k");
    expect(compactCoins(2000000)).toBe("2m");
    expect(compactCoins(2425500)).toBe("2.4m");
  });

  it("returns an empty label rather than 'NaN' for junk", () => {
    expect(compactCoins(undefined)).toBe("");
  });
});

describe("humaniseSpan", () => {
  it("describes short and long coverage in the right units", () => {
    expect(humaniseSpan(30 * 60 * 1000)).toBe("30 minutes");
    expect(humaniseSpan(6 * 3600 * 1000)).toBe("6 hours");
    expect(humaniseSpan(3 * 24 * 3600 * 1000)).toBe("3 days");
  });
});
