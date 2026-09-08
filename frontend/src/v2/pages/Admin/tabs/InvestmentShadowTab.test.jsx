import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const query = vi.hoisted(() => ({ value: {
  data: {
    engine: { enabled: true, publishing: false, mode: "SHADOW" },
    decisions: [{ strategy: "rebound", cohort: "premium_gold", decision: "SUPPRESS", reason: "COHORT_STILL_CRASHING", count: 40, graded_24h: 32, graded_72h: 18, median_roi_24h_pct: -4.2, median_roi_72h_pct: -6.1 }],
    by_market_state: [{ market_state: "CRASHING", decision: "SUPPRESS", n: 40, median_roi_24h_pct: -4.2 }],
    signals: [{ strategy: "rebound", cohort: "premium_gold", n: 6 }],
  },
  isLoading: false, error: null, isFetching: false, refetch: vi.fn(),
}}));

vi.mock("../../../hooks/useAdminInvestmentShadow", () => ({ useAdminInvestmentShadow: () => query.value }));
import InvestmentShadowTab from "./InvestmentShadowTab";

describe("InvestmentShadowTab", () => {
  it("makes shadow/publication separation explicit and shows suppressed outcomes", () => {
    render(<InvestmentShadowTab />);
    expect(screen.getByText("Investment Engine")).toBeInTheDocument();
    expect(screen.getByText("SHADOW")).toBeInTheDocument();
    expect(screen.getByText("KEEP SHADOW")).toBeInTheDocument();
    expect(screen.getByText("COHORT_STILL_CRASHING")).toBeInTheDocument();
    expect(screen.getByText("CRASHING · SUPPRESS")).toBeInTheDocument();
  });
});
