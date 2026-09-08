import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

function read(rel) {
  return fs.readFileSync(path.resolve(here, rel), "utf8");
}

describe("paid gate naming", () => {
  it("uses Elite on the current v2 gate", () => {
    const source = read("./PremiumGate.jsx");
    expect(source).toContain("Elite feature");
    expect(source).toContain("available on Elite");
    expect(source).not.toContain("Premium feature");
    expect(source).not.toContain("available on Premium");
  });

  it("uses Elite on the surviving legacy paid gate", () => {
    const source = read("../../components/PremiumGate.jsx");
    expect(source).toContain("Elite Feature");
    expect(source).toContain("available on the Elite plan");
    expect(source).not.toContain("Premium Feature");
    expect(source).not.toContain("available on the Premium plan");
  });
});
