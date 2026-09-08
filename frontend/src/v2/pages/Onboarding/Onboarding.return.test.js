import { describe, expect, it } from "vitest";
import { safeOnboardingReturnTo } from "./Onboarding";

describe("onboarding return destination", () => {
  it("preserves a same-app destination", () => {
    expect(safeOnboardingReturnTo("/billing?unlock=elite")).toBe("/billing?unlock=elite");
    expect(safeOnboardingReturnTo("/players/12345")).toBe("/players/12345");
  });

  it("falls back to Home when no destination was supplied", () => {
    expect(safeOnboardingReturnTo(null)).toBe("/");
    expect(safeOnboardingReturnTo("")).toBe("/");
  });

  it("rejects protocol-relative and external destinations", () => {
    expect(safeOnboardingReturnTo("//evil.example/path")).toBe("/");
    expect(safeOnboardingReturnTo("https://evil.example/path")).toBe("/");
  });
});
