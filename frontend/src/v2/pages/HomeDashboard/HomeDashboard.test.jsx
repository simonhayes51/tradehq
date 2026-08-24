import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
// TrackRecordPanel has its own substantial hook tree, already covered
// by its own test file - stubbed here so this file only tests
// HomeDashboard's own mobile section-grouping behaviour.
vi.mock("../../components/TrackRecordPanel", () => ({
  default: () => <section className="track-record-panel" data-testid="track-record-stub">Track record stub</section>,
}));

const EMPTY_DASHBOARD = {
  marketRegime: { label: "Steady", confidence: 40, summary: "", metrics: {} },
  todaysOpportunities: [], highConfidenceInvestments: [], cardsToAvoid: [], biggestMovers: [],
  watchlistAlerts: [], watchedCount: 0, nextPromo: null, latestMarketEvents: [], latestSbcImpact: [],
  locked: { opportunityFeed: false, previewCount: 0 },
};

const ONE_OPPORTUNITY = {
  card_id: 111, source_card_id: 111, name: "Test Player", rating: 91, primary_position: "ST",
  current_bin: 40000, recommended_buy_max: 42000, expected_profit_basis: 40000,
  recommended_sell_target: 55000, expected_profit_after_tax: 8000, expected_roi: 0.2,
  confidence_score: 0.7, risk_level: "low", liquidity_score: 0.6, signal_reasons: [],
  current_bin_captured_at: new Date().toISOString(), price_age_seconds: 60,
};

function renderHome() {
  return render(
    <MemoryRouter>
      <HomeDashboard />
    </MemoryRouter>,
  );
}

describe("HomeDashboard mobile section grouping", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });
    useDashboard.mockReturnValue({ data: EMPTY_DASHBOARD, isLoading: false });
    useFutggOpportunities.mockReturnValue({ data: { items: [ONE_OPPORTUNITY] }, isLoading: false });
    useStrategyFinder.mockReturnValue({ data: { items: [], watch_items: [], description: "" }, isLoading: false });
    useFutggMarketFreshness.mockReturnValue({ data: {} });
    useGeneratedCardImages.mockReturnValue({ data: { images: {} } });
  });

  it("on desktop, renders every section simultaneously with no tab bar (unchanged behaviour)", () => {
    useIsMobile.mockReturnValue(false);
    renderHome();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.getByText("TOP CARD")).toBeInTheDocument();
    expect(screen.getByTestId("track-record-stub")).toBeInTheDocument();
    expect(screen.getByText("Next best moves")).toBeInTheDocument();
    expect(screen.getByText("Where coins are moving")).toBeInTheDocument();
    expect(screen.getByText("Find a different angle")).toBeInTheDocument();
  });

  it("on mobile, defaults to Overview: the decision (Top Card) is present, other section groups are not mounted", () => {
    useIsMobile.mockReturnValue(true);
    renderHome();

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Opportunities", "Track record", "Market"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");

    // The essential decision content is present without switching tabs.
    expect(screen.getByText("TOP CARD")).toBeInTheDocument();
    expect(screen.getAllByText("Test Player").length).toBeGreaterThan(0);

    // Everything else is not mounted at all (not merely hidden) until
    // its tab is selected.
    expect(screen.queryByTestId("track-record-stub")).not.toBeInTheDocument();
    expect(screen.queryByText("Next best moves")).not.toBeInTheDocument();
    expect(screen.queryByText("Where coins are moving")).not.toBeInTheDocument();
    expect(screen.queryByText("Find a different angle")).not.toBeInTheDocument();
  });

  it("switching to the Track record tab mounts it and unmounts Overview", () => {
    useIsMobile.mockReturnValue(true);
    renderHome();
    fireEvent.click(screen.getByRole("tab", { name: "Track record" }));
    expect(screen.getByTestId("track-record-stub")).toBeInTheDocument();
    expect(screen.queryByText("TOP CARD")).not.toBeInTheDocument();
  });

  it("switching to the Opportunities tab mounts Next Best Moves and Strategy Finder together", () => {
    useIsMobile.mockReturnValue(true);
    renderHome();
    fireEvent.click(screen.getByRole("tab", { name: "Opportunities" }));
    expect(screen.getByText("Next best moves")).toBeInTheDocument();
    expect(screen.getByText("Find a different angle")).toBeInTheDocument();
    expect(screen.queryByTestId("track-record-stub")).not.toBeInTheDocument();
  });

  it("switching to the Market tab mounts Market Map and the full Alerts feed", () => {
    useIsMobile.mockReturnValue(true);
    renderHome();
    fireEvent.click(screen.getByRole("tab", { name: "Market" }));
    expect(screen.getByText("Where coins are moving")).toBeInTheDocument();
    expect(screen.getByText("Alerts & content")).toBeInTheDocument();
  });
});
