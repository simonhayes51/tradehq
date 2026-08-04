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
