// Home was the last surface deciding for itself who gets what. It took
// the current top-ranked BUY and returned it complete to anyone without
// the paid feature - signed-out visitors included, since /api/v2/dashboard
// needs no account. That was a third free pick nobody counted, it changed
// every time the ranking moved, and it disagreed with the two picks
// /api/v2/opportunities had already committed to for the day.
//
// These tests cover the client half: Home renders `locked` as the server
// sent it, and a locked row has nothing in it to leak.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import HomeDashboard from "./HomeDashboard";
import { useAuth } from "../../../context/AuthContext";
import { useDashboard } from "../../hooks/useDashboard";
import { useFutggMarketFreshness, useFutggOpportunities, useStrategyFinder } from "../../hooks/useFutggMarket";
import { useGeneratedCardImages } from "../../hooks/useRecommendationFeeds";
import { useIsMobile } from "../../hooks/useIsMobile";

vi.mock("../../../context/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../hooks/useDashboard", () => ({ useDashboard: vi.fn() }));
vi.mock("../../hooks/useFutggMarket", () => ({
  useFutggOpportunities: vi.fn(),
  useStrategyFinder: vi.fn(),
  useFutggMarketFreshness: vi.fn(),
}));
vi.mock("../../hooks/useRecommendationFeeds", () => ({ useGeneratedCardImages: vi.fn() }));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: vi.fn() }));
vi.mock("../../components/TrackRecordSummary", () => ({ default: () => <section /> }));

const BASE_DASHBOARD = {
  marketRegime: { label: "Steady", confidence: 40, summary: "", metrics: {} },
  todaysOpportunities: [], highConfidenceInvestments: [], cardsToAvoid: [], biggestMovers: [],
  watchlistAlerts: [], watchedCount: 0, nextPromo: null, latestMarketEvents: [], latestSbcImpact: [],
  freePicks: null, locked: { opportunityFeed: false, previewCount: 0 },
};

// The complete row, as a Free member's own daily pick arrives.
const UNLOCKED = {
  card_id: 111, source_card_id: 111, name: "Kylian Mbappe", rating: 91, primary_position: "ST",
  rarity: "team_of_the_week", current_bin: 420431, recommended_buy_max: 410000,
  recommended_sell_target: 520000, expected_profit_after_tax: 84000, expected_profit_basis: 415000,
  expected_roi: 0.2, confidence_score: 0.78, risk_level: "low", liquidity_score: 0.7,
  signal_reasons: ["Trading below recent median"],
  current_bin_captured_at: new Date().toISOString(), price_age_seconds: 60,
};

// The preview shape. Note what is NOT here: no buy target, no sell
// target, no profit, no confidence - the server never sent them.
const LOCKED = {
  source_card_id: 222, card_id: 222, name: "Erling Haaland", rating: 93,
  rarity: "gold_rare", primary_position: "ST", signal: "strong_buy", risk_level: "medium",
  current_bin_approx: 900000, headline_reason: "Trading below its recent median", locked: true,
};

const FREE_PICKS = {
  allowance: 2, unlocked: 1, contentDay: "2026-08-30",
  nextRefreshAt: "2099-01-01T18:00:00Z", secondsUntilRefresh: 18600, fewerThanAllowance: true,
};

function renderHome() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HomeDashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useIsMobile.mockReturnValue(false);
  useAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });
  useDashboard.mockReturnValue({ data: BASE_DASHBOARD, isLoading: false });
  useStrategyFinder.mockReturnValue({ data: { items: [], watch_items: [], description: "" }, isLoading: false });
  useFutggMarketFreshness.mockReturnValue({ data: {} });
  useGeneratedCardImages.mockReturnValue({ data: { images: {} } });
});

describe("Home, free member", () => {
  beforeEach(() => {
    useDashboard.mockReturnValue({ data: { ...BASE_DASHBOARD, freePicks: FREE_PICKS }, isLoading: false });
    useFutggOpportunities.mockReturnValue({
      data: { items: [UNLOCKED, LOCKED, { ...LOCKED, source_card_id: 333, card_id: 333, name: "Jude Bellingham" }] },
      isLoading: false,
    });
  });

  it("puts no executable number on any locked card", () => {
    const { container } = renderHome();
    const locked = container.querySelectorAll(".queue-card.locked-opportunity");
    expect(locked.length).toBe(2);
    for (const card of locked) {
      for (const leak of ["410,000", "520,000", "84,000", "420,431"]) {
        expect(card.textContent, `locked Home card leaked ${leak}`).not.toContain(leak);
      }
    }
  });

  it("still identifies the locked card and says why it is there", () => {
    const { container } = renderHome();
    const card = container.querySelector(".queue-card.locked-opportunity");
    expect(card.textContent).toContain("Erling Haaland");
    expect(card.textContent).toContain("Trading below its recent median");
    expect(card.textContent).toContain("Exact prices on Elite");
  });

  it("leads with a card the viewer is entitled to, never a blanked-out hero", () => {
    renderHome();
    // Top Card is the one slot Home puts real numbers on.
    expect(screen.getByText("TOP CARD")).toBeInTheDocument();
    expect(screen.getAllByText(/Kylian Mbappe/).length).toBeGreaterThan(0);
  });

  it("says how many picks are unlocked and when the next ones land", () => {
    renderHome();
    expect(screen.getByText(/Your 1 free pick is unlocked below/)).toBeInTheDocument();
    expect(screen.getByText(/at 6pm UK time/)).toBeInTheDocument();
  });
});

describe("Home, no pick resolved yet", () => {
  it("says the picks haven't landed rather than showing a hero with every figure blank", () => {
    useDashboard.mockReturnValue({
      data: { ...BASE_DASHBOARD, freePicks: { ...FREE_PICKS, unlocked: 0 } },
      isLoading: false,
    });
    useFutggOpportunities.mockReturnValue({ data: { items: [LOCKED] }, isLoading: false });
    renderHome();
    // The section heading stays - what changes is that nothing fills it.
    expect(screen.getByText(/Today's picks haven't landed yet/)).toBeInTheDocument();
    expect(screen.queryByText("Buy below")).not.toBeInTheDocument();
    expect(screen.getByText(/No free pick has cleared our bar yet today/)).toBeInTheDocument();
  });
});

describe("Home, elite member", () => {
  it("shows every card complete, with no lock and no allowance note", () => {
    useFutggOpportunities.mockReturnValue({
      data: { items: [UNLOCKED, { ...UNLOCKED, card_id: 333, source_card_id: 333, name: "Jude Bellingham" }] },
      isLoading: false,
    });
    const { container } = renderHome();
    expect(container.querySelectorAll(".locked-opportunity").length).toBe(0);
    expect(container.querySelector(".home-free-picks")).toBeNull();
  });
});
