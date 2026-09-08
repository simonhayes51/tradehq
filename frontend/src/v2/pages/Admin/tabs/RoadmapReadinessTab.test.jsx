import { describe, expect, it } from "vitest";
import { classifyRoadmapReadiness } from "./RoadmapReadinessTab";

function baseData() {
  return {
    intelligence: {
      status: "ok",
      current_coverage_pct: 100,
      dashboard_market_cache: { live_fallback_enabled: false },
    },
    salesShadow: {
      status: "ok",
      pickup_analysis: {
        measured_shadow_card_events: 120,
        repeated_missed_cards: [],
      },
    },
    calibration: {
      calibration_allowed: true,
      blockers: [],
      review_candidates: [],
      version_integrity: { config_clean: true },
    },
    investments: {
      engine: { enabled: true, publishing: false, mode: "SHADOW" },
      decisions: [],
    },
    sbc: {
      mode: "SHADOW_ONLY",
      writes_market_status: false,
      changes_priority_weight: false,
      demands_checked: 3,
      status_counts: {},
    },
    openTrades: {
      trades: [
        { current_position_analysis: { trigger: "hold" }, current_evaluated_at: "2026-09-08T10:00:00Z" },
      ],
    },
  };
}

function byName(items, name) {
  return items.find((item) => item.name === name);
}

describe("roadmap readiness classification", () => {
  it("marks healthy architecture as ready while evidence-gated investment/SBC remain waiting", () => {
    const items = classifyRoadmapReadiness(baseData());
    expect(byName(items, "Core Intelligence").status).toBe("READY");
    expect(byName(items, "Held Positions").status).toBe("READY");
    expect(byName(items, "SalesSync Reallocation").status).toBe("READY");
    expect(byName(items, "Recommendation Calibration").status).toBe("READY");
    expect(byName(items, "Investment Engine").status).toBe("WAITING");
    expect(byName(items, "SBC Reinforcement").status).toBe("WAITING");
  });

  it("never treats incomplete held-position analysis as ready", () => {
    const data = baseData();
    data.openTrades.trades.push({ current_position_analysis: null, current_evaluated_at: null });
    const items = classifyRoadmapReadiness(data);
    expect(byName(items, "Held Positions").status).toBe("BLOCKED");
  });

  it("surfaces statistically defensible calibration candidates for review, not automatic change", () => {
    const data = baseData();
    data.calibration.review_candidates = [{ dimension: "confidence" }];
    const item = byName(classifyRoadmapReadiness(data), "Recommendation Calibration");
    expect(item.status).toBe("REVIEW");
    expect(item.next).toMatch(/Review manually/i);
  });

  it("blocks accidental investment publication before evidence review", () => {
    const data = baseData();
    data.investments.engine.publishing = true;
    data.investments.engine.mode = "PUBLISHING";
    const item = byName(classifyRoadmapReadiness(data), "Investment Engine");
    expect(item.status).toBe("BLOCKED");
    expect(item.next).toMatch(/Disable publishing/i);
  });

  it("keeps SalesSync evidence-gated until enough mature observations exist", () => {
    const data = baseData();
    data.salesShadow.pickup_analysis.measured_shadow_card_events = 12;
    const item = byName(classifyRoadmapReadiness(data), "SalesSync Reallocation");
    expect(item.status).toBe("WAITING");
    expect(item.next).toMatch(/60\/300s/i);
  });
});
