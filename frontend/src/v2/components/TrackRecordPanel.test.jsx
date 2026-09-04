import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import TrackRecordPanel from "./TrackRecordPanel";
import { useTrackRecord, useRecentOutcomes } from "../hooks/useTrackRecord";

vi.mock("../hooks/useTrackRecord", () => ({
  useTrackRecord: vi.fn(),
  useRecentOutcomes: vi.fn(),
}));

function trackRecordResult(published, model_validation = { graded_signals: 0 }) {
  return { isLoading: false, isError: false, data: { published, model_validation, strategies: [] } };
}

function recentOutcomesResult(outcomes) {
  return { isLoading: false, isError: false, data: { outcomes } };
}

const PUBLISHED_WITH_DATA = {
  has_published_data: true,
  has_enough_data: true,
  min_sample: 20,
  tracking_since: "2026-06-01",
  published_opportunities: 60,
  entry_reached: 60,
  entry_reached_not_tracked: true,
  wins: 45,
  losses: 15,
  flats: 0,
  win_rate_pct: 75.0,
  total_profit_after_tax: 320000,
  median_profit_after_tax: 5200,
  median_roi_pct: 8.1,
  median_time_to_target_minutes: 95,
  recent_wins: [],
  recent_losses: [],
};

const PUBLISHED_NO_DATA = {
  has_published_data: false,
  tracking_since: null,
};

const PUBLISHED_THIN = {
  has_published_data: true,
  has_enough_data: false,
  min_sample: 20,
  published_opportunities: 5,
  tracking_since: "2026-08-01",
};

describe("TrackRecordPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useRecentOutcomes.mockReturnValue(recentOutcomesResult([]));
  });

  it("renders an explicit loading state", () => {
    useTrackRecord.mockReturnValue({ isLoading: true, isError: false, data: undefined });
    render(<TrackRecordPanel />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading published results/i);
  });

  it("renders a recoverable error instead of a blank page", () => {
    useTrackRecord.mockReturnValue({
      isLoading: false,
      isError: true,
      data: undefined,
      refetch: vi.fn(),
    });
    render(<TrackRecordPanel />);
    expect(screen.getByText(/track record could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("never conflates published recommendations with internal model signals", () => {
    // Regression for the exact hotfix bug: the old panel headlined
    // "DID OUR CALLS ACTUALLY WORK?" over 8,000+ internal model signals
    // while its own subtitle admitted they weren't real published
    // recommendations. The rebuilt panel's headline and primary numbers
    // must come from `published`, never `model_validation`.
    useTrackRecord.mockReturnValue(trackRecordResult(PUBLISHED_WITH_DATA, {
      graded_signals: 8134, entry_reached: 8014, entry_not_reached: 120,
      has_enough_data: true, min_sample: 20, profitable_rate_pct: 63.9,
      median_roi_pct: 22.0, avg_hold_minutes: 290,
    }));
    render(<TrackRecordPanel />);

    // Primary numbers are the published ones (45 wins, 75.0% win rate).
    expect(screen.getByText("45")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument();
    // The large internal signal count only appears inside the separate,
    // collapsed-by-default Model Validation section - not as part of
    // the primary published summary grid.
    const publishedGrid = document.querySelector(".track-record-grid.compact-grid");
    expect(publishedGrid?.textContent).not.toMatch(/8,134|8,014/);
  });

  it("shows an honest empty state instead of fabricating published data", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(PUBLISHED_NO_DATA));
    render(<TrackRecordPanel />);
    expect(screen.getByText(/no published recommendations tracked yet/i)).toBeInTheDocument();
    expect(screen.queryByText("75.0%")).not.toBeInTheDocument();
  });

  it("suppresses published percentages below the minimum sample", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(PUBLISHED_THIN));
    render(<TrackRecordPanel />);
    expect(screen.getByText(/not enough published outcomes yet/i)).toBeInTheDocument();
  });

  it("shows both recent wins and recent losses by default, no filter needed", () => {
    const published = {
      ...PUBLISHED_WITH_DATA,
      recent_wins: [
        { outcome_id: "1:24h", player_name: "Winner Card", card_rating: 91, rarity: "gold_rare",
          detected_at: "2026-08-01T00:00:00Z", entry_price: 50000, net_profit_after_tax: 5200, realised_roi_pct: 10.4 },
      ],
      recent_losses: [
        { outcome_id: "2:24h", player_name: "Loser Card", card_rating: 88, rarity: "gold_rare",
          detected_at: "2026-08-02T00:00:00Z", entry_price: 30000, net_profit_after_tax: -1800, realised_roi_pct: -6.0 },
      ],
    };
    useTrackRecord.mockReturnValue(trackRecordResult(published));
    render(<TrackRecordPanel />);
    expect(screen.getByText("Recent wins")).toBeInTheDocument();
    expect(screen.getByText("Recent losses")).toBeInTheDocument();
    expect(screen.getByText("Winner Card")).toBeInTheDocument();
    expect(screen.getByText("Loser Card")).toBeInTheDocument();
  });

  it("explains identical numbers across windows when tracking history is younger than the selected window", () => {
    // Regression: a Discord Proof Ledger that only started sending 3
    // days ago shows byte-identical stats for 7d/30d/90d/All time -
    // there's nothing older to differ on. Without an explanation this
    // reads as a bug rather than the honest, expected state of a young
    // feature.
    const threeDaysAgo = new Date(Date.now() - 3 * 86_400_000).toISOString();
    useTrackRecord.mockReturnValue(trackRecordResult({ ...PUBLISHED_WITH_DATA, tracking_since: threeDaysAgo }));
    render(<TrackRecordPanel />); // default window is 90d, well past 3 days of history
    expect(screen.getByText(/tracking published recommendations since/i)).toBeInTheDocument();
    expect(screen.getByText(/not a bug/i)).toBeInTheDocument();
  });

  it("does not show the tracking-history note once history covers the selected window", () => {
    useTrackRecord.mockReturnValue(trackRecordResult({ ...PUBLISHED_WITH_DATA, tracking_since: "2020-01-01" }));
    render(<TrackRecordPanel />);
    expect(screen.queryByText(/tracking published recommendations since/i)).not.toBeInTheDocument();
  });

  it("keeps model validation collapsed by default and labelled as signals, not trades", () => {
    useTrackRecord.mockReturnValue(trackRecordResult(PUBLISHED_WITH_DATA, {
      graded_signals: 8134, entry_reached: 8014, entry_not_reached: 120,
      has_enough_data: true, min_sample: 20, profitable_rate_pct: 63.9,
    }));
    const { container } = render(<TrackRecordPanel />);
    const details = container.querySelector(".track-record-model-validation");
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(screen.getAllByText(/model signal validation/i).length).toBeGreaterThan(0);
  });
});
