import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const query = vi.hoisted(() => ({ value: {
  data: {
    mode: "SHADOW_ONLY", demands_checked: 2,
    status_counts: { MARKET_CONFIRMED_CANDIDATE: 1, PENDING: 1 },
    writes_market_status: false, changes_priority_weight: false,
    items: [
      { demand_id: 1, resource_type: "TOTW", resource_value: "totw", shadow_status: "MARKET_CONFIRMED_CANDIDATE", raw_text: "Min. 1 TOTW Player", reason: "Price and completed-sales activity both strengthened.", evidence: { matched_cards: 40, price_change_pct: 4.1, sales_activity_ratio: 1.4 } },
      { demand_id: 2, resource_type: "EXACT_RATING", resource_value: "87", shadow_status: "PENDING", raw_text: "Min. 1 87 Rated Player", reason: "Observation window not mature.", evidence: { matched_cards: 25, pending: true } },
    ],
  }, isLoading: false, error: null, isFetching: false, refetch: vi.fn(),
}}));

vi.mock("../../../hooks/useAdminSbcReinforcement", () => ({ useAdminSbcReinforcement: () => query.value }));
import SbcReinforcementTab from "./SbcReinforcementTab";

describe("SbcReinforcementTab", () => {
  it("shows market evidence while making the no-write guardrail explicit", () => {
    render(<SbcReinforcementTab />);
    expect(screen.getByText("SBC Market Reinforcement")).toBeInTheDocument();
    expect(screen.getByText("SHADOW_ONLY")).toBeInTheDocument();
    expect(screen.getAllByText("NO")).toHaveLength(2);
    expect(screen.getByText("MARKET CONFIRMED CANDIDATE")).toBeInTheDocument();
    expect(screen.getByText("4.1%")).toBeInTheDocument();
    expect(screen.getByText("1.40×")).toBeInTheDocument();
  });
});
