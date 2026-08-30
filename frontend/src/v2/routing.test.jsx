// Guards the /v2 URL removal from silently regressing: a stray
// `to="/v2/..."` would still "work" (the redirect catches it) but would
// show the user a version-prefixed URL and cost them an extra navigation,
// which is exactly what the change removed.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = path.resolve(__dirname, "..");

function sourceFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sourceFiles(full));
    else if (/\.jsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

// A route string is one that opens with a quote/backtick or follows a
// hash marker. "/api/v2/..." (an API prefix, unchanged) and "./v2/..."
// (module imports) deliberately don't match.
const ROUTE_LIKE = /(["'`]|#)\/v2(?=["'`/])/;

function stripComments(text) {
  return text
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*");
    })
    .join("\n");
}

describe("app URLs", () => {
  it("contains no version-prefixed route strings", () => {
    const offenders = [];
    for (const file of sourceFiles(SRC)) {
      // App.jsx legitimately declares the legacy redirect route itself.
      if (file.endsWith(`${path.sep}App.jsx`)) continue;
      if (file.endsWith(".test.jsx") || file.endsWith(".test.js")) continue;
      const code = stripComments(fs.readFileSync(file, "utf8"));
      if (ROUTE_LIKE.test(code)) offenders.push(path.relative(SRC, file));
    }
    expect(offenders).toEqual([]);
  });

  it("still declares the legacy redirect, which must never be removed", () => {
    // Deep links with the old prefix are in posted Discord messages and
    // Stripe return URLs. Deleting this route breaks them permanently.
    const app = fs.readFileSync(path.join(SRC, "App.jsx"), "utf8");
    expect(app).toContain('path="/v2/*"');
    expect(app).toContain("LegacyV2Redirect");
  });
});
