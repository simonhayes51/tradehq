import { api } from "../lib/api";

export async function sendChatMessage(messages) {
  try {
    return (await api.post("/api/v2/ai/chat", { messages })).data;
  } catch (requestError) {
    // Keep the hook-facing error contract exactly as before while using
    // the shared client underneath. That gives AI Chat the same auth and
    // structured-402 handling as the rest of v2 without losing the
    // assistant-specific reason/reset metadata used for rate-limit copy.
    const status = requestError?.response?.status;
    const detail = requestError?.response?.data?.detail;
    const structured = detail && typeof detail === "object" ? detail : null;
    const message =
      structured?.message
      || (typeof detail === "string" ? detail : null)
      || requestError?.response?.data?.message
      || requestError?.userMessage
      || requestError?.message
      || (status ? `Request failed (${status})` : "Could not reach the assistant");

    const error = new Error(message);
    error.status = status;
    error.reason = structured?.reason || structured?.error || null;
    error.resetsAt = structured?.resetsAt || null;
    error.response = requestError?.response;
    throw error;
  }
}
