import { describe, expect, it } from "vitest";
import { DIMENSIONS, formatCoinsCompact, formatCoinsExact, formatRoi, scaledFontSize } from "./shareCardFormat";

describe("formatCoinsExact", () => {
  it("formats with thousands separators", () => {
    expect(formatCoinsExact(1450000)).toBe("1,450,000");
  });

  it("treats null/undefined as 0", () => {
    expect(formatCoinsExact(null)).toBe("0");
    expect(formatCoinsExact(undefined)).toBe("0");
  });
});

describe("formatCoinsCompact", () => {
  it("does not abbreviate below 1000", () => {
    expect(formatCoinsCompact(997)).toBe("997");
  });

  it("abbreviates thousands without a fabricated decimal", () => {
    expect(formatCoinsCompact(997000)).toBe("997K");
  });

  it("keeps two significant decimal digits so a card's exact result is recognisable", () => {
    // The exact bug this guards against: the site-wide compact
    // formatter (CoinValue's coinText) rounds to 1 fraction digit,
    // which would show 1,450,000 as "1.5M" - indistinguishable from a
    // 1,500,000 trade. This formatter is share-card-specific and keeps
    // the second digit.
    expect(formatCoinsCompact(1450000)).toBe("1.45M");
  });

  it("keeps a trailing zero at the million scale rather than trimming it", () => {
    // Every hero figure gets a fixed, pre-measured width: "1.50M" not
    // "1.5M". Trimming would make the width unpredictable per value,
    // reopening the clipping risk a fixed format is meant to close.
    expect(formatCoinsCompact(1500000)).toBe("1.50M");
  });

  it("rounds to the nearest whole thousand below 1M, with no decimal", () => {
    expect(formatCoinsCompact(2195500)).not.toMatch(/K$/);
    expect(formatCoinsCompact(997450)).toBe("997K");
  });

  it("promotes a value that rounds up to a full 1000K into the M tier", () => {
    expect(formatCoinsCompact(999600)).toBe("1.00M");
  });

  it("shows the exact worked examples from the design spec", () => {
    expect(formatCoinsCompact(230000)).toBe("230K");
    expect(formatCoinsCompact(480000)).toBe("480K");
    expect(formatCoinsCompact(226000)).toBe("226K");
    expect(formatCoinsCompact(997000)).toBe("997K");
    expect(formatCoinsCompact(1450000)).toBe("1.45M");
    expect(formatCoinsCompact(1522000)).toBe("1.52M");
    expect(formatCoinsCompact(2195500)).toBe("2.20M");
  });

  it("signs a negative value with a minus, applied before the suffix", () => {
    expect(formatCoinsCompact(-1450000)).toBe("-1.45M");
    expect(formatCoinsCompact(-997000)).toBe("-997K");
  });
});

describe("formatRoi", () => {
  it("signs a positive ROI", () => {
    expect(formatRoi(38.164)).toBe("+38.2%");
  });

  it("signs a negative ROI with a minus, not double-negative", () => {
    expect(formatRoi(-5)).toBe("-5.0%");
  });

  it("returns null (not a fabricated 0%) when there is nothing to divide by", () => {
    expect(formatRoi(null)).toBeNull();
    expect(formatRoi(undefined)).toBeNull();
  });
});

describe("scaledFontSize", () => {
  it("keeps the base size for a figure at or under the threshold", () => {
    expect(scaledFontSize(150, "+425,000", 9)).toBe(150);
  });

  it("shrinks a figure past the threshold instead of letting it clip", () => {
    // Caught in visual QA: an all-time total like "+24,650,000" clipped
    // off the right edge of the 1080-wide portrait card at a flat
    // 150px hero size. Must render smaller, not equal, past threshold.
    const size = scaledFontSize(150, "+24,650,000", 9);
    expect(size).toBeLessThan(150);
    expect(size).toBeGreaterThan(0);
  });

  it("never shrinks below the floor scale", () => {
    const size = scaledFontSize(150, "+123,456,789,000,000,000", 9, 0.4);
    expect(size).toBe(60); // 150 * 0.4
  });
});

describe("DIMENSIONS", () => {
  it("defines the wide (Discord/X) and portrait (TikTok/IG) export sizes", () => {
    expect(DIMENSIONS.wide).toEqual({ width: 900, height: 470 });
    expect(DIMENSIONS.portrait).toEqual({ width: 1080, height: 1350 });
  });
});
