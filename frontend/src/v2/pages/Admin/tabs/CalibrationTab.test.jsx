import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({
  report: {
    data: {
      engine_version: "futgg-2.3",
      grader_version: "grader-1",
      calibration_allowed: true,
      blockers: [],
      version_integrity: { config_clean: true, config_fingerprints: 1 },
      dimensions: {
        overall: [{ sample_size: 240, profitable_call_rate_pct: 61.2 }],
        confidence: [
          { bucket: "0.75-0.85", sample_size: 120, entry_rate_pct: 90, profitable_call_rate_pct: 55, profitable_call_rate_ci95_pct: [46, 64], median_roi_pct: 4.1, avg_mae_pct: -2.2 },
          { bucket: "0.85+", sample_size: 120, entry_rate_pct: 93, profitable_call_rate_pct: 72, profitable_call_rate_ci95_pct: [63, 79], median_roi_pct: 6.3, avg_mae_pct: -1.8 },
        ],
      },
      review_candidates: [{
        dimension: "confidence",
        lower_bucket: "0.75-0.85",
        higher_bucket: "0.85+",
        lower_sample: 120,
        higher_sample: 120,
        profitable_call_rate_delta_pp: 17,
        p_value: 0.002,
      }],
    },
    isLoading: false,
    error: null,
  },
  decisions: {
    data: { items: [{ id: 1, parameter: "min_confidence_for_buy_signal", status: "proposed", from_engine_version: "futgg-2.3", rationale: "Outcome evidence warrants review." }] },
    isLoading: false,
  },
}));

vi.mock("../../../hooks/useAdminCalibration", () => ({
  useAdminCalibration: () => hooks.report,
  useAdminCalibrationDecisions: () => hooks.decisions,
}));

import CalibrationTab from "./CalibrationTab";

describe("CalibrationTab", () => {
  beforeEach(() => {});

  it("renders calibration readiness and statistically defensible candidates", () => {
    render(<CalibrationTab />);
    expect(screen.getByText("Engine Calibration")).toBeInTheDocument();
    expect(screen.getByText("YES")).toBeInTheDocument();
    expect(screen.getByText("240")).toBeInTheDocument();
    expect(screen.getByText(/confidence: 0.75-0.85/)).toBeInTheDocument();
    expect(screen.getByText("Confidence Calibration")).toBeInTheDocument();
    expect(screen.getByText("min_confidence_for_buy_signal")).toBeInTheDocument();
  });

  it("shows blockers instead of pretending a threshold change is allowed", () => {
    hooks.report = {
      ...hooks.report,
      data: {
        ...hooks.report.data,
        calibration_allowed: false,
        blockers: ["multiple_engine_configs_share_version"],
        review_candidates: [],
        version_integrity: { config_clean: false, config_fingerprints: 2 },
      },
    };
    render(<CalibrationTab />);
    expect(screen.getByText("NO")).toBeInTheDocument();
    expect(screen.getByText(/multiple engine configs share version/)).toBeInTheDocument();
    expect(screen.getByText(/No threshold move is currently justified/)).toBeInTheDocument();
  });
});
