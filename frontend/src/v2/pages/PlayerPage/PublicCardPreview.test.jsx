// What a signed-out visitor sees on a card page.
//
// Phase 2 handed anonymous callers the complete live price, fair value
// and trend on any card - which is the substance of what a Free account
// is supposed to unlock. The server now sends a narrower payload
// (app/auth/access.py's public_card_preview); these tests pin that the
// page renders THAT payload honestly rather than a version of the real
// page with blanks in it.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const futgg = vi.hoisted(() => ({ value: { data: undefined, isLoading: false, isError: false } }));
vi.mock("../../hooks/useFutggMarket", () => ({
  useFutggPlayer: () => futgg.value,
  useFutggPlayerPrices: () => ({ data: undefined }),
  useFutggPlayerSales: () => ({ data: undefined }),
  useFutggMarketFreshness: () => ({ data: undefined }),
}));
vi.mock("../../../api/watchlist", () => ({ addWatch: vi.fn() }));
vi.mock("./sections/FutggMarketSection", () => ({ default: () => <div data-testid="market-section" /> }));

const tracked = vi.hoisted(() => ({ calls: [] }));
vi.mock("../../../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: (...a) => tracked.calls.push(a),
  trackOnce: (...a) => tracked.calls.push(a),
}));

import PlayerPage from "./PlayerPage";

// Exactly what public_card_preview() produces: identity, an approximate
// price, and nothing else. Note what is NOT here.
const PUBLIC = {
  source_card_id: 111, card_id: 111, name: "Kylian Mbappe", display_name: "Kylian Mbappe",
  rating: 91, rarity: "team_of_the_week", primary_position: "ST", club: "Real Madrid",
  player_image_url: "https://example.com/a.png",
  current_bin_approx: 425000, price_is_approximate: true, requires_account: true,
  price_age_seconds: 300,
};

const FULL = {
  source_card_id: 111, card_id: 111, name: "Kylian Mbappe", display_name: "Kylian Mbappe",
  rating: 91, rarity: "team_of_the_week", primary_position: "ST",
  current_bin: 420431, fair_value: 505000, signal: "buy", confidence_score: 0.78,
  risk_level: "low", recommended_buy_max: 410000, recommended_sell_target: 520000,
  expected_profit_after_tax: 84000, expected_roi: 0.2, signal_reasons: ["Below median"],
};

function renderPage() {
  // The signed-in path renders the price chart, which is a real
  // react-query consumer. The signed-out path never reaches it - that it
  // does not is part of what is being tested.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/players/111"]}>
        <Routes>
          <Route path="/players/:cardId" element={<PlayerPage />} />
          <Route path="/signup" element={<p>SIGNUP</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  tracked.calls = [];
  futgg.value = { data: undefined, isLoading: false, isError: false };
});

describe("the signed-out card page", () => {
  beforeEach(() => { futgg.value = { data: PUBLIC, isLoading: false, isError: false }; });

  it("shows the card, so a shared link is still worth opening", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Kylian Mbappe" })).toBeInTheDocument();
    expect(screen.getByText("91 OVR")).toBeInTheDocument();
    expect(screen.getByText("Real Madrid")).toBeInTheDocument();
  });

  it("shows an approximate price and says it is approximate", () => {
    renderPage();
    expect(screen.getByText("425,000 coins")).toBeInTheDocument();
    expect(screen.getByText(/Approximate\./)).toBeInTheDocument();
  });

  it("puts no exact price or verdict anywhere on the page", () => {
    renderPage();
    const body = document.body.textContent;
    for (const leak of ["420,431", "505,000", "410,000", "520,000", "84,000"]) {
      expect(body, `leaked ${leak}`).not.toContain(leak);
    }
    expect(body).not.toMatch(/\bBUY\b|\bAVOID\b/);
  });

  it("names what the free account adds rather than saying 'see more'", () => {
    renderPage();
    expect(screen.getByText(/exact live price, and what the card is actually worth/i)).toBeInTheDocument();
    expect(screen.getByText(/Recent completed sales and the full price history/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/see more|unlock more|more tools/i);
  });

  it("returns the visitor to this exact card after signup", () => {
    renderPage();
    const cta = screen.getByRole("link", { name: /Create free account/ });
    expect(cta.getAttribute("href")).toBe("/signup?from=%2Fplayers%2F111");
  });

  it("says the account is free and needs no card", () => {
    renderPage();
    expect(screen.getByText(/Free permanently\. No card required\./i)).toBeInTheDocument();
  });

  it("records that the account prompt was seen, with no card identity", () => {
    renderPage();
    const [name, props] = tracked.calls[0];
    expect(name).toBe("upgrade_prompt_viewed");
    expect(props).toEqual({ feature: "fair_value", surface: "player_page" });
    expect(JSON.stringify(props)).not.toMatch(/Mbappe|111/);
  });

  it("treats a 401 from the detail endpoint the same way", () => {
    futgg.value = {
      data: undefined, isLoading: false, isError: true,
      error: { response: { status: 401 } },
    };
    renderPage();
    expect(screen.getByRole("link", { name: /Create free account/ })).toBeInTheDocument();
    expect(screen.queryByText(/Could not load this card/i)).not.toBeInTheDocument();
  });
});

describe("the signed-in card page", () => {
  it("renders the real analysis, not the public preview", () => {
    futgg.value = { data: FULL, isLoading: false, isError: false };
    renderPage();
    expect(screen.queryByRole("link", { name: /Create free account/ })).toBeNull();
    expect(screen.queryByText(/Approximate\./)).toBeNull();
  });
});
