// src/pages/AdminUsers.jsx
// Admin-only: search users and grant/revoke premium, and manage Data API
// keys (upgrade a key's tier after a sale) - all from the browser, built
// because the deploy environment has no terminal access. Server-side gate
// is require_admin on /api/admin/*; the client-side isAdmin check is UX.
import React, { useState } from "react";
import { Shield, Search, Loader2, KeyRound, Users as UsersIcon } from "lucide-react";
import api from "../axios";
import { useEntitlements } from "../context/EntitlementsContext";

const LIME = "#91db32";

const TIER_STYLES = {
  elite: { bg: "rgba(250,204,21,0.15)", fg: "#facc15" },
  pro: { bg: "rgba(145,219,50,0.15)", fg: LIME },
  dev: { bg: "rgba(250,204,21,0.15)", fg: "#facc15" },
  trader: { bg: "rgba(145,219,50,0.15)", fg: LIME },
  basic: { bg: "rgba(255,255,255,0.08)", fg: "rgba(255,255,255,0.6)" },
  starter: { bg: "rgba(255,255,255,0.08)", fg: "rgba(255,255,255,0.6)" },
};

function TierChip({ tier }) {
  const s = TIER_STYLES[tier] || TIER_STYLES.basic;
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase"
      style={{ background: s.bg, color: s.fg }}
    >
      {tier || "basic"}
    </span>
  );
}

