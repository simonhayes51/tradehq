import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import TrackRecordPanel from "./TrackRecordPanel";
import { useTrackRecord, useRecentOutcomes } from "../hooks/useTrackRecord";

vi.mock("../hooks/useTrackRecord", () => ({
  useTrackRecord: vi.fn(),
  useRecentOutcomes: vi.fn(),
}));

function trackRecordResult(summary, overrides = {}) {
  return {
    isLoading: false,
    isError: false,
    data: { summary, ...overrides },
  };
}

function recentOutcomesResult(outcomes) {
  return { isLoading: false, isError: false, data: { outcomes } };
}

const ENOUGH_SUMMARY = {
  total_graded: 4033,
  entered: 2500,
  no_entry: 1533,
  entry_rate_pct: 62.0,
  win_rate_pct: 75.5,
  target_hit_rate_pct: 60.2,
  avg_roi_pct: 8.1,
  median_roi_pct: 6.4,
  avg_hold_minutes: 95,
  has_enough_data: true,
  min_sample: 20,
};

const THIN_SUMMARY = {
  total_graded: 5,
  entered: 3,
  no_entry: 2,
  entry_rate_pct: null,
  win_rate_pct: null,
  target_hit_rate_pct: null,
  avg_roi_pct: null,
  median_roi_pct: null,
  avg_hold_minutes: null,
  has_enough_data: false,
  min_sample: 20,
};

describe("TrackRecordPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing while loading or on error", () => {
    useTrackRecord.mockReturnValue({ isLoading: true, isError: false, data: undefined });
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([]));
    const { container: loadingContainer } = render(<TrackRecordPanel />);
    expect(loadingContainer.firstChild).toBeNull();

    useTrackRecord.mockReturnValue({ isLoading: false, isError: true, data: undefined });
    const { container: errorContainer } = render(<TrackRecordPanel />);
    expect(errorContainer.firstChild).toBeNull();
  });

  it("shows the true entered-trade base next to the win rate, not the larger total-graded count", () => {
    // Regression for the exact bug this rebuild fixes: the old panel
    // showed "75.5% hit target · 40,330 closed calls" where 40,330 was
    // total graded calls, not the entered count the percentage is
    // actually computed over.
    useTrackRecord.mockReturnValue(trackRecordResult(ENOUGH_SUMMARY));
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([]));
    render(<TrackRecordPanel />);

    expect(screen.getByText("75.5%")).toBeInTheDocument();
    expect(screen.getByText("of 2,500 entered trades")).toBeInTheDocument();
    // The total-graded figure is shown too, but as its own separate,
    // correctly-labelled tile - never paired with the win rate.
    expect(screen.getByText("4,033")).toBeInTheDocument();
  });

  it("shows the no-entry count separately and never folds it into win rate", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(ENOUGH_SUMMARY));
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([]));
    render(<TrackRecordPanel />);
    expect(screen.getByText("1,533")).toBeInTheDocument();
    expect(screen.getAllByText(/excluded from win rate/i).length).toBeGreaterThan(0);
  });

  it("suppresses percentages and shows an honest empty state below the minimum sample", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(THIN_SUMMARY));
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([]));
    render(<TrackRecordPanel />);
    expect(screen.getByText(/not enough graded outcomes/i)).toBeInTheDocument();
    expect(screen.queryByText("75.5%")).not.toBeInTheDocument();
  });

  it("renders a mix of wins and losses in the recent outcomes list, each labelled", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(ENOUGH_SUMMARY));
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([
      {
        outcome_id: 1, player_name: "Winner Card", rating: 91, rarity: "gold_rare",
        result: "win", entry_achieved: true, entry_price: 50000,
        net_profit_after_tax: 5200, realised_roi_pct: 10.4,
        buy_threshold: 50000, detected_at: "2026-08-01T00:00:00Z", image_url: null,
      },
      {
        outcome_id: 2, player_name: "Loser Card", rating: 88, rarity: "gold_rare",
        result: "loss", entry_achieved: true, entry_price: 30000,
        net_profit_after_tax: -1800, realised_roi_pct: -6.0,
        buy_threshold: 30000, detected_at: "2026-08-02T00:00:00Z", image_url: null,
      },
      {
        outcome_id: 3, player_name: "Never Entered Card", rating: 85, rarity: "gold_rare",
        result: "no_entry", entry_achieved: false, entry_price: null,
        net_profit_after_tax: null, realised_roi_pct: null,
        buy_threshold: 20000, detected_at: "2026-08-03T00:00:00Z", image_url: null,
      },
    ]));
    render(<TrackRecordPanel />);

    expect(screen.getByText("Winner Card")).toBeInTheDocument();
    expect(screen.getByText("Loser Card")).toBeInTheDocument();
    expect(screen.getByText("Never Entered Card")).toBeInTheDocument();
    expect(screen.getByText("Win")).toBeInTheDocument();
    expect(screen.getByText("Loss")).toBeInTheDocument();
    expect(screen.getAllByText("No entry").length).toBeGreaterThan(0);
    expect(screen.getByText("Buy price never reached")).toBeInTheDocument();
  });
});
