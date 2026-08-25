// Regression test for a specific mobile-polish-pass finding: an AVOID
// card could show a large green "Return after tax" figure right next
// to the AVOID badge, because the engine's naive profit math against a
// now-invalid target (e.g. a falling-knife discount, see
// futgg_intelligence.py's trend_blocks branch) is still positive even
// though the recommendation is explicitly not to act on it. The number
// itself isn't wrong, but presenting it as a positive/green metric next
// to "AVOID" reads as self-contradictory.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisModal } from "./HomeDashboard";

vi.mock("./ModalPriceChart", () => ({ default: () => <div data-testid="chart-stub" /> }));

const AVOID_ITEM = {
  cardId: 999,
  source: "futgg",
  recommendation: "AVOID",
  entryPrice: 640000,
  currentBin: 640000,
  fairValue: 700000,
  expectedProfitAfterTax: 43000,
  expectedRoi: 6.7,
  confidence: 40,
  risk: "High",
  liquidityScore: 0.5,
  player: { name: "Erling Haaland", rating: 98, position: "ST", version: "Icon" },
};

describe("AnalysisModal on an AVOID card", () => {
  it("never colours the profit figure green and labels it hypothetical", () => {
    render(
      <AnalysisModal item={AVOID_ITEM} onClose={() => {}} navigate={() => {}} onWatch={() => {}} watchState="" variant="page" />,
    );

    expect(screen.getByText("AVOID")).toBeInTheDocument();
    expect(screen.getByText("Return after tax")).toBeInTheDocument();
    // The metric tile must not carry the "positive" (green) tone class,
    // even though the underlying number is positive.
    const metricLabel = screen.getByText("Return after tax");
    const metricTile = metricLabel.closest(".metric");
    expect(metricTile.className).not.toMatch(/positive/);
    expect(screen.getByText("Hypothetical - not a recommendation")).toBeInTheDocument();

    // The reasons list must not present the profit figure as a
    // positive/checkmark reason to buy.
    expect(screen.queryByText(/estimated after EA tax/)).not.toBeInTheDocument();
    expect(screen.getByText(/recommendation is to avoid this card/i)).toBeInTheDocument();
  });
});
