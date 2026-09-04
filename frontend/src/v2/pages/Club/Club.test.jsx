// Portfolio: the credibility of the numbers, and the states around them.
//
// Covers the four cases the brief calls out for any list surface (zero,
// one, two, many) plus the real bug that started this: a target sitting
// at break-even presented as a "projected profit".
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Club from "./Club";
import {
  getOpenTrades,
  getProfitTimeline,
  getTradeHistory,
  getTradingPerformance,
} from "../../api/trades";

vi.mock("../../api/trades", () => ({
  getOpenTrades: vi.fn(),
  getTradeHistory: vi.fn(),
  getTradingPerformance: vi.fn(),
  getProfitTimeline: vi.fn(),
  closeTrade: vi.fn(),
  shareTradeCard: vi.fn(),
  shareSummaryCard: vi.fn(),
}));

const PERFORMANCE = {
  open_positions: 1, closed_trades: 0, wins: 0, total_profit: 0, profit_today: 0,
  profit_week: 0, total_ea_tax: 0, average_hold_hours: 0, average_roi: 0,
  strategies: [], confidence_accuracy: [],
};

// The row from the real Free account the user screenshotted.
const MBAPPE = {
  trade_id: 1, player: "Kylian Mbappe", version: "gold_rare", buy: 2304000,
  target_sell: 2425500, quantity: 1, platform: "ps", bought_at: "2026-08-01T10:00:00Z",
};

const HEALTHY = {
  trade_id: 2, player: "Jude Bellingham", version: "gold_rare", buy: 100000,
  target_sell: 130000, quantity: 1, platform: "ps", bought_at: "2026-08-02T10:00:00Z",
};

const NO_TARGET = {
  trade_id: 3, player: "Vinicius Junior", version: "gold_rare", buy: 80000,
  target_sell: null, quantity: 1, platform: "ps", bought_at: "2026-08-03T10:00:00Z",
};

function closedTrade(id) {
  return {
    trade_id: id, player: `Closed ${id}`, version: "gold_rare", buy: 10000,
    sell: 13000, profit: 2350, quantity: 1, sold_at: "2026-08-05T10:00:00Z",
  };
}

function mockApi({ open = [], history = [], performance = PERFORMANCE } = {}) {
  getOpenTrades.mockResolvedValue({ trades: open });
  getTradeHistory.mockResolvedValue({ trades: history });
  getTradingPerformance.mockResolvedValue(performance);
  getProfitTimeline.mockResolvedValue({ items: [] });
}

function renderClub() {
  return render(<MemoryRouter><Club /></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("open trades: break-even credibility", () => {
  it("warns that a break-even target is not a return, and names the break-even price", async () => {
    mockApi({ open: [MBAPPE] });
    renderClub();
    const card = (await screen.findAllByTestId("open-trade"))[0];

    // The arithmetic is unchanged - 225 coins really is what this target
    // nets - but it is no longer presented as an attractive result.
    expect(within(card).getByText(/barely above break-even/i)).toBeInTheDocument();
    expect(card).toHaveAttribute("data-target-status", "marginal");
    // The break-even price is now stated outright rather than left to be
    // inferred from a suspiciously small profit.
    expect(within(card).getByText(/Break-even sale/i)).toBeInTheDocument();
    expect(within(card).getByText(/2,430,000/)).toBeInTheDocument();
    // And the profit is labelled as what it is.
    expect(within(card).getByText(/Projected net profit/i)).toBeInTheDocument();
    expect(within(card).getByText(/After 5% EA tax/i)).toBeInTheDocument();
  });

  it("leaves a genuinely profitable target unwarned", async () => {
    mockApi({ open: [HEALTHY] });
    renderClub();
    const card = (await screen.findAllByTestId("open-trade"))[0];
    expect(card).toHaveAttribute("data-target-status", "profit");
    expect(within(card).queryByText(/break-even after EA/i)).not.toBeInTheDocument();
    expect(within(card).queryByText(/sells at a LOSS/i)).not.toBeInTheDocument();
  });

  it("does not invent a target when none was recorded", async () => {
    mockApi({ open: [NO_TARGET] });
    renderClub();
    const card = (await screen.findAllByTestId("open-trade"))[0];
    expect(card).toHaveAttribute("data-target-status", "no-target");
    expect(within(card).getByText(/No target recorded/i)).toBeInTheDocument();
    expect(within(card).getByText(/Needs a target price/i)).toBeInTheDocument();
  });
});

describe("open trades: list sizes", () => {
  it("shows an empty state with zero trades", async () => {
    mockApi({ open: [] });
    renderClub();
    expect(await screen.findByText(/No open trades yet/i)).toBeInTheDocument();
  });

  it("renders one, and two, without a paginator", async () => {
    mockApi({ open: [MBAPPE] });
    const one = renderClub();
    expect(await screen.findAllByTestId("open-trade")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /show more/i })).not.toBeInTheDocument();
    one.unmount();

    mockApi({ open: [MBAPPE, HEALTHY] });
    renderClub();
    await waitFor(async () => expect(await screen.findAllByTestId("open-trade")).toHaveLength(2));
  });

  it("paginates many trades rather than rendering all of them at once", async () => {
    const many = Array.from({ length: 45 }, (_, i) => ({ ...HEALTHY, trade_id: 100 + i, player: `Player ${i}` }));
    mockApi({ open: many });
    renderClub();
    await waitFor(async () => expect(await screen.findAllByTestId("open-trade")).toHaveLength(20));
    expect(screen.getByText(/Showing 20 of 45 open trades/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /show more/i }));
    expect(await screen.findAllByTestId("open-trade")).toHaveLength(40);
  });
});

