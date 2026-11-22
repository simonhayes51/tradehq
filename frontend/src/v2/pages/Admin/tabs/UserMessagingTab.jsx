import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import SectionCard from "../../../components/SectionCard";

export default function UserMessagingTab() {
  const [audience, setAudience] = useState(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const refresh = () => api.get("/api/admin/discord/user-messaging/audience")
    .then(({ data }) => setAudience(data))
    .catch(() => setResult("Could not load audience."));
  useEffect(() => { refresh(); }, []);
  const send = async (test = false) => {
    if (!content.trim()) return;
    if (!test && !window.confirm(`Send this message to up to ${Math.min(audience?.opted_in || 0, 50)} opted-in users? This cannot be undone.`)) return;
    setBusy(true);
    setResult("");
    try {
      const payload = { content: content.trim() };
      if (test) {
        // Backend resolves the administrator's linked Discord identity.
        const { data } = await api.get("/api/admin/discord/user-messaging/test-recipient");
        payload.test_discord_id = data.discord_id;
      }
      const { data } = await api.post("/api/admin/discord/user-messaging/send", payload);
      setResult(`Attempted ${data.attempted} · Sent ${data.sent} · Failed ${data.failed}`);
      refresh();
    } catch (error) {
      setResult(error?.userMessage || "Could not send message.");
    } finally {
      setBusy(false);
    }
  };
  return <SectionCard title="User messaging" subtitle="Discord DMs only to users who explicitly opt in. Discord login alone is not consent.">
    <p className="text-sm text-[var(--v2-muted)]">
      {audience ? `${audience.total} registered · ${audience.discord_linked} Discord-linked · ${audience.opted_in} opted in` : "Loading audience..."}
    </p>
    <p className="mt-2 text-xs text-[var(--v2-muted)]">Maximum 50 recipients per send. Messages may fail if a user blocks bot DMs. Do not repeat sends to the same audience.</p>
    <textarea className="mt-3 w-full rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3 text-sm" rows={5} maxLength={1800} value={content} onChange={e => setContent(e.target.value)} placeholder="Write a message for users who opted in..." />
    <div className="mt-3 flex gap-3">
      <button disabled={busy || !content.trim()} onClick={() => send(true)} className="rounded border border-[var(--v2-border)] px-3 py-2 text-xs">Test on my Discord</button>
      <button disabled={busy || !content.trim() || !audience?.opted_in} onClick={() => send(false)} className="rounded bg-[var(--v2-accent)] px-3 py-2 text-xs font-bold text-[#07120d]">Send to opted-in users</button>
    </div>
    {result && <p role="status" className="mt-3 text-sm">{result}</p>}
  </SectionCard>;
}
