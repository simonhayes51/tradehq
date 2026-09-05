// Coverage for the BIN history estimate badge - a BIN-derived valuation
// (app/services/futgg_bin_valuation.py, evidenceQuality === "estimated")
// must be visibly and unmistakably distinct from a confirmed completed
// sale, both in the modal's own badge and in its reasons list, and that
// distinction must never appear on a normal confirmed-sales result.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisModal } from "./HomeDashboard";

vi.mock("./ModalPriceChart", () => ({ default: () => <div data-testid="chart-stub" /> }));

const BIN_ESTIMATE_ITEM = {
  cardId: 501,
  source: "futgg",
  recommendation: "BUY",
  entryPrice: 7000,
  currentBin: 7000,
  fairValue: 10000,
  recommendedBuyMax: 7900,
  expectedProfitBasis: 7000,
  expectedProfitAfterTax: 1930,
  expectedRoi: 27.6,
  confidence: 42,
  risk: "Medium",
  liquidityScore: 0.3,
  recommendedSellTarget: 9400,
  valuationSource: "bin_history",
  evidenceQuality: "estimated",
  player: { name: "Test Player", rating: 82, position: "CM", version: "Gold Rare" },
};

const CONFIRMED_SALES_ITEM = {
  ...BIN_ESTIMATE_ITEM,
  cardId: 502,
  valuationSource: "completed_sales",
  evidenceQuality: "confirmed",
};

describe("AnalysisModal BIN-history estimate labelling", () => {
  it("shows the BIN history estimate badge and explanatory copy for an estimated valuation", () => {
    render(
      <AnalysisModal item={BIN_ESTIMATE_ITEM} onClose={() => {}} navigate={() => {}} onWatch={() => {}} watchState="" variant="page" />,
    );
    expect(screen.getByText("BIN history estimate")).toBeInTheDocument();
    expect(
      screen.getByText(/based on recent asking-price movement, not confirmed sales/i),
    ).toBeInTheDocument();
  });

  it("never shows the badge or the estimate copy for a confirmed completed-sales result", () => {
    render(
      <AnalysisModal item={CONFIRMED_SALES_ITEM} onClose={() => {}} navigate={() => {}} onWatch={() => {}} watchState="" variant="page" />,
    );
    expect(screen.queryByText("BIN history estimate")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/based on recent asking-price movement, not confirmed sales/i),
    ).not.toBeInTheDocument();
  });
});
