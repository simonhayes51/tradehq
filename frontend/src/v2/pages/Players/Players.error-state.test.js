import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

describe("Players error recovery", () => {
  it("offers an explicit retry action for a failed search", () => {
    const source = fs.readFileSync(path.resolve(here, "./Players.jsx"), "utf8");
    expect(source).toContain("Player search is unavailable right now.");
    expect(source).toContain("futggQuery.refetch()");
    expect(source).toContain("Try again");
  });
});
