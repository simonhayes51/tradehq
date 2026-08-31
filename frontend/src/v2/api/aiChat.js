const API_BASE = import.meta.env.VITE_API_URL || "";

export async function sendChatMessage(messages) {
  const response = await fetch(`${API_BASE}/api/v2/ai/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // The server's refusals now carry structure - which limit was hit,
    // and when it lifts (app/services/ai_usage.py). `detail` is an object
    // in those cases, and String()-ing it would print "[object Object]"
    // at a customer, so the fields are read out explicitly.
    const detail = body?.detail;
    const structured = detail && typeof detail === "object" ? detail : null;
    const message =
      structured?.message
      || (typeof detail === "string" ? detail : null)
      || body?.message
      || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.reason = structured?.reason || structured?.error || null;
    error.resetsAt = structured?.resetsAt || null;
    throw error;
  }
  return body;
}
