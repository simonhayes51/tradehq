import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import StatTile from "./StatTile";

describe("StatTile", () => {
  it("renders label and value", () => {
    render(<StatTile label="Win rate" value="62%" />);
    expect(screen.getByText("Win rate")).toBeInTheDocument();
    expect(screen.getByText("62%")).toBeInTheDocument();
  });

  it("omits the delta row when delta is undefined or null", () => {
    const { container, rerender } = render(<StatTile label="Profit" value="1,200" />);
    expect(container.querySelectorAll("span")).toHaveLength(2);

    rerender(<StatTile label="Profit" value="1,200" delta={null} />);
    expect(container.querySelectorAll("span")).toHaveLength(2);
  });

  it("renders the delta when provided, even the falsy-but-valid 0", () => {
    render(<StatTile label="Profit" value="1,200" delta={0} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("colors value/delta by tone", () => {
    render(<StatTile label="ROI" value="12%" valueTone="positive" delta="-3%" deltaTone="negative" />);
    expect(screen.getByText("12%")).toHaveClass("text-[var(--v2-positive)]");
    expect(screen.getByText("-3%")).toHaveClass("text-[var(--v2-negative)]");
  });
});
