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
    headline: "Best FC27 watch profile: ST + 1k-5k + gold_rare",
    summary: "Use the FC26 launch window as evidence for what to shortlist early in FC27. It is historical pattern matching, not a guaranteed buy signal.",
    cards: [
      { label: "Biggest riser", value: "+150%", copy: "Fast ST moved from 1,000 to 2,500 coins.", card_id: 1 },
      { label: "Best coin return", value: "+1,375", copy: "After EA tax, Fast ST produced the biggest coin gain.", card_id: 1 },
    ],
  },
  trends: {
    by_position: [{ position: "ST", median_pct_change: 120, median_profit_after_tax: 1100, card_count: 12 }],
    by_pace_tier: [
      { group: "high_pace", label: "Pace 85+", median_pct_change: 90, median_profit_after_tax: 800, card_count: 20 },
      { group: "low_pace", label: "Pace under 85", median_pct_change: 30, median_profit_after_tax: 120, card_count: 30 },
    ],
    by_price_band: [{ price_band: "1k-5k", median_pct_change: 80, median_profit_after_tax: 700, card_count: 18 }],
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

  it("renders the launch playbook and after-tax movement", async () => {
    render(<MemoryRouter><ReleasePriceRises /></MemoryRouter>);

    expect(await screen.findByText("Launch Movers")).toBeInTheDocument();
    expect(screen.getByText("ST + 1k-5k + gold_rare")).toBeInTheDocument();\n    expect(screen.getByText("Day 1 to full release")).toBeInTheDocument();
    expect(screen.getByText("Best coin return")).toBeInTheDocument();
    expect(screen.getAllByText(/tax net|after tax/i).length).toBeGreaterThan(0);
    expect(screen.getByText("Budget")).toBeInTheDocument();
  });

  it("passes search and rating filters to the server instead of filtering a local top page", async () => {
    render(<MemoryRouter><ReleasePriceRises /></MemoryRouter>);

    await waitFor(() => expect(getReleasePriceRises).toHaveBeenCalled());
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
