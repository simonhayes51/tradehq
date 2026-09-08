import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../..");

function read(rel) {
  return fs.readFileSync(path.resolve(frontend, rel), "utf8");
}

describe("Companion launch readiness", () => {
  it("keeps the customer launcher season-neutral", () => {
    const source = read("src/pages/Companion.jsx");
    expect(source).toContain("EA SPORTS FC Web App Transfer Market");
    expect(source).not.toContain("EA SPORTS FC 26 Web App");
  });

  it("prepares the served userscript before every production build", () => {
    const pkg = JSON.parse(read("package.json"));
    const prepare = read("scripts/prepare-companion.mjs");
    expect(pkg.scripts.build).toContain("prepare-companion.mjs");
    expect(prepare).toContain("1.10.0");
    expect(prepare).toContain("FC_GAME_RE");
    expect(prepare).toContain("throw new Error");
  });
});
