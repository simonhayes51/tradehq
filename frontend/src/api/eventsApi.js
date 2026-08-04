const API_BASE = import.meta.env.VITE_API_URL || "";

export async function fetchNextEvent() {
  const r = await fetch(`${API_BASE}/api/events/next`);
  if (!r.ok) throw new Error(`Failed: ${r.status}`);
  return r.json();
}

export async function createEvent(eventData) {
  const r = await fetch(`${API_BASE}/api/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(eventData)
  });
  if (!r.ok) throw new Error(`Failed: ${r.status}`);
  return r.json();
}
