import { beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock("../lib/api", () => ({ api: shared.api }));

import { getOpenTrades, openTrade, shareSummaryCard } from "./trades";

beforeEach(() => {
  shared.api.get.mockReset();
  shared.api.post.mockReset();
  shared.api.patch.mockReset();
});

describe("v2 trade journal API", () => {
  it("opens a trade through the shared client", async () => {
    shared.api.post.mockResolvedValue({ data: { id: 123 } });
    const payload = { card_id: 99, buy: 10000 };

    await expect(openTrade(payload)).resolves.toEqual({ id: 123 });
    expect(shared.api.post).toHaveBeenCalledWith("/api/v2/trades/open", payload);
  });

  it("does not wrap structured shared-client failures", async () => {
    const blocked = {
      response: {
        status: 402,
        data: { detail: { error: "limit_reached", feature: "trade_journal", used: 5, limit: 5 } },
      },
    };
    shared.api.post.mockRejectedValue(blocked);

    await expect(openTrade({ card_id: 99 })).rejects.toBe(blocked);
  });

  it("keeps read and share routes on the same client too", async () => {
    shared.api.get.mockResolvedValue({ data: { trades: [] } });
    shared.api.post.mockResolvedValue({ data: { image_data_url: "data:image/png;base64,x" } });

    await getOpenTrades();
    await shareSummaryCard("week", "portrait");

    expect(shared.api.get).toHaveBeenCalledWith("/api/v2/trades/open");
    expect(shared.api.post).toHaveBeenCalledWith(
      "/api/trades/summary-share-card",
      null,
      { params: { period: "week", format: "portrait" } },
    );
  });
});
