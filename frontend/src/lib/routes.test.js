import { describe, expect, it } from "vitest";
import { legacyV2Target } from "./routes";

describe("legacyV2Target", () => {
  it("maps the old app root to the new one", () => {
    expect(legacyV2Target("/v2")).toBe("/");
    expect(legacyV2Target("/v2/")).toBe("/");
  });

  it("keeps the rest of the path, including params", () => {
    expect(legacyV2Target("/v2/players/117493814")).toBe("/players/117493814");
    expect(legacyV2Target("/v2/track-record")).toBe("/track-record");
  });

  it("never rewrites a path that merely starts with the letters v2", () => {
    // "/v2games" is not a versioned path and must be left alone - the
    // lookahead is what stops a prefix match from eating it.
    expect(legacyV2Target("/v2games")).toBe("/v2games");
  });

  it("leaves an already-current path untouched", () => {
    expect(legacyV2Target("/opportunities")).toBe("/opportunities");
  });
});
