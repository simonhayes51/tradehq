import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RecommendationBadge from "./RecommendationBadge";

describe("RecommendationBadge", () => {
  it("renders known recommendations", () => {
    render(<RecommendationBadge recommendation="buy" />);
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("renders a visible fallback instead of nothing for an unrecognized status", () => {
    // The exact bug class documented in this component's header comment:
    // a real status the backend can emit (e.g. a future engine version,
    // or a caller passing the raw uppercase status by mistake) must never
    // silently render nothing.
    render(<RecommendationBadge recommendation="WAIT" />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("still renders nothing for a genuinely absent recommendation", () => {
    const { container } = render(<RecommendationBadge recommendation={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
