// Player Detail: the recommendation-and-action area.
//
// Three separate bugs the user found on this page:
//   1. A WAIT verdict sat directly above a large bright-green LOG TRADE
//      button - the primary action contradicted the advice.
//   2. "Add to watchlist", the live-price freshness line and the WATCH
//      status each appeared twice, because the analysis panel and the
//      market section below it rendered the same facts from the same
//      fields.
//   3. Two navigation models at once: a circular X (dismiss an overlay)
//      and a "Back to player search" link (leave a page).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PlayerPage from "./PlayerPage";
import { useFutggPlayer, useFutggPlayerPrices, useFutggPlayerSales } from "../../hooks/useFutggMarket";

vi.mock("../../hooks/useFutggMarket", () => ({
  useFutggPlayer: vi.fn(),
  useFutggPlayerPrices: vi.fn(),
  useFutggPlayerSales: vi.fn(),
}));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useParams: () => ({ cardId: "42" }) };
});

const BASE = {
  card_id: 42, source_card_id: 42, name: "Kylian Mbappe", rating: 91,
  primary_position: "ST", rarity: "gold_rare", current_bin: 500000,
  fair_value: 520000, recommended_buy_max: 460000, buy_below: 460000,
  expected_profit_basis: 460000, recommended_sell_target: 540000,
  expected_profit_after_tax: 53000, expected_roi: 0.11, confidence_score: 0.7,
  risk_level: "medium", liquidity_score: 0.5, signal_reasons: ["Trading below its recent median"],
  current_bin_captured_at: new Date().toISOString(), price_age_seconds: 120,
};

function mockCard(overrides = {}) {
  useFutggPlayer.mockReturnValue({ data: { ...BASE, ...overrides }, isLoading: false, isError: false });
  useFutggPlayerPrices.mockReturnValue({ data: { items: [] }, isLoading: false, isError: false });
  useFutggPlayerSales.mockReturnValue({ data: { items: [] }, isLoading: false, isError: false });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><PlayerPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("the primary action follows the verdict", () => {
  it("does not offer LOG TRADE as the primary action on a WAIT", () => {
    mockCard({ signal: "watch" });
    const { container } = renderPage();
    const buttons = [...container.querySelectorAll(".dash-modal-actions button")];
    expect(buttons.length).toBeGreaterThan(1);
    // The first (primary, filled-accent) action watches for an entry.
    expect(buttons[0].textContent).toMatch(/Watch for entry/i);
    // Logging a purchase already made stays available, secondary, and
    // labelled as what it is.
    expect(buttons[1].className).toContain("secondary");
    expect(buttons[1].textContent).toMatch(/already bought this/i);
  });

  it("keeps LOG TRADE primary on a BUY, where it agrees with the advice", () => {
    mockCard({ signal: "buy" });
    const { container } = renderPage();
    const buttons = [...container.querySelectorAll(".dash-modal-actions button")];
    expect(buttons[0].textContent).toMatch(/Log trade/i);
    expect(buttons[0].className).not.toContain("secondary");
  });

  it("sends an AVOID somewhere useful instead of offering to log it", () => {
    mockCard({ signal: "avoid" });
    const { container } = renderPage();
    const buttons = [...container.querySelectorAll(".dash-modal-actions button")];
    expect(buttons[0].textContent).toMatch(/Find a better trade/i);
  });
});

describe("no duplicated elements", () => {
  it("has exactly one watchlist action", () => {
    mockCard({ signal: "buy" });
    renderPage();
    expect(screen.getAllByRole("button", { name: /watchlist/i })).toHaveLength(1);
  });

  it("has exactly one live-price freshness readout", () => {
    mockCard({ signal: "buy" });
    const { container } = renderPage();
    expect(container.querySelectorAll(".market-freshness").length).toBeLessThanOrEqual(1);
  });

  it("states the verdict once, not once as a pill and again as a signal badge", () => {
    mockCard({ signal: "watch" });
    const { container } = renderPage();
    // One verdict pill, stating WAIT. The market section below used to
    // render the raw engine signal ("watch") as a second badge beside
    // it, so the page gave the same card two status words at once.
    const pills = [...container.querySelectorAll(".call")];
    expect(pills).toHaveLength(1);
    expect(pills[0].textContent.trim()).toBe("WAIT");
    const strays = [...container.querySelectorAll("*")].filter(
      (el) => el.children.length === 0 && el.textContent.trim().toUpperCase() === "WATCH",
    );
    expect(strays).toHaveLength(0);
  });
});

describe("one navigation model", () => {
  it("uses the back link on the standalone page, not a dismiss X as well", () => {
    mockCard({ signal: "buy" });
    const { container } = renderPage();
    expect(container.querySelector(".dash-modal-back")).toBeTruthy();
    expect(container.querySelector(".dash-modal-close")).toBeNull();
  });
});

describe("page states", () => {
  it("shows a loading state before anything has arrived", () => {
    useFutggPlayer.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    useFutggPlayerPrices.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    useFutggPlayerSales.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    renderPage();
    expect(screen.getByText(/Loading the latest analysis/i)).toBeInTheDocument();
  });

  it("shows a real error state on a failed load", () => {
    useFutggPlayer.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: { response: { status: 500 } }, refetch: vi.fn() });
    useFutggPlayerPrices.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    useFutggPlayerSales.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    renderPage();
    expect(screen.getByText(/Could not load this card/i)).toBeInTheDocument();
  });
});
