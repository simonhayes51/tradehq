import { describe, expect, it } from "vitest";
import { normaliseDashboard } from "./useDashboard";

describe("normaliseDashboard", () => {
  it("does not present historical alert rows as current watchlist alerts when the watchlist is empty", () => {
    const result = normaliseDashboard({
      watchedCount: 0,
      unreadAlertCount: 2,
      watchlistAlerts: [{ id: 1, title: "Old Pele alert" }],
      latestMarketEvents: [{ id: 2 }],
    });

    expect(result.watchlistAlerts).toEqual([]);
    expect(result.unreadAlertCount).toBe(0);
    expect(result.latestMarketEvents).toEqual([{ id: 2 }]);
  });

  it("keeps current watchlist alerts when cards are still watched", () => {
    const data = { watchedCount: 1, unreadAlertCount: 1, watchlistAlerts: [{ id: 1 }] };
    expect(normaliseDashboard(data)).toBe(data);
  });
});
