import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ReleasePriceRises from "./ReleasePriceRises";
import { getReleasePriceRises } from "../../api/releasePriceRises";

vi.mock("../../api/releasePriceRises", () => ({ getReleasePriceRises: vi.fn() }));

const RESPONSE = {
  game_year: 26,
  early_date: "2025-09-17",
  full_date: "2025-09-24",
  include_uncertain: false,
  total: 72,
  page: 1,
  page_size: 60,
  playbook: {
    headline: "Best FC27 watch profile: CM + 80-84 + 1k-5k",
    summary: "The signal is ranked from the actual FC26 data by median rise versus the overall market. Treat it as a shortlist, then verify current FC27 prices before buying.",
    cards: [
      { label: "Biggest riser", value: "+150%", copy: "Fast ST moved from 1,000 to 2,500 coins.", card_id: 1 },
      { label: "Best coin return", value: "+1,375", copy: "After EA tax, Fast ST produced the biggest coin gain.", card_id: 1 },
    ],
  },
  trends: {
    key_signals: [
      { dimension: "Position", label: "CM", median_pct_change: 120, median_profit_after_tax: 1100, card_count: 12, lift_vs_all_pct_points: 44.5 },
      { dimension: "Rating band", label: "80-84", median_pct_change: 95, median_profit_after_tax: 850, card_count: 24, lift_vs_all_pct_points: 19.5 },
      { dimension: "Price band", label: "1k-5k", median_pct_change: 80, median_profit_after_tax: 700, card_count: 18, lift_vs_all_pct_points: 4.5 },
    ],
    by_position: [{ position: "CM", median_pct_change: 120, median_profit_after_tax: 1100, card_count: 12 }],
    by_pace_tier: [],
    by_price_band: [{ price_band: "1k-5k", median_pct_change: 80, median_profit_after_tax: 700, card_count: 18 }],
    by_rating_band: [{ rating_band: "80-84", median_pct_change: 95, median_profit_after_tax: 850, card_count: 24 }],
    by_nation: [],
    by_league: [],
    by_rarity: [],
  },
  items: [
    {
      card_id: 1,
      name: "Fast ST",
      rating: 82,
      position: "ST",
      rarity: "gold_rare",
      pace: 91,
      image_url: null,
      early_price: 1000,
      full_price: 2500,
      abs_change: 1500,
      pct_change: 150,
      net_profit_after_tax: 1375,
      net_roi_after_tax: 137.5,
      price_band: { key: "1k_5k", label: "1k-5k" },
    },
  ],
};

describe("ReleasePriceRises", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getReleasePriceRises.mockResolvedValue(RESPONSE);
  });

  it("renders a simple launch answer from data-ranked signals", async () => {
    render(<MemoryRouter><ReleasePriceRises /></MemoryRouter>);

    expect(await screen.findByText("Launch Movers")).toBeInTheDocument();
    expect(screen.getByText("Early rush")).toBeInTheDocument();
    expect(screen.getByText("CM + 80-84 + 1k-5k")).toBeInTheDocument();
    expect(screen.getByText("1. Strongest trend")).toBeInTheDocument();
    expect(screen.getByText("Position: CM")).toBeInTheDocument();
    expect(screen.getByText("Rating band: 80-84")).toBeInTheDocument();
    expect(screen.getByText(/44.5 pts vs all cards/)).toBeInTheDocument();
    expect(screen.getByText("Proof list")).toBeInTheDocument();
    expect(screen.getAllByText(/after tax/i).length).toBeGreaterThan(0);
  });

  it("passes search and rating filters to the server instead of filtering a local top page", async () => {
    render(<MemoryRouter><ReleasePriceRises /></MemoryRouter>);

    await waitFor(() => expect(getReleasePriceRises).toHaveBeenCalled());
    fireEvent.click(screen.getByText("Refine the proof list"));
    fireEvent.change(screen.getByPlaceholderText("Search player..."), { target: { value: "rashford" } });
    fireEvent.change(screen.getByLabelText("Minimum rating"), { target: { value: "85" } });

    await waitFor(() => {
      expect(getReleasePriceRises).toHaveBeenLastCalledWith(expect.objectContaining({
        q: "rashford",
        min_rating: 85,
        page: 1,
      }));
    });
  });

  it("uses API pagination controls", async () => {
    render(<MemoryRouter><ReleasePriceRises /></MemoryRouter>);

    expect(await screen.findByText("1-60 of 72")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => {
      expect(getReleasePriceRises).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    });
  });
});