import { beforeEach, describe, expect, it, vi } from "vitest";

const shared = vi.hoisted(() => ({
  api: { post: vi.fn() },
}));

vi.mock("../lib/api", () => ({ api: shared.api }));

import { sendChatMessage } from "./aiChat";

beforeEach(() => {
  shared.api.post.mockReset();
});

describe("AI chat API", () => {
  it("posts through the shared v2 HTTP client", async () => {
    shared.api.post.mockResolvedValue({ data: { reply: "hello" } });

    await expect(sendChatMessage([{ role: "user", content: "hi" }])).resolves.toEqual({ reply: "hello" });
    expect(shared.api.post).toHaveBeenCalledWith("/api/v2/ai/chat", {
      messages: [{ role: "user", content: "hi" }],
    });
  });

  it("preserves structured rate-limit metadata for the chat hook", async () => {
    shared.api.post.mockRejectedValue({
      response: {
        status: 429,
        data: {
          detail: {
            error: "daily_limit",
            message: "You've used today's AI allowance.",
            resetsAt: "2026-09-09T00:00:00Z",
          },
        },
      },
    });

    await expect(sendChatMessage([{ role: "user", content: "hi" }])).rejects.toMatchObject({
      status: 429,
      reason: "daily_limit",
      resetsAt: "2026-09-09T00:00:00Z",
      message: "You've used today's AI allowance.",
    });
  });

  it("preserves a structured 402 so the shared client can refresh entitlements", async () => {
    const response = {
      status: 402,
      data: { detail: { feature: "ai_chat", message: "AI Chat is part of Elite." } },
    };
    shared.api.post.mockRejectedValue({ response, userMessage: "This feature requires Elite" });

    await expect(sendChatMessage([{ role: "user", content: "hi" }])).rejects.toMatchObject({
      status: 402,
      message: "AI Chat is part of Elite.",
      response,
    });
  });
});
