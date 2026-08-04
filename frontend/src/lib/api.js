// src/lib/api.js
export const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

if (!API_BASE) {
  throw new Error("VITE_API_URL is missing. Set it to your FastAPI origin.");
}

export async function apiFetch(path, opts = {}) {
  const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, {
    credentials: "include", // send session cookie
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });

  // Don’t call res.json() blindly; catch the HTML case
  const ct = res.headers.get("content-type") || "";
  const text = await res.text();
  if (!ct.includes("application/json")) {
    throw new Error(`Non-JSON from ${url} (status ${res.status}): ${text.slice(0, 120)}…`);
  }
  const json = JSON.parse(text);
  if (!res.ok) {
    const msg = json?.detail || text;
    throw new Error(`${res.status} ${msg}`);
  }
  return json;
}
