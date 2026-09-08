import { describe, expect, it } from "vitest";
import { normaliseDashboard } from "./useDashboard";

describe("normaliseDashboard", () => {
  it("does not show historical alerts as affecting an empty watchlist", () => {
    const result = normaliseDashboard({
      watchedCount: 0,
      watchlistAlerts: [{ id: 1, title: "Pelé is up" }],
    });

    expect(result.watchlistAlerts).toEqual([]);
  });

  it("keeps current watchlist alerts when cards are still watched", () => {
    const alerts = [{ id: 1, title: "Pelé is up" }];
    const result = normaliseDashboard({ watchedCount: 1, watchlistAlerts: alerts });

    expect(result.watchlistAlerts).toBe(alerts);
  });
});
