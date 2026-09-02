import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("renders the total profit hero compact, not as a full integer", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText("+4.25M")).toBeInTheDocument();
    expect(screen.queryByText("+4,250,000")).not.toBeInTheDocument();
  });

  it("compacts the best-sale profit for readability, alongside its card thumbnail", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    expect(screen.getByText(/\+320K/)).toBeInTheDocument();
    expect(screen.getByText(/Mbappe/)).toBeInTheDocument();
  });

  it("renders the best-trade thumbnail large enough to stay identifiable on a phone", () => {
    const { container } = render(<TradeSummaryCardArt data={WEEK_SUMMARY} format="portrait" />);
    const thumb = container.querySelector("[data-trade-summary-thumb]");
    expect(parseInt(thumb.style.width, 10)).toBeGreaterThanOrEqual(60);
  });

  it("shows the local placeholder immediately when the best sale has no image_url", () => {
    render(<TradeSummaryCardArt data={WEEK_SUMMARY} />);
    const thumb = document.querySelector("[data-trade-summary-thumb]");
    expect(thumb.src).toContain("/img/card-placeholder.png");
  });

  it("falls back to the placeholder when the best sale's image_url fails to load", () => {
    // Same failure this guards against on the trade-result card: a
    // remote photo that 404s or CORS-fails must not sit there as a
    // blank bordered box.
    const best = { player: "Mbappe", profit: 320000, image_url: "https://cdn.example.com/gone.png" };
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, best_sale: best }} />);
    const thumb = document.querySelector("[data-trade-summary-thumb]");
    expect(thumb.src).toBe("https://cdn.example.com/gone.png");

    fireEvent.error(thumb);

    expect(thumb.src).toContain("/img/card-placeholder.png");
  });

  it("omits the best-trade line entirely when there is no best sale", () => {
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, best_sale: null }} />);
    expect(screen.queryByText(/Best trade/)).not.toBeInTheDocument();
    expect(screen.getByText("87 completed trades")).toBeInTheDocument();
  });

  it("shrinks an unusually long best-trade line instead of letting it overflow", () => {
    const longBest = { player: "Wojciech Szczesny-Fernandez del Castillo", profit: 500000, image_url: null };
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, best_sale: longBest }} format="portrait" />);
    const el = screen.getByText(/Best trade:/);
    expect(parseInt(el.style.fontSize, 10)).toBeLessThan(34);
    expect(el.style.textOverflow).toBe("ellipsis");
  });

  it("renders a loss period without a '+' on total profit", () => {
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, total_profit: -15000, wins: 2, losses: 5 }} />);
    expect(screen.getByText("-15K")).toBeInTheDocument();
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

  it("keeps a realistic all-time total short enough to never need the scale-down backstop", () => {
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, period_label: "All-Time", total_profit: 24650000 }} format="portrait" />);
    const hero = screen.getByText("+24.65M");
    expect(parseInt(hero.style.fontSize, 10)).toBe(224);
  });

  it("still has a scaledFontSize backstop for a figure too large for even compact formatting", () => {
    // Synthetic, unrealistic magnitude - the point is only that the
    // backstop exists and fires, not that this figure is plausible.
    render(<TradeSummaryCardArt data={{ ...WEEK_SUMMARY, period_label: "All-Time", total_profit: 123456789000 }} format="portrait" />);
    const hero = screen.getByText("+123456.79M");
    expect(parseInt(hero.style.fontSize, 10)).toBeLessThan(224);
  });

  it("keeps the total profit exact internally while displaying it compact", () => {
    // Compact formatting is display-only: the props handed to this
    // component are never mutated, only what paints is rounded.
    const exact = { ...WEEK_SUMMARY, period_label: "All-Time", total_profit: 4160000 };
    render(<TradeSummaryCardArt data={exact} />);
    expect(exact.total_profit).toBe(4160000);
    expect(screen.getByText("+4.16M")).toBeInTheDocument();
    expect(screen.queryByText("+4,160,000")).not.toBeInTheDocument();
  });
});
