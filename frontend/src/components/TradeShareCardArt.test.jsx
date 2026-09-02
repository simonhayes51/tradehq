import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("renders the hero profit compact, not as a full integer", () => {
    // A full "+1,522,000" is exactly the kind of long string that risks
    // clipping the canvas; the hero is always compact-formatted now.
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText("+1.52M")).toBeInTheDocument();
    expect(screen.queryByText("+1,522,000")).not.toBeInTheDocument();
  });

  it("shows quantity folded into the buy/sell figures, per-card price unambiguous", () => {
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText("4×997K")).toBeInTheDocument();
    expect(screen.getByText("4×1.45M")).toBeInTheDocument();
  });

  it("shows plain compact prices with no quantity prefix for a single card", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, quantity: 1, roi: 42.5 }} />);
    expect(screen.getByText("997K")).toBeInTheDocument();
    expect(screen.getByText("1.45M")).toBeInTheDocument();
  });

  it("shows a transparency line naming the card count and the tax", () => {
    render(<TradeShareCardArt data={HAALAND} />);
    expect(screen.getByText("4 cards · after 5% EA tax")).toBeInTheDocument();
  });

  it("omits the card count from the transparency line for a single card", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, quantity: 1, roi: 42.5 }} />);
    expect(screen.getByText("After 5% EA tax")).toBeInTheDocument();
  });

  it("labels a loss as Loss, not Profit, and renders no '+' sign", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, profit: -50000, roi: -1.25 }} />);
    expect(screen.getByText("-50K")).toBeInTheDocument();
    expect(screen.getByText("Loss")).toBeInTheDocument();
    expect(screen.queryByText("Profit")).not.toBeInTheDocument();
  });

  it("renders no ROI stat when roi is null (no cost to divide by)", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, roi: null }} />);
    expect(screen.queryByText("ROI")).not.toBeInTheDocument();
  });

  it("shows just the player name, not a duplicated version/card caption", () => {
    // Version/rating/platform are already visible on the card art -
    // repeating the version as "· TOTS" here duplicated the photo.
    render(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/haaland.png" }} />);
    expect(screen.getByText("Haaland")).toBeInTheDocument();
    expect(screen.queryByText(/TOTS/)).not.toBeInTheDocument();
  });

  it("shrinks an unusually long player name instead of letting it overflow", () => {
    const longName = "Alexander Isak-Christiansen-Whitfield";
    render(<TradeShareCardArt data={{ ...HAALAND, player: longName }} format="portrait" />);
    const el = screen.getByText(longName);
    expect(parseInt(el.style.fontSize, 10)).toBeLessThan(30);
    // Belt-and-braces backstop even past the font-shrink floor.
    expect(el.style.textOverflow).toBe("ellipsis");
    expect(el.style.whiteSpace).toBe("nowrap");
  });

  it("never lets the player name silently collapse to zero height", () => {
    // Real bug, caught only by rendering through an actual layout
    // engine (jsdom doesn't lay out flexbox, so this only asserts the
    // fix's presence, not the failure mode itself): a flex item inside
    // a flex-direction:column container gets its automatic minimum
    // main-axis size forced to 0 by the spec once it has any overflow
    // other than visible. If the column ever ran even slightly over its
    // vertical budget, Chromium would collapse the player name to
    // height:0 - invisibly, with no visual overflow at all - before it
    // touched any sibling that lacked overflow:hidden. flexShrink:0
    // opts this element out, so a real overflow shows up as actual
    // clipping (loud) instead of the name just vanishing (silent).
    render(<TradeShareCardArt data={HAALAND} format="portrait" />);
    const el = screen.getByText("Haaland");
    expect(el.style.flexShrink).toBe("0");
  });

  it("shows the card rating on the fallback frame only when there is no real card art", () => {
    const { rerender } = render(<TradeShareCardArt data={{ ...HAALAND, image_url: null, rating: 91 }} />);
    expect(screen.getByText("91")).toBeInTheDocument();

    rerender(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/haaland.png", rating: 91 }} />);
    // The real card photo already shows the rating - no duplicate overlay.
    expect(screen.queryByText("91")).not.toBeInTheDocument();
  });

  it("shows the local placeholder art immediately when no image_url is given", () => {
    render(<TradeShareCardArt data={{ ...HAALAND, image_url: null }} />);
    const img = document.querySelector("[data-trade-share-thumb]");
    expect(img.src).toContain("/img/card-placeholder.png");
  });

  it("falls back to the placeholder, rating overlay included, when a real image_url fails to load", () => {
    // The exact bug this guards: a remote card photo that 404s or
    // CORS-fails left a blank bordered box with nothing but a tiny
    // browser broken-image glyph in it - recognisable as "this graphic
    // is broken," not "this card has no photo."
    render(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/gone.png", rating: 91 }} />);
    const img = document.querySelector("[data-trade-share-thumb]");
    expect(img.src).toBe("https://cdn.example.com/gone.png");
    expect(screen.queryByText("91")).not.toBeInTheDocument();

    fireEvent.error(img);

    expect(img.src).toContain("/img/card-placeholder.png");
    expect(screen.getByText("91")).toBeInTheDocument();
  });

  it("does not keep retrying a failed image_url across re-renders with the same url", () => {
    const { rerender } = render(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/gone.png" }} />);
    const img = document.querySelector("[data-trade-share-thumb]");
    fireEvent.error(img);
    expect(img.src).toContain("/img/card-placeholder.png");

    // A re-render that doesn't change image_url (e.g. a sibling prop
    // changing) must not re-attempt the URL that just failed.
    rerender(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/gone.png", profit: 1600000 }} />);
    expect(img.src).toContain("/img/card-placeholder.png");
  });

  it("gives a new image_url a fresh attempt after a previous one failed", () => {
    const { rerender } = render(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/gone.png" }} />);
    const img = document.querySelector("[data-trade-share-thumb]");
    fireEvent.error(img);
    expect(img.src).toContain("/img/card-placeholder.png");

    rerender(<TradeShareCardArt data={{ ...HAALAND, image_url: "https://cdn.example.com/fresh.png" }} />);
    expect(img.src).toBe("https://cdn.example.com/fresh.png");
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
    // Portrait still shows the same figures, just at a different scale.
    expect(screen.getByText("+38.2%")).toBeInTheDocument();
  });

  it("keeps the Haaland worked example exact internally while displaying it compact", () => {
    // The canonical regression example from the audit spec: buy 997,000
    // x4, sell 1,450,000 x4 -> profit 1,522,000, ROI 38.16%. Compact
    // formatting is display-only - this asserts both halves of that:
    // the props this component was handed are untouched, and what
    // actually paints is the rounded/abbreviated form, never the exact
    // figure recomputed or mutated by this component.
    const exact = { ...HAALAND, profit: 1522000, roi: 38.164493 };
    render(<TradeShareCardArt data={exact} />);
    expect(exact.profit).toBe(1522000);
    expect(exact.roi).toBe(38.164493);
    expect(screen.getByText("+1.52M")).toBeInTheDocument();
    expect(screen.getByText("+38.2%")).toBeInTheDocument();
    expect(screen.queryByText("+1,522,000")).not.toBeInTheDocument();
    expect(screen.queryByText(/38\.164/)).not.toBeInTheDocument();
  });
});
