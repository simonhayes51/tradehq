import { describe, expect, it } from "vitest";
import { normaliseDashboard } from "./useDashboard";

describe("normaliseDashboard", () => {
  it("does not show historical alerts as affecting an empty watchlist", () => {
    const result = normaliseDashboard({
      watchedCount: 0,
      watchlistAlerts: [{ id: 1, cardId: 95, title: "Pelé is up" }],
    });

    expect(result.watchlistAlerts).toEqual([]);
  });

  it("uses the actual current watchlist even when the dashboard aggregate is stale", () => {
    const result = normaliseDashboard({
      watchedCount: 1,
      unreadAlertCount: 1,
      watchlistAlerts: [{ id: 1, cardId: 95, title: "Pelé is up" }],
    }, []);

    expect(result.watchedCount).toBe(0);
    expect(result.watchlistAlerts).toEqual([]);
    expect(result.unreadAlertCount).toBe(0);
  });

  it("filters removed-card alerts while keeping alerts for cards still watched", () => {
    const result = normaliseDashboard({
      watchedCount: 2,
      watchlistAlerts: [
        { id: 1, cardId: 95, title: "Pelé is up" },
        { id: 2, cardId: 123, title: "Current card is down" },
      ],
    }, [{ card_id: 123 }]);

    expect(result.watchlistAlerts).toEqual([{ id: 2, cardId: 123, title: "Current card is down" }]);
  });

  it("keeps aggregate alerts as a fallback if the live watchlist request fails", () => {
    const alerts = [{ id: 1, title: "Pelé is up" }];
    const result = normaliseDashboard({ watchedCount: 1, watchlistAlerts: alerts });

    expect(result.watchlistAlerts).toBe(alerts);
  });
});
