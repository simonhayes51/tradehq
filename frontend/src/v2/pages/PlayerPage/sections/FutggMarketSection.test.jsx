import { describe, expect, it, vi, afterEach } from "vitest";
import { RECENT_SALES_ROWS, saleAgeLabel, saleTitle } from "./FutggMarketSection";

// 2026-08-11 22:11 UTC - the moment the production screenshots were taken.
const NOW = new Date("2026-08-11T22:11:00Z").getTime();

afterEach(() => vi.useRealTimers());

function freezeNow() {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
}

describe("saleAgeLabel", () => {
  it("ages a sale from its timestamp, NOT from the scraper's frozen string", () => {
    // The production bug, exactly. Card 218342893's "Recent sales" table
    // showed eleven rows reading "2 minutes ago" through "25 minutes ago"
    // while the analysis panel on the same page said the newest completed
    // sale was 92 hours old - and both were right about their own field.
    // age_text is what FUT.GG rendered at SCRAPE time and never changes
    // afterwards, so a sale scraped four days ago claims to be minutes
    // old forever.
    freezeNow();
    const sale = {
      sold_price: 995_000,
      age_text: "2 minutes ago",
      approximate_sold_at: new Date(NOW - 92 * 3600 * 1000).toISOString(),
    };
    expect(saleAgeLabel(sale)).toBe("4 days ago");
    expect(saleAgeLabel(sale)).not.toContain("2 minutes");
  });

  it("reports a genuinely fresh sale as fresh", () => {
    freezeNow();
    expect(
      saleAgeLabel({ approximate_sold_at: new Date(NOW - 2 * 60 * 1000).toISOString() }),
    ).toBe("2 minutes ago");
  });

  it("marks the fallback as scrape-time when there is no timestamp at all", () => {
    // Still better than nothing, but it must never read as a live age.
    expect(saleAgeLabel({ age_text: "2 minutes ago" })).toBe("2 minutes ago (at scrape time)");
  });

  it("says so plainly when the row carries no usable time", () => {
    expect(saleAgeLabel({})).toBe("approximate time unavailable");
  });

  it("does not trust an unparseable timestamp", () => {
    expect(saleAgeLabel({ approximate_sold_at: "not-a-date", age_text: "5 minutes ago" }))
      .toBe("5 minutes ago (at scrape time)");
  });
});

describe("saleTitle", () => {
  it("keeps the scraper's own wording as a detail, attributed to FUT.GG", () => {
    const title = saleTitle({
      age_text: "2 minutes ago",
      approximate_sold_at: "2026-08-08T02:11:00.000Z",
    });
    expect(title).toContain("Approximate:");
    expect(title).toContain('FUT.GG said "2 minutes ago"');
  });

  it("is undefined when there is nothing to say", () => {
    expect(saleTitle({})).toBeUndefined();
  });
});

describe("RECENT_SALES_ROWS", () => {
  it("caps the table well below the endpoint's 500-row ceiling", () => {
    expect(RECENT_SALES_ROWS).toBeLessThanOrEqual(50);
  });
});
