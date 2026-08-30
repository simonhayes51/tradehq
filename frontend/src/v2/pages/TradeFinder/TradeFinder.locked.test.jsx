// Trade Finder is Elite-only, and the lock is decided by ONE thing: the
// 402 the server returns. These tests pin that, because the tempting
// alternative - reading the entitlement context and hiding the page
// client-side - lets the page and the API disagree, and the API is the
// one that is right.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const finder = vi.hoisted(() => ({ value: { data: undefined, isLoading: false, isError: false } }));
vi.mock("../../hooks/useFutggMarket", () => ({
  useFutggTradeFinder: () => finder.value,
  useFutggMarketFreshness: () => ({ data: undefined }),
}));
const auth = vi.hoisted(() => ({ value: { isAuthenticated: true } }));
vi.mock("../../../context/AuthContext", () => ({ useAuth: () => auth.value }));
vi.mock("../../hooks/useBillingPlans", async (importOriginal) => ({
  ...(await importOriginal()),
  useBillingPlans: () => ({ data: { plans: { monthly: { configured: true, price_display: "£3/month" } } } }),
}));
vi.mock("../../../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: vi.fn(),
  trackOnce: vi.fn(),
}));

import TradeFinder from "./TradeFinder";
import { GATED } from "../../lib/gatedFeatures";

function err(status) {
  return { isLoading: false, isError: true, data: undefined, error: { response: { status } } };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TradeFinder />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  auth.value = { isAuthenticated: true };
  finder.value = { data: undefined, isLoading: false, isError: false };
});

describe("Trade Finder access states", () => {
  it("explains the lock on a 402 rather than showing an error", () => {
    finder.value = err(402);
    renderPage();
    expect(screen.getByRole("heading", { name: GATED.trade_finder.title })).toBeInTheDocument();
    expect(screen.getByText(GATED.trade_finder.what)).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });

  it("hides the filter controls when locked, so nothing invites a search that cannot run", () => {
    finder.value = err(402);
    const { container } = renderPage();
    expect(container.querySelector(".finder-controls")).toBeNull();
    expect(container.querySelector(".finder-result-head")).toBeNull();
  });

  it("offers a signed-out visitor the free account, not checkout", () => {
    auth.value = { isAuthenticated: false };
    finder.value = err(401);
    renderPage();
    expect(screen.getByRole("heading", { name: GATED.trade_finder.title })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create free account/ })).toBeInTheDocument();
  });

  it("still reports a genuine outage as an outage, not as a paywall", () => {
    finder.value = err(500);
    renderPage();
    expect(screen.queryByRole("heading", { name: GATED.trade_finder.title })).toBeNull();
    const outage = screen.getByText(/could not load results/i);
    expect(outage).toBeInTheDocument();
    // Said out loud, because "something went wrong" on a paid feature
    // reads to a free member as another paywall.
    expect(outage.textContent).toMatch(/not a limit on your account/i);
  });

  it("shows the full tool, and no lock, to a member the server serves", () => {
    finder.value = {
      isLoading: false, isError: false,
      data: {
        items: [{
          source_card_id: 111, card_id: 111, name: "Kylian Mbappé", display_name: "Kylian Mbappé",
          rating: 91, rarity: "team_of_the_week", primary_position: "ST",
          current_bin: 420000, recommended_buy_max: 410000, recommended_sell_target: 520000,
          expected_profit_after_tax: 84000, expected_roi: 0.2, confidence_score: 0.78,
          risk_level: "low", signal: "buy", signal_reasons: ["Trading below recent median"],
        }],
        total: 1,
      },
    };
    const { container } = renderPage();
    expect(screen.queryByRole("heading", { name: GATED.trade_finder.title })).toBeNull();
    expect(container.querySelector(".finder-controls")).toBeTruthy();
    expect(screen.getByText("Kylian Mbappé")).toBeInTheDocument();
  });
});
