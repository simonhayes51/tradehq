import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import TradeShareCardArt from "./TradeShareCardArt";

const HAALAND = {
  player: "Haaland",
  version: "TOTS",
  buy: 997000,
  sell: 1450000,
  quantity: 4,
  profit: 1522000,
  roi: 38.164, // computed server-side (trade_economics.py), never recomputed here
  image_url: null,
};

describe("TradeShareCardArt", () => {
  it("renders the ROI it is given, never recomputing it", () => {
    // The bug this guards: an earlier version computed
    // roi = profit / data.buy * 100 itself, ignoring quantity and
    // overstating ROI 4x for this exact trade (152.66% instead of
    // 38.2%). The component now has no ROI math of its own at all.
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText("+38.2%")).toBeInTheDocument();
    expect(screen.queryByText(/152\.6/)).not.toBeInTheDocument();
  });

  it("shows quantity folded into the buy/sell figures, not just a pill", () => {
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText(/4x @ 997K/)).toBeInTheDocument();
    expect(screen.getByText(/4x @ 1\.45M/)).toBeInTheDocument();
  });

  it("shows a transparency line naming the card count and the tax", () => {
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText("4 cards • after 5% EA tax")).toBeInTheDocument();
  });

  it("omits the card count from the transparency line for a single card", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, quantity: 1, roi: 42.5 }} />);
    expect(screen.getByText("after 5% EA tax")).toBeInTheDocument();
  });

  it("renders a loss without a '+' sign", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, profit: -50000, roi: -1.25 }} />);
    expect(screen.getByText("-50,000")).toBeInTheDocument();
    expect(screen.getByText("Trade Closed")).toBeInTheDocument();
  });

  it("renders no ROI stat when roi is null (no cost to divide by)", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, roi: null }} />);
    expect(screen.queryByText(/ROI/)).not.toBeInTheDocument();
  });

  it("defaults to the 900x470 wide export size", () => {
    const { container } = render(<TradeShareCardArt data={HAALAND} />);
    const root = container.querySelector("[data-trade-share-export]");
    expect(root.style.width).toBe("900px");
    expect(root.style.height).toBe("470px");
  });

  it("renders the 1080x1350 portrait size when format='portrait'", () => {
    const { container } = render(<TradeShareCardArt data={HAALAND} format="portrait" />);
    const root = container.querySelector("[data-trade-share-export]");
    expect(root.style.width).toBe("1080px");
    expect(root.style.height).toBe("1350px");
    // Portrait still shows the same figures, just in a taller layout
    // (the ROI badge combines the label and value into one text node
    // here, unlike wide's separate label/value stat card).
    expect(screen.getByText(/\+38\.2%/)).toBeInTheDocument();
  });
});
