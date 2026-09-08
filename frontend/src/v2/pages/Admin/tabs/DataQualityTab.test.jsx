import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const query = vi.hoisted(() => ({
  value: {
    data: {
      intelligence: {
        status: "ok",
        current_coverage_pct: 98.5,
        current_rows: 15700,
        player_pool: 15940,
        newest_age_minutes: 7,
        evaluated_24h_pct: 96.2,
        evaluated_24h: 15334,
        signals: { buy: 12, watch: 80, avoid: 500 },
        transitions_1h: 4,
        transitions_24h: 62,
        dashboard_market_cache: {
          source: "persisted",
          cards: 1288,
          age_seconds: 22,
          live_fallback_enabled: false,
        },
      },
      salesShadow: {
        status: "ok",
        cycles: 30,
        avg_live_due_per_cycle: 60,
        avg_shadow_priority_per_cycle: 11.4,
        held_position_shadow_events: 8,
        reason_counts: { watchlist: 22, held_position: 8 },
        extra_futgg_sales_requests: 0,
        pickup_analysis: {
          pickup_rate_pct: 42.5,
          still_missed_after_window: 23,
          measured_shadow_card_events: 40,
          pending_shadow_card_events: 2,
          user_driven_pickup_rate_pct: 31.2,
          repeated_missed_cards: [
            {
              card_id: 82092,
              name: "Mario Kempes",
              rating: 88,
              rarity: "Icon",
              current_bin: 77000,
              current_signal: "watch",
              missed_events: 3,
              user_driven_events: 3,
              reasons: { held_position: 3 },
            },
          ],
        },
      },
    },
    isLoading: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  },
}));

vi.mock("../../../hooks/useAdminDataQuality", () => ({
  useAdminDataQuality: () => query.value,
}));

import DataQualityTab from "./DataQualityTab";

describe("DataQualityTab", () => {
  beforeEach(() => {
    query.value.refetch.mockClear();
  });

  it("shows persisted intelligence health and repeated scheduler misses", () => {
    render(<DataQualityTab />);

    expect(screen.getByText("Data Quality")).toBeInTheDocument();
    expect(screen.getByText("98.5%")).toBeInTheDocument();
    expect(screen.getByText("OFF")).toBeInTheDocument();
    expect(screen.getByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("40 mature · 2 pending")).toBeInTheDocument();
    expect(screen.getByText("Mario Kempes")).toBeInTheDocument();
    expect(screen.getByText("77,000")).toBeInTheDocument();
    expect(screen.getByText("held position ×3")).toBeInTheDocument();
  });

  it("allows an operator to refresh the evidence", () => {
    render(<DataQualityTab />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(query.value.refetch).toHaveBeenCalledTimes(1);
  });
});