function TierButtons({ current, options, busy, onPick }) {
  return (
    <div className="flex gap-1.5 shrink-0">
      {options.map((t) => {
        const active = current === t || (t === "free" && (!current || current === "basic"));
        return (
          <button
            key={t}
            onClick={() => onPick(t)}
            disabled={busy}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold border active:scale-[0.98] disabled:opacity-50 ${
              active ? "border-transparent text-black" : "border-gray-700 text-gray-300 hover:border-gray-500"
            }`}
            style={active ? { background: LIME } : {}}
          >
            {t}
          </button>
        );
      })}
    </div>
  );
}

export default function AdminUsers() {
  const { isAdmin, loading: entLoading } = useEntitlements() || {};
  const [view, setView] = useState("users"); // users | keys
  const [q, setQ] = useState("");
  const [users, setUsers] = useState([]);
  const [keys, setKeys] = useState([]);
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState(null);
  const [msg, setMsg] = useState(null);

  const search = async (e) => {
    e?.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      if (view === "users") {
        const { data } = await api.get("/api/admin/users", { params: { q } });
        setUsers(data.users || []);
        if (!data.users?.length) setMsg({ ok: true, text: "No users matched." });
      } else {
        const { data } = await api.get("/api/admin/api-keys", { params: { q } });
        setKeys(data.keys || []);
        if (!data.keys?.length) setMsg({ ok: true, text: "No active keys matched." });
      }
    } catch (err) {
      setMsg({ ok: false, text: err?.userMessage || "Search failed - are you admin?" });
    } finally {
      setBusy(false);
    }
  };

  const setUserTier = async (user, tier) => {
    setRowBusy(`u${user.id}`);
    setMsg(null);
    try {
      const { data } = await api.post(`/api/admin/users/${user.id}/tier`, { tier });
      setUsers((us) =>
        us.map((u) => (u.id === user.id ? { ...u, tier: data.tier, premium_until: data.premium_until } : u))
      );
      setMsg({ ok: true, text: `${data.username || data.user_id}: ${data.previous_tier || "basic"} → ${data.tier}. Live within ~60s.` });
    } catch (err) {
      setMsg({ ok: false, text: err?.userMessage || "Update failed." });
    } finally {
      setRowBusy(null);
    }
  };

  const setKeyTier = async (key, tier) => {
    setRowBusy(`k${key.id}`);
    setMsg(null);
    try {
      const { data } = await api.post(`/api/admin/api-keys/${key.id}/tier`, { tier });
      setKeys((ks) =>
        ks.map((k) =>
          k.id === key.id ? { ...k, tier: data.tier, rpm: data.rpm, monthly_quota: data.monthly_quota } : k
        )
      );
      setMsg({ ok: true, text: `${data.key_prefix}…: now ${data.tier} (${data.rpm} rpm, ${data.monthly_quota.toLocaleString()}/mo). Live on next request.` });
    } catch (err) {
      setMsg({ ok: false, text: err?.userMessage || "Update failed." });
    } finally {
      setRowBusy(null);
    }
  };

  if (entLoading) return null;
  if (!isAdmin) {
    return (
      <div className="max-w-xl mx-auto px-4 pt-12 text-center text-gray-400 text-sm">
        <Shield className="mx-auto mb-2" size={20} />
        Admin only.
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-3 pb-24 pt-4">
      <div className="flex items-center gap-2">
        <Shield size={22} style={{ color: LIME }} />
        <h1 className="text-xl font-extrabold text-white">Admin</h1>
      </div>

      <div className="mt-3 inline-flex rounded-xl border border-gray-800 bg-gray-900/70 p-1">
        {[
          { id: "users", label: "Users", icon: UsersIcon },
          { id: "keys", label: "API Keys", icon: KeyRound },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => { setView(t.id); setMsg(null); }}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold inline-flex items-center gap-1.5 ${
              view === t.id ? "bg-gray-800 text-white" : "text-gray-400"
            }`}
          >
            <t.icon size={13} /> {t.label}
          </button>
        ))}
      </div>

      <p className="mt-2 text-sm text-gray-400">
        {view === "users"
          ? "Search by username or Discord ID, then grant or revoke premium. Audited; live within a minute."
          : "Data API sales fulfilment: after a buyer pays, find their key and bump its tier. Applies on their next request."}
      </p>

      <form onSubmit={search} className="mt-4 flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={view === "users" ? "Username or Discord ID…" : "Username, Discord ID or key prefix…"}
          className="flex-1 px-3 py-2 rounded-xl bg-gray-900 border border-gray-800 text-sm text-white placeholder-gray-500"
        />
        <button
          type="submit"
          disabled={busy}
          className="px-4 py-2 rounded-xl text-sm font-bold text-black inline-flex items-center gap-1.5 active:scale-[0.98] disabled:opacity-50"
          style={{ background: LIME }}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Search
        </button>
      </form>

      {msg && <p className={`mt-3 text-xs ${msg.ok ? "text-gray-300" : "text-red-400"}`}>{msg.text}</p>}

      {view === "users" && (
        <div className="mt-4 space-y-2">
          {users.map((u) => (
            <div key={u.id} className="rounded-xl border border-gray-800 bg-gray-900/60 p-3 flex flex-col sm:flex-row sm:items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-white truncate">{u.username || u.id}</span>
                  <TierChip tier={u.tier} />
                  {u.account_type && u.account_type !== "user" && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">{u.account_type}</span>
                  )}
                </div>
                <div className="text-[11px] text-gray-500 truncate">
                  {u.discord_id ? `discord ${u.discord_id}` : u.id}
                  {u.premium_until ? ` · until ${new Date(u.premium_until).toLocaleDateString()}` : ""}
                </div>
              </div>
              <TierButtons
                current={u.tier}
                options={["free", "pro", "elite"]}
                busy={rowBusy === `u${u.id}`}
                onPick={(t) => setUserTier(u, t)}
              />
            </div>
          ))}
        </div>
      )}

      {view === "keys" && (
        <div className="mt-4 space-y-2">
          {keys.map((k) => (
            <div key={k.id} className="rounded-xl border border-gray-800 bg-gray-900/60 p-3 flex flex-col sm:flex-row sm:items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-white truncate">{k.key_prefix}…</span>
                  <TierChip tier={k.tier} />
                </div>
                <div className="text-[11px] text-gray-500 truncate">
                  {k.username || k.user_id} · {k.name || "Untitled"} ·{" "}
                  {k.used_this_month.toLocaleString()}/{(k.monthly_quota || 0).toLocaleString()} this month
                  {k.last_used_at ? ` · last used ${new Date(k.last_used_at).toLocaleDateString()}` : " · never used"}
                </div>
              </div>
              <TierButtons
                current={k.tier}
                options={["starter", "trader", "dev"]}
                busy={rowBusy === `k${k.id}`}
                onPick={(t) => setKeyTier(k, t)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
