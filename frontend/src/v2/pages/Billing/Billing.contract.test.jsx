import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./Billing.jsx", import.meta.url), "utf8");

describe("Billing launch contract", () => {
  it("derives trial wording from the billing catalogue rather than hardcoding seven days", () => {
    expect(source).toContain("plans?.trial_days");
    expect(source).toContain("const hasTrial = trialDays > 0");
    expect(source).not.toContain("7-day free trial included");
    expect(source).not.toContain("Start 7-day free trial");
  });

  it("uses Elite consistently in customer-facing paid-plan copy", () => {
    expect(source).toContain("ELITE ACTIVE");
    expect(source).toContain("the Elite channel");
    expect(source).not.toContain("PREMIUM ACTIVE");
    expect(source).not.toContain("the premium channel");
  });

  it("does not make a value claim when a real season saving cannot be calculated", () => {
    expect(source).toContain("seasonSaving ? <span");
    expect(source).not.toContain('"Best value"');
  });

  it("fails checkout closed when live Stripe pricing is unavailable", () => {
    expect(source).toContain("const checkoutAvailable = activePlan?.configured === true && Boolean(priceDisplay)");
    expect(source).toContain("disabled={loading || !checkoutAvailable}");
    expect(source).toContain("if (loading || !checkoutAvailable) return");
  });
});
