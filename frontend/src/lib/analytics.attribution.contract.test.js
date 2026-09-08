import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "analytics.js"), "utf8");

describe("launch attribution contract", () => {
  it("captures only bounded partner/campaign codes into first-party funnel events", () => {
    expect(source).toContain('params.get("ref")');
    expect(source).toContain('params.get("utm_source")');
    expect(source).toContain('params.get("utm_campaign")');
    expect(source).toContain('"source"');
    expect(source).toContain('"campaign"');
    expect(source).toContain("slice(0, 40)");
    expect(source).not.toContain("document.referrer");
  });
});
