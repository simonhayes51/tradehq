import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import EmptyState from "./EmptyState";

describe("EmptyState", () => {
  it("renders the text and an optional title", () => {
    render(<EmptyState title="Nothing here" text="Come back later." />);
    expect(screen.getByRole("heading", { name: "Nothing here" })).toBeInTheDocument();
    expect(screen.getByText("Come back later.")).toBeInTheDocument();
  });

  it("omits the heading when no title is given", () => {
    render(<EmptyState text="Just text." />);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("renders the provided icon and action", () => {
    render(<EmptyState icon={<svg data-testid="icon" />} text="Empty" action={<button>Try again</button>} />);
    expect(screen.getByTestId("icon")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("flags the error/compact tones via className, matching the CSS in tokens.css", () => {
    const { container, rerender } = render(<EmptyState text="x" />);
    expect(container.firstChild.className).toBe("v2-empty-state");

    rerender(<EmptyState text="x" error compact />);
    expect(container.firstChild.className).toBe("v2-empty-state is-error is-compact");
  });
});
