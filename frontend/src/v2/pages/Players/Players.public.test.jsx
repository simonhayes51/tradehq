// Search stays public. That is deliberate: it is discovery, it is what
// makes a shared link worth opening, and making it account-only would
// remove the top of the funnel to protect a number.
//
// What changes for a signed-out visitor is the price column and what the
// page says about it.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const search = vi.hoisted(() => ({ value: { data: undefined, isLoading: false, isError: false } }));
vi.mock("../../hooks/useFutggMarket", () => ({ useFutggPlayers: () => search.value }));
const tracked = vi.hoisted(() => ({ calls: [] }));
vi.mock("../../../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: (...a) => tracked.calls.push(a),
  trackOnce: (...a) => tracked.calls.push(a),
}));

import Players from "./Players";

const PUBLIC_ROW = {
  source_card_id: 111, card_id: 111, name: "Kylian Mbappe", rating: 91,
  rarity: "team_of_the_week", position: "ST",
  current_bin_approx: 425000, price_is_approximate: true, requires_account: true,
};
const FULL_ROW = {
  source_card_id: 111, card_id: 111, name: "Kylian Mbappe", rating: 91,
  rarity: "team_of_the_week", position: "ST", current_bin: 420431,
};

function renderSearch() {
  return render(
    <MemoryRouter initialEntries={["/players?q=mbappe"]}>
      <Players />
    </MemoryRouter>,
  );
}

beforeEach(() => { tracked.calls = []; });

describe("signed-out search", () => {
  beforeEach(() => {
    search.value = {
      isLoading: false, isError: false,
      data: { items: [PUBLIC_ROW], requires_account: true, viewer_tier: "free" },
    };
  });

  it("still returns results", () => {
    renderSearch();
    expect(screen.getByText("Kylian Mbappe")).toBeInTheDocument();
  });

  it("labels the price as approximate rather than live", () => {
    renderSearch();
    expect(screen.getByText("AROUND")).toBeInTheDocument();
    expect(screen.queryByText("LIVE PRICE")).toBeNull();
  });

  it("does not show an exact price", () => {
    renderSearch();
    expect(document.body.textContent).not.toContain("420,431");
  });

  it("explains what an account adds, under the results", () => {
    renderSearch();
    expect(screen.getByText(/exact live price/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create free account/ })).toBeInTheDocument();
    expect(screen.getByText(/No card required/i)).toBeInTheDocument();
  });
});

describe("signed-in search", () => {
  beforeEach(() => {
    search.value = {
      isLoading: false, isError: false,
      data: { items: [FULL_ROW], requires_account: false, viewer_tier: "free" },
    };
  });

  it("shows the live price and no account prompt", () => {
    renderSearch();
    expect(screen.getByText("LIVE PRICE")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Create free account/ })).toBeNull();
  });
});
