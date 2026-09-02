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
  it("labels the count as completed trades, not a card count", () => {
    // closed_trades counts trade-calls, not physical cards - a qty-4
    // trade is one closed trade, not four.
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("87 completed trades")).toBeInTheDocument();
  });

  it("does not hide losses - wins and losses render in the same weight", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText(/78 Wins/)).toBeInTheDocument();
    expect(screen.getByText(/9 Losses/)).toBeInTheDocument();
    expect(screen.getByText("89.7% Win Rate")).toBeInTheDocument();
  });

  it("shows the total profit as the dominant figure", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("+4,250,000")).toBeInTheDocument();
  });

  it("compacts the best-sale profit for readability, alongside its card thumbnail", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText(/\+320K/)).toBeInTheDocument();
    expect(screen.getByText(/Mbappe/)).toBeInTheDocument();
  });

  it("omits the best-trade line entirely when there is no best sale", () => {
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, best_sale: null }} />);
    expect(screen.queryByText(/Best trade/)).not.toBeInTheDocument();
    expect(screen.getByText("87 completed trades")).toBeInTheDocument();
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

  it("scales down an extreme all-time total instead of letting it clip", () => {
    // Caught in visual QA: an all-time total like "+24,650,000" clipped
    // off the right edge of the 1080-wide portrait card at a flat hero
    // font size.
    const { container } = render(
      <TradeSummaryCardArt data={{ ...WEEK_SUMMARY, period_label: "All-Time", total_profit: 24650000 }} format="portrait" />
    );
    const hero = screen.getByText("+24,650,000");
    expect(parseInt(hero.style.fontSize, 10)).toBeLessThan(224);
    expect(container.querySelector("[data-trade-summary-export]")).toBeInTheDocument();
  });
});