describe("win rate", () => {
  it("shows '1 of 1' rather than a 100% win rate on a single closed trade", async () => {
    mockApi({
      open: [],
      history: [closedTrade(1)],
      performance: { ...PERFORMANCE, closed_trades: 1, wins: 1 },
    });
    renderClub();
    expect(await screen.findByText("1 of 1")).toBeInTheDocument();
    expect(screen.queryByText("100.0%")).not.toBeInTheDocument();
    expect(screen.getByText(/needs 5 closed trades/i)).toBeInTheDocument();
  });

  it("shows a percentage once the sample is big enough", async () => {
    mockApi({
      open: [],
      history: [],
      performance: { ...PERFORMANCE, closed_trades: 8, wins: 6 },
    });
    renderClub();
    expect(await screen.findByText("75.0%")).toBeInTheDocument();
  });

  it("shows a dash, not 0%, before anything has been closed", async () => {
    mockApi({ open: [], history: [] });
    renderClub();
    expect(await screen.findByText("—")).toBeInTheDocument();
  });
});

describe("completed sales list", () => {
  it("keeps player, status, buy, sold and net profit on every row", async () => {
    mockApi({ open: [], history: [closedTrade(1)], performance: { ...PERFORMANCE, closed_trades: 1, wins: 1 } });
    renderClub();
    await userEvent.click(await screen.findByRole("tab", { name: /sold players/i }));
    expect(await screen.findByText("Closed 1")).toBeInTheDocument();
    expect(screen.getByText("SOLD")).toBeInTheDocument();
    // data-label drives the mobile card layout - each labelled cell is
    // the same DOM as the desktop column, so nothing is hidden on one.
    for (const label of ["Bought", "Sold", "Net profit", "Date"]) {
      expect(document.querySelector(`[data-label="${label}"]`)).toBeTruthy();
    }
  });

  it("paginates a long history", async () => {
    const history = Array.from({ length: 60 }, (_, i) => closedTrade(i + 1));
    mockApi({ open: [], history, performance: { ...PERFORMANCE, closed_trades: 60, wins: 40 } });
    renderClub();
    await userEvent.click(await screen.findByRole("tab", { name: /sold players/i }));
    expect(await screen.findByText(/Showing 20 of 60 completed sales/i)).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been sold", async () => {
    mockApi({ open: [], history: [] });
    renderClub();
    await userEvent.click(await screen.findByRole("tab", { name: /sold players/i }));
    expect(await screen.findByText(/No completed sales/i)).toBeInTheDocument();
  });
});

describe("error state", () => {
  it("surfaces a load failure with a retry", async () => {
    getOpenTrades.mockRejectedValue(new Error("Could not load your portfolio."));
    getTradeHistory.mockResolvedValue({ trades: [] });
    getTradingPerformance.mockResolvedValue(PERFORMANCE);
    getProfitTimeline.mockResolvedValue({ items: [] });
    renderClub();
    expect(await screen.findByText(/Could not load your portfolio/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
