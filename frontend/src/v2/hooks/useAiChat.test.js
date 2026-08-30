// How the assistant's refusals reach the customer.
//
// The server now says WHICH limit was hit and WHEN it lifts. Before this,
// every 429 rendered "you're sending messages a bit fast - give it a
// moment", which is false for someone who has used their whole day's
// allowance: there is no moment that fixes it, and the sentence invites
// them to keep retrying.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ send: vi.fn() }));
vi.mock("../api/aiChat", () => ({ sendChatMessage: api.send }));

import { useAiChat } from "./useAiChat";

function refusal(status, message, extra = {}) {
  const error = new Error(message);
  error.status = status;
  Object.assign(error, extra);
  return error;
}

beforeEach(() => { api.send.mockReset(); });

describe("limit refusals", () => {
  it("shows the server's own sentence for a daily allowance, not 'try again shortly'", async () => {
    api.send.mockRejectedValue(refusal(
      429,
      "You've reached today's fair-use limit for the assistant. It resets at 00:00 UTC - everything else on your plan is unaffected.",
      { reason: "day", resetsAt: "2026-09-01T00:00:00+00:00" },
    ));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.error).toMatch(/resets at 00:00 UTC/);
    expect(result.current.error).not.toMatch(/give it a moment/);
  });

  it("still says what it means for a per-minute throttle", async () => {
    api.send.mockRejectedValue(refusal(429, "Too many chat messages - please wait a moment.", { reason: "minute" }));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.error).toMatch(/wait a moment/));
  });

  it("explains a concurrent send rather than reading as a fault", async () => {
    api.send.mockRejectedValue(refusal(
      429, "You already have a message in progress. Wait for that answer before sending another.",
      { reason: "concurrent" },
    ));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.error).toMatch(/already have a message in progress/));
  });

  it("shows the kill switch's own message rather than 'not switched on yet'", async () => {
    api.send.mockRejectedValue(refusal(
      503, "The assistant is switched off right now. Everything else on your plan is unaffected.",
      { reason: "disabled" },
    ));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.unavailable).toBeTruthy());
    expect(result.current.unavailable).toMatch(/switched off right now/);
  });

  it("never renders a refusal as though the assistant said it", async () => {
    api.send.mockRejectedValue(refusal(429, "You've reached today's fair-use limit.", { reason: "day" }));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.error).toBeTruthy());
    // The customer's own message stays; no assistant turn is appended.
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].role).toBe("user");
  });

  it("keeps a plan lock separate from a rate limit", async () => {
    api.send.mockRejectedValue(refusal(402, "Elite only"));
    const { result } = renderHook(() => useAiChat());
    await act(async () => { await result.current.send("hello"); });
    await waitFor(() => expect(result.current.locked).toBe(true));
    expect(result.current.error).toBe("");
  });
});
