// src/v2/pages/Admin/tabs/SubscriptionsTab.jsx
import { useState } from "react";
import { Search, ShieldCheck } from "lucide-react";
import SectionCard from "../../../components/SectionCard";
import { useAdminSubscriptions } from "../../../hooks/useAdminSubscriptions";
import { formatCountdown } from "../../../lib/format";
import { api } from "../../../lib/api";

const STATUS_TONE = { active: "text-[var(--v2-positive)]", trialing: "text-[var(--v2-positive)]" };
const GRANT_OPTIONS = [
  { value: "permanent", label: "Permanent" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
];

function manualAccessLabel(user) {
  const tier = String(user?.tier || "").toLowerCase();
  if (!["elite", "pro", "premium"].includes(tier)) return "No manual Elite grant";
  if (!user.premium_until) return "Manual Elite · no expiry";
  const date = new Date(user.premium_until);
  if (!Number.isFinite(date.getTime())) return "Manual Elite";
  return `Manual Elite · until ${date.toLocaleDateString("en-GB")}`;
}

export default function SubscriptionsTab() {
  const { data, isLoading, error } = useAdminSubscriptions({ limit: 30 });
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState([]);
  const [searching, setSearching] = useState(false);
  const [busyUserId, setBusyUserId] = useState(null);
  const [grantDays, setGrantDays] = useState({});
  const [message, setMessage] = useState(null);

  const searchUsers = async (event) => {
    event?.preventDefault();
    setSearching(true);
    setMessage(null);
    try {
      const { data: result } = await api.get("/api/admin/users", { params: { q: query, limit: 30 } });
      const matched = result?.users || [];
      setUsers(matched);
      if (!matched.length) setMessage({ ok: true, text: "No users matched that search." });
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't search users." });
    } finally {
      setSearching(false);
    }
  };

  const updateManualAccess = async (user, tier) => {
    setBusyUserId(user.id);
    setMessage(null);
    try {
      const selected = grantDays[user.id] || "permanent";
      const payload = tier === "elite"
        ? { tier: "elite", ...(selected === "permanent" ? {} : { days: Number(selected) }) }
        : { tier: "free" };
      const { data: result } = await api.post(`/api/admin/users/${user.id}/tier`, payload);
      setUsers((current) => current.map((item) => item.id === user.id
        ? { ...item, tier: result.tier, premium_until: result.premium_until }
        : item));
      setMessage({
        ok: true,
        text: tier === "elite"
          ? `${result.username || result.user_id} now has manual Elite access${result.premium_until ? ` until ${new Date(result.premium_until).toLocaleDateString("en-GB")}` : " with no expiry"}.`
          : `${result.username || result.user_id}'s manual Elite grant was removed. Any genuine Stripe subscription remains untouched.`,
      });
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't update manual access." });
    } finally {
      setBusyUserId(null);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <SectionCard
        title="Manual Elite access"
        subtitle="Grant Elite without creating a Stripe subscription. Every change is admin-audited."
      >
        <div className="rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3 text-[11px] leading-relaxed text-[var(--v2-muted)]">
          Manual access is separate from billing. Removing a manual grant does not cancel or override a real paid Stripe subscription.
        </div>

        <form onSubmit={searchUsers} className="mt-3 flex gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Username, Discord ID or user ID"
            className="min-w-0 flex-1 rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-xs text-[var(--v2-text)]"
          />
          <button
            type="submit"
            disabled={searching}
            className="inline-flex min-h-9 items-center gap-1.5 rounded border border-[var(--v2-accent)] px-3 text-xs font-semibold text-[var(--v2-accent)] disabled:opacity-50"
          >
            <Search size={14} /> {searching ? "Searching..." : "Search"}
          </button>
        </form>

        {message ? (
          <p className={`mt-3 text-xs ${message.ok ? "text-[var(--v2-muted)]" : "text-[var(--v2-negative)]"}`}>{message.text}</p>
        ) : null}

        {users.length ? (
          <ul className="mt-3 flex flex-col divide-y divide-[var(--v2-border)] border-t border-[var(--v2-border)]">
            {users.map((user) => {
              const manualPaid = ["elite", "pro", "premium"].includes(String(user.tier || "").toLowerCase());
              const busy = busyUserId === user.id;
              return (
                <li key={user.id} className="grid gap-3 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="truncate text-sm">{user.username || user.id}</strong>
                      {manualPaid ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-[var(--v2-accent)] px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-[var(--v2-accent)]">
                          <ShieldCheck size={10} /> Manual Elite
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-[11px] text-[var(--v2-muted)]">
                      {manualAccessLabel(user)}
                      {user.plan ? ` · account plan: ${user.plan}` : ""}
                      {user.discord_id ? ` · Discord ${user.discord_id}` : ""}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={grantDays[user.id] || "permanent"}
                      onChange={(event) => setGrantDays((current) => ({ ...current, [user.id]: event.target.value }))}
                      disabled={busy}
                      aria-label={`Grant duration for ${user.username || user.id}`}
                      className="min-h-9 rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-2 text-xs text-[var(--v2-text)]"
                    >
                      {GRANT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => updateManualAccess(user, "elite")}
                      className="min-h-9 rounded bg-[var(--v2-accent)] px-3 text-xs font-bold text-[#07120d] disabled:opacity-50"
                    >
                      {busy ? "Updating..." : manualPaid ? "Extend / replace" : "Grant Elite"}
                    </button>
                    {manualPaid ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => updateManualAccess(user, "free")}
                        className="min-h-9 rounded border border-[var(--v2-border)] px-3 text-xs font-semibold text-[var(--v2-muted)] disabled:opacity-50"
                      >
                        Remove manual grant
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </SectionCard>

      <SectionCard title="Stripe subscriptions" subtitle="Billing records, most recent first">
        {isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
        ) : error ? (
          <p className="text-xs text-[var(--v2-negative)]">Couldn't load subscriptions.</p>
        ) : (data?.items || []).length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No subscriptions recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
            {(data?.items || []).map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 py-2 text-xs">
                <div className="min-w-0">
                  <p className="truncate font-medium">{s.username || s.user_id}</p>
                  <p className="mt-0.5 text-[var(--v2-muted)]">
                    {s.plan_id}
                    {s.cancel_at_period_end ? " · cancels at period end" : ""}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className={`font-medium ${STATUS_TONE[s.status] || "text-[var(--v2-muted)]"}`}>{s.status}</p>
                  <p className="mt-0.5 text-[var(--v2-muted)]">
                    {s.current_period_end ? `renews in ${formatCountdown(s.current_period_end)}` : "—"}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
