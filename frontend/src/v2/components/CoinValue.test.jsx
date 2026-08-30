import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CoinValue, { coinText } from "./CoinValue";

describe("coinText", () => {
  it("renders an empty dash for null (no data), not a fabricated zero", () => {
    // The exact bug this test guards: Number(null) === 0 in JS, unlike
    // Number(undefined) === NaN, so a naive Number.isFinite check let an
    // explicit "no data" null through as a real, displayable 0.
    expect(coinText(null)).toBe("—");
  });

  it("renders an empty dash for undefined", () => {
    expect(coinText(undefined)).toBe("—");
  });

  it("renders a real zero as 0, not as missing data", () => {
    expect(coinText(0)).toBe("0");
  });

  it("renders a real positive value formatted", () => {
    expect(coinText(500000)).toBe("500,000");
  });
});

describe("CoinValue", () => {
  it("renders the empty state for null", () => {
    render(<CoinValue value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders a real zero, not the empty state", () => {
    render(<CoinValue value={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});

describe("coin icon", () => {
  it("is inlined, not fetched from a third-party CDN", () => {
    // It used to be an <img src="https://cdn2.futbin.com/...">: one
    // request per coin figure, to the CDN of the data source this
    // product migrated off, on every screen.
    const { container } = render(<CoinValue value={1000} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg.coin-icon")).not.toBeNull();
    expect(container.innerHTML).not.toContain("futbin");
  });

  it("hides the icon from assistive tech and labels the value instead", () => {
    const { container } = render(<CoinValue value={1000} />);
    expect(container.querySelector("svg").getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector(".coin-value").getAttribute("aria-label")).toBe("1,000 coins");
  });
});
