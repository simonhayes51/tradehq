import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

function read(rel) {
  return fs.readFileSync(path.resolve(here, rel), "utf8");
}

describe("Companion bundle preparation", () => {
  it("contains the exact FC26-to-season-neutral migration guard", () => {
    const source = read("./prepare-companion.mjs");
    expect(source).toContain("// @version      1.9.1");
    expect(source).toContain("// @version      1.10.0");
    expect(source).toContain("FC26_RE");
    expect(source).toContain("FC_GAME_RE");
    expect(source).toContain("fc\\\\d+");
    expect(source).toContain("expected ${label} was not found");
  });
});
