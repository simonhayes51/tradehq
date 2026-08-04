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
    const error = new Error(body?.detail || body?.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}
