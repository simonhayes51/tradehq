// Labels that used to print the engine's own enum values at customers:
// "team_of_the_week" as a card chip, "low risk" / "unknown risk" as a
// card footer. Both are internal vocabulary.
import { describe, expect, it } from "vitest";
import { riskLabel, versionLabel } from "./format";

describe("versionLabel", () => {
  it("humanises a snake_case rarity key", () => {
    expect(versionLabel("team_of_the_week")).toBe("Team Of The Week");
  });

  it("never leaks an underscore", () => {
    expect(versionLabel("trailblazers_plus")).not.toContain("_");
  });

  it("falls back to a word, not an empty chip", () => {
    expect(versionLabel(null)).toBe("Card");
    expect(versionLabel("")).toBe("Card");
    expect(versionLabel("   ")).toBe("Card");
  });

  it("normalises case rather than shouting", () => {
    expect(versionLabel("GOLD RARE")).toBe("Gold Rare");
  });
});

describe("riskLabel", () => {
  it("reads as a sentence, not an enum", () => {
    expect(riskLabel("low")).toBe("Low risk");
    expect(riskLabel("medium")).toBe("Medium risk");
  });

  it("says missing data is missing rather than 'unknown risk'", () => {
    // "unknown risk" reads as a warning about the card; the truth is
    // that the engine has no risk read on it.
    expect(riskLabel(null)).toBe("Risk not rated");
    expect(riskLabel("unknown")).toBe("Risk not rated");
  });

  it("does not append 'risk' to the avoid verdict", () => {
    expect(riskLabel("avoid")).toBe("Avoid");
  });
});
