import { useEffect, useMemo, useState } from "react";
import { api } from "../../../lib/api";
import SectionCard from "../../../components/SectionCard";

const ACCESS_OPTIONS = [
  { value: "free", label: "Free · 1/day" },
  { value: "partner", label: "Partner · 6/day" },
  { value: "partner_plus", label: "Partner+ · 15/day" },
];

function planLabel(server) {
  if (server.access_level === "free") return "Free";
  if (server.plan === "server_feed_plus" || Number(server.max_posts_per_day) >= 15) return "Partner+";
  if (server.access_level === "official") return "Official";
  return "Partner";
}

function statusTone(status) {
  return status === "active" ? "text-[var(--v2-positive)]" : "text-[var(--v2-muted)]";
}

export default function BotTab() {
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [grantLevel, setGrantLevel] = useState({});
  const [grantDays, setGrantDays] = useState({});
  const [drafts, setDrafts] = useState({});
  const [broadcast, setBroadcast] = useState("");
  const [broadcasting, setBroadcasting] = useState(false);
  const [newServer, setNewServer] = useState({ guild_id: "", name: "" });
  const [adding, setAdding] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/api/admin/discord/servers");
      setServers(Array.isArray(data) ? data : []);
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't load Discord servers." });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const activeCount = useMemo(() => servers.filter((s) => s.status === "active").length, [servers]);
  const partnerCount = useMemo(() => servers.filter((s) => s.access_level === "paid_partner").length, [servers]);

  const addServer = async (event) => {
    event.preventDefault();
    if (!newServer.guild_id.trim() || !newServer.name.trim()) return;
    setAdding(true);
    setMessage(null);
    try {
      await api.post("/api/admin/discord/servers", {
        guild_id: newServer.guild_id.trim(),
        name: newServer.name.trim(),
        access_level: "paid_partner",
      });
      setNewServer({ guild_id: "", name: "" });
      setMessage({ ok: true, text: "Server added. Set its feed channel and grant access when ready." });
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't add server." });
    } finally {
      setAdding(false);
    }
  };

  const grantAccess = async (server) => {
    setBusyId(server.id);
    setMessage(null);
    try {
      const level = grantLevel[server.id] || (Number(server.max_posts_per_day) >= 15 ? "partner_plus" : server.access_level === "free" ? "free" : "partner");
      const rawDays = grantDays[server.id] || "30";
      const payload = { level };
      if (level !== "free" && rawDays !== "none") payload.days = Number(rawDays);
      await api.put(`/api/admin/discord/servers/${server.id}/manual-access`, payload);
      setMessage({ ok: true, text: `${server.name} access updated.` });
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't update access." });
    } finally {
      setBusyId(null);
    }
  };

  const toggleServer = async (server) => {
    setBusyId(server.id);
    setMessage(null);
    try {
      const action = server.status === "active" ? "pause" : "resume";
      await api.post(`/api/admin/discord/servers/${server.id}/${action}`);
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't update server status." });
    } finally {
      setBusyId(null);
    }
  };

  const saveChannel = async (server) => {
    const channel = String(drafts[`channel:${server.id}`] ?? server.feed_channel_id ?? "").trim();
    if (!channel) return;
    setBusyId(server.id);
    try {
      await api.put(`/api/admin/discord/servers/${server.id}/channel`, { feed_channel_id: channel });
      setMessage({ ok: true, text: `${server.name} feed channel updated.` });
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't save channel." });
    } finally {
      setBusyId(null);
    }
  };

  const sendOne = async (server) => {
    const content = String(drafts[`message:${server.id}`] || "").trim();
    if (!content) return;
    setBusyId(server.id);
    setMessage(null);
    try {
      await api.post(`/api/admin/discord/servers/${server.id}/message`, { content });
      setDrafts((d) => ({ ...d, [`message:${server.id}`]: "" }));
      setMessage({ ok: true, text: `Message sent to ${server.name}.` });
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't send message." });
    } finally {
      setBusyId(null);
    }
  };

  const sendBroadcast = async () => {
    const content = broadcast.trim();
    if (!content) return;
    setBroadcasting(true);
    setMessage(null);
    try {
      const { data } = await api.post("/api/admin/discord/broadcast", { content });
      setBroadcast("");
      setMessage({ ok: true, text: `Broadcast sent to ${data.sent} server${data.sent === 1 ? "" : "s"}${data.failed ? `, ${data.failed} failed` : ""}.` });
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't send broadcast." });
    } finally {
      setBroadcasting(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <SectionCard title="Discord bot" subtitle={`${servers.length} registered · ${activeCount} active · ${partnerCount} partner servers`}>
        {message ? <p className={`mb-3 text-xs ${message.ok ? "text-[var(--v2-positive)]" : "text-[var(--v2-negative)]"}`}>{message.text}</p> : null}
        <form onSubmit={addServer} className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
          <input value={newServer.name} onChange={(e) => setNewServer((v) => ({ ...v, name: e.target.value }))} placeholder="Server name" className="rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-xs" />
          <input value={newServer.guild_id} onChange={(e) => setNewServer((v) => ({ ...v, guild_id: e.target.value }))} placeholder="Discord Guild ID" className="rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-xs" />
          <button disabled={adding} className="rounded bg-[var(--v2-accent)] px-3 py-2 text-xs font-bold text-[#07120d] disabled:opacity-50">{adding ? "Adding..." : "Add server"}</button>
        </form>
      </SectionCard>

      <SectionCard title="Broadcast" subtitle="Push a manual EAFC.Live message into every active Partner/Official feed channel. These do not use a trade-post allowance.">
        <textarea value={broadcast} onChange={(e) => setBroadcast(e.target.value)} rows={4} maxLength={1900} placeholder="Message to all active partner feeds..." className="w-full rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3 text-xs" />
        <div className="mt-2 flex justify-end">
          <button type="button" onClick={sendBroadcast} disabled={broadcasting || !broadcast.trim()} className="rounded bg-[var(--v2-accent)] px-3 py-2 text-xs font-bold text-[#07120d] disabled:opacity-50">{broadcasting ? "Sending..." : "Send to all active feeds"}</button>
        </div>
      </SectionCard>

      <SectionCard title="Servers" subtitle="See who has added the bot, their current access, feed allowance and subscription state.">
        {loading ? <p className="text-xs text-[var(--v2-muted)]">Loading...</p> : servers.length === 0 ? <p className="text-xs text-[var(--v2-muted)]">No Discord servers registered yet.</p> : (
          <div className="flex flex-col gap-4">
            {servers.map((server) => {
              const busy = busyId === server.id;
              return (
                <div key={server.id} className="rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-sm">{server.name}</strong>
                        <span className="rounded-full border border-[var(--v2-border)] px-2 py-0.5 text-[10px]">{planLabel(server)}</span>
                        <span className={`text-[10px] font-semibold uppercase ${statusTone(server.status)}`}>{server.status}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-[var(--v2-muted)]">Guild {server.guild_id}{server.owner_discord_id ? ` · owner ${server.owner_discord_id}` : ""}</p>
                      <p className="mt-1 text-[11px] text-[var(--v2-muted)]">Subscription: {server.subscription_status || "none"} · allowance: {server.max_posts_per_day ?? "—"}/day · bot removed: {server.bot_removed_at ? "yes" : "no"}</p>
                    </div>
                    <button type="button" disabled={busy} onClick={() => toggleServer(server)} className="rounded border border-[var(--v2-border)] px-3 py-2 text-xs disabled:opacity-50">{server.status === "active" ? "Pause" : "Resume"}</button>
                  </div>

                  <div className="mt-4 grid gap-2 md:grid-cols-[1fr_auto]">
                    <input value={drafts[`channel:${server.id}`] ?? server.feed_channel_id ?? ""} onChange={(e) => setDrafts((d) => ({ ...d, [`channel:${server.id}`]: e.target.value }))} placeholder="Feed channel ID" className="rounded border border-[var(--v2-border)] bg-[var(--v2-bg)] px-3 py-2 text-xs" />
                    <button type="button" disabled={busy} onClick={() => saveChannel(server)} className="rounded border border-[var(--v2-border)] px-3 py-2 text-xs disabled:opacity-50">Save channel</button>
                  </div>

                  <div className="mt-3 grid gap-2 md:grid-cols-[1fr_140px_auto]">
                    <select value={grantLevel[server.id] || (Number(server.max_posts_per_day) >= 15 ? "partner_plus" : server.access_level === "free" ? "free" : "partner")} onChange={(e) => setGrantLevel((v) => ({ ...v, [server.id]: e.target.value }))} className="rounded border border-[var(--v2-border)] bg-[var(--v2-bg)] px-3 py-2 text-xs">
                      {ACCESS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <select value={grantDays[server.id] || "30"} onChange={(e) => setGrantDays((v) => ({ ...v, [server.id]: e.target.value }))} className="rounded border border-[var(--v2-border)] bg-[var(--v2-bg)] px-3 py-2 text-xs">
                      <option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="none">No expiry</option>
                    </select>
                    <button type="button" disabled={busy} onClick={() => grantAccess(server)} className="rounded bg-[var(--v2-accent)] px-3 py-2 text-xs font-bold text-[#07120d] disabled:opacity-50">Apply access</button>
                  </div>

                  <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto]">
                    <textarea value={drafts[`message:${server.id}`] || ""} onChange={(e) => setDrafts((d) => ({ ...d, [`message:${server.id}`]: e.target.value }))} rows={2} maxLength={1900} placeholder="Send a one-off message into this feed..." className="rounded border border-[var(--v2-border)] bg-[var(--v2-bg)] p-3 text-xs" />
                    <button type="button" disabled={busy || !String(drafts[`message:${server.id}`] || "").trim()} onClick={() => sendOne(server)} className="rounded border border-[var(--v2-accent)] px-3 py-2 text-xs font-semibold text-[var(--v2-accent)] disabled:opacity-50">Send message</button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
