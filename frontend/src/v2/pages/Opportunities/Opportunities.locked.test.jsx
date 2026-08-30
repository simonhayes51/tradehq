// The free/locked feed is where the access model becomes visible to a
// customer, so it is tested at the component boundary against the exact
// payload shape the server produces (app/auth/access.py's
// serialise_opportunities).
//
// The point these tests protect is that the client never decides what is
// locked. It renders `locked` as the server sent it, and a locked row
// simply has no premium fields in it to leak.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const feed = vi.hoisted(() => ({ value: { data: undefined, isLoading: false, isError: false } }));
vi.mock("../../hooks/useFutggMarket", () => ({
  useFutggOpportunities: () => feed.value,
  useFutggMarketFreshness: () => ({ data: undefined }),
}));
vi.mock("../../../context/AuthContext", () => ({ useAuth: () => ({ isAuthenticated: true }) }));
vi.mock("../../hooks/useBillingPlans", async (importOriginal) => ({
  ...(await importOriginal()),
  useBillingPlans: () => ({ data: { plans: { monthly: { configured: true, price_display: "£3/month" } } } }),
}));

const tracked = vi.hoisted(() => ({ calls: [] }));
vi.mock("../../../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: (...args) => tracked.calls.push(args),
  trackOnce: (...args) => tracked.calls.push(args),
}));

import Opportunities from "./Opportunities";

// What the server sends a Free member for their own daily pick: complete.
const UNLOCKED = {
  source_card_id: 111, card_id: 111, name: "Kylian Mbappé", display_name: "Kylian Mbappé",
  rating: 91, rarity: "team_of_the_week", primary_position: "ST",
  current_bin: 420000, recommended_buy_max: 410000, buy_below: 410000,
  recommended_sell_target: 520000, expected_profit_after_tax: 84000, expected_roi: 0.2,
  confidence_score: 0.78, risk_level: "low", liquidity_score: 0.7, signal: "buy",
  signal_reasons: ["Trading below recent median"], recent_sale_count: 21,
};

// What the server sends for everything else: the allow-listed preview
// only. Note what is NOT here - there is no buy target or profit field
// on this object at all.
const LOCKED = {
  source_card_id: 222, name: "Erling Haaland", display_name: "Erling Haaland",
  rating: 93, rarity: "gold_rare", primary_position: "ST", signal: "strong_buy",
  risk_level: "medium", current_bin_approx: 900000,
  headline_reason: "Trading below its recent median", locked: true,
};

const FREE_META = {
  allowance: 2, unlocked: 1, contentDay: "2026-08-30",
  nextRefreshAt: "2099-01-01T18:00:00Z", secondsUntilRefresh: 18600,
  fewerThanAllowance: true,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Opportunities />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  tracked.calls = [];
  feed.value = { data: undefined, isLoading: false, isError: false };
});

describe("Opportunities, free member", () => {
  beforeEach(() => {
    feed.value = {
      isLoading: false, isError: false,
      data: {
        items: [UNLOCKED, LOCKED, { ...LOCKED, source_card_id: 333, name: "Jude Bellingham", display_name: "Jude Bellingham" }],
        locked_count: 2, viewer_tier: "free", free_picks: FREE_META,
      },
    };
  });

  it("shows the daily pick complete, with its real numbers", () => {
    renderPage();
    expect(screen.getByText("Kylian Mbappé")).toBeInTheDocument();
    expect(screen.getByText(/410,000/)).toBeInTheDocument();
  });

  it("puts no executable number on any locked card", () => {
    const { container } = renderPage();
    const lockedCards = container.querySelectorAll(".locked-opportunity");
    expect(lockedCards.length).toBe(2);
    for (const card of lockedCards) {
      const text = card.textContent;
      for (const leak of ["410,000", "520,000", "84,000", "420,000"]) {
        expect(text, `locked card leaked ${leak}`).not.toContain(leak);
      }
    }
  });

  it("still identifies the locked card, so the preview proves it is a real one", () => {
    const { container } = renderPage();
    const card = container.querySelector(".locked-opportunity");
    expect(within(card).getByText("Erling Haaland")).toBeInTheDocument();
    expect(within(card).getByText(/STRONG BUY/)).toBeInTheDocument();
    expect(within(card).getByText(/Trading below its recent median/)).toBeInTheDocument();
    expect(within(card).getByText(/Around 900,000 coins/)).toBeInTheDocument();
  });

  it("states the real allowance and when it refreshes, and is honest about a thin day", () => {
    renderPage();
    expect(screen.getByText("Your 1 free pick for today")).toBeInTheDocument();
    expect(screen.getByText(/Only 1 of 2 today/)).toBeInTheDocument();
    expect(screen.getByText(/at 6pm UK time/)).toBeInTheDocument();
  });

  it("explains the upgrade once, below the feed, not as a banner above it", () => {
    const { container } = renderPage();
    const prompt = container.querySelector(".upgrade-prompt");
    expect(prompt).toBeTruthy();
    expect(container.querySelectorAll(".upgrade-prompt").length).toBe(1);
    const grid = container.querySelector(".opp-grid");
    // DOCUMENT_POSITION_FOLLOWING === 4: the prompt comes after the feed.
    expect(grid.compareDocumentPosition(prompt) & 4).toBeTruthy();
  });

  it("records the free view and the limit without any card or player identity", () => {
    renderPage();
    const names = tracked.calls.map(([name]) => name);
    expect(names).toContain("free_opportunity_viewed");
    expect(names).toContain("free_limit_reached");
    for (const [, props] of tracked.calls) {
      const serialised = JSON.stringify(props ?? {});
      expect(serialised).not.toMatch(/Mbappé|Haaland|Bellingham|111|222|333/);
    }
  });
});

describe("Opportunities, elite member", () => {
  it("shows every card complete and no upgrade prompt at all", () => {
    feed.value = {
      isLoading: false, isError: false,
      data: {
        items: [UNLOCKED, { ...UNLOCKED, source_card_id: 333, card_id: 333, name: "Jude Bellingham", display_name: "Jude Bellingham" }],
        locked_count: 0, viewer_tier: "elite",
      },
    };
    const { container } = renderPage();
    expect(container.querySelectorAll(".locked-opportunity").length).toBe(0);
    expect(container.querySelector(".upgrade-prompt")).toBeNull();
    expect(container.querySelector(".free-pick-banner")).toBeNull();
  });
});

describe("Opportunities, thin day", () => {
  it("says so plainly rather than inventing a second pick", () => {
    feed.value = {
      isLoading: false, isError: false,
      data: {
        items: [LOCKED], locked_count: 1, viewer_tier: "free",
        free_picks: { ...FREE_META, unlocked: 0, fewerThanAllowance: true },
      },
    };
    renderPage();
    expect(screen.getByText(/No free pick has cleared our bar yet today/)).toBeInTheDocument();
  });
});
