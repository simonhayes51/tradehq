import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import TradeSummaryCardArt from "./TradeSummaryCardArt";

const WEEK_SUMMARY = {
  period: "week",
  period_label: "This Week",
  wins: 78,
  losses: 9,
  closed_trades: 87,
  win_rate: 89.7,
  total_profit: 4250000,
  best_sale: { player: "Mbappe", profit: 320000, image_url: null },
};

describe("TradeSummaryCardArt", () => {
  it('labels the trade count "Trades Tracked", not "Cards Traded"', () => {
    // closed_trades counts trade-calls, not physical cards - a qty-4
    // trade is one closed trade, not four. The old "Cards Traded" label
    // implied the latter.
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("Trades Tracked")).toBeInTheDocument();
    expect(screen.queryByText("Cards Traded")).not.toBeInTheDocument();
    expect(screen.getByText("87")).toBeInTheDocument();
  });

  it("does not hide losses - the record line shows both wins and losses plainly", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText(/78W – 9L/)).toBeInTheDocument();
    expect(screen.getByText(/89\.7% win rate/)).toBeInTheDocument();
  });

  it("shows the total profit as the dominant figure", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("+4,250,000")).toBeInTheDocument();
  });

  it("compacts the best-sale profit for readability", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("+320K")).toBeInTheDocument();
  });

  it("renders a loss period without a '+' on total profit", () => {
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, total_profit: -15000, wins: 2, losses: 5 }} />);
    expect(screen.getByText("-15,000")).toBeInTheDocument();
  });

  it("defaults to the 900x470 wide export size", () => {
    const { container } = render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    const root = container.querySelector("[data-trade-summary-export]");
    expect(root.style.width).toBe("900px");
    expect(root.style.height).toBe("470px");
  });

  it("renders the 1080x1350 portrait size when format='portrait'", () => {
    const { container } = render(<TradeSummaryCardArt data={WEEK_SUMMARY} format="portrait" />);
    const root = container.querySelector("[data-trade-summary-export]");
    expect(root.style.width).toBe("1080px");
    expect(root.style.height).toBe("1350px");
  });
});
