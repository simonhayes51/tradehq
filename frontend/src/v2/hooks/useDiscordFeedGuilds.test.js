import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = readFileSync(fileURLToPath(new URL("./useDiscordFeedGuilds.js", import.meta.url)), "utf8");

describe("Server Feed guild discovery contract", () => {
  it("is not coupled to the personal Elite tier", () => {
    expect(source).toContain('api.get("/api/v2/discord-feed/guilds"');
    expect(source).not.toContain("isElite");
    expect(source).not.toContain('tier === "elite"');
  });
});
