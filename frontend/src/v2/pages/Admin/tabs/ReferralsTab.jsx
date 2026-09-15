// src/v2/pages/Admin/tabs/ReferralsTab.jsx
import { useEffect, useMemo, useState } from "react";
import { Copy, Plus, Power, RefreshCw } from "lucide-react";
import SectionCard from "../../../components/SectionCard";
import { api } from "../../../lib/api";

function pct(value) {
  const n = Number(value || 0);
  return `${Number.isFinite(n) ? n.toFixed(n % 1 ? 1 : 0) : "0"}%`;
}

function Stat({ label, value }) {
  return (
    <div className="rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] p-3">
      <p className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</p>
      <p className="mt-1 text-lg font-semibold text-[var(--v2-text)]">{value}</p>
    </div>
  );
}

export default function ReferralsTab() {
  const [partners, setPartners] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyCode, setBusyCode] = useState(null);
  const [message, setMessage] = useState(null);
  const [form, setForm] = useState({ partner_name: "", code: "" });

  const loadPartners = async () => {
    setLoading(true);
    setMessage(null);
    try {
      const { data } = await api.get("/api/referrals/admin/partners");
      setPartners(data?.partners || []);
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't load partner referrals." });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPartners();
  }, []);

  const totals = useMemo(() => partners.reduce((acc, p) => ({
    clicks: acc.clicks + Number(p.total_clicks || 0),
    unique: acc.unique + Number(p.unique_clicks || 0),
    signups: acc.signups + Number(p.signups || 0),
    active: acc.active + Number(p.active_subscriptions || 0),
  }), { clicks: 0, unique: 0, signups: 0, active: 0 }), [partners]);

  const createPartner = async (event) => {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      const payload = {
        partner_name: form.partner_name.trim(),
        code: form.code.trim().toUpperCase(),
        is_active: true,
      };
      const { data } = await api.post("/api/referrals/admin/partners", payload);
      setPartners((current) => [data.partner, ...current]);
      setForm({ partner_name: "", code: "" });
      setMessage({ ok: true, text: `${data.partner.partner_name} created. ${data.partner.share_url}` });
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setMessage({ ok: false, text: detail || err?.userMessage || "Couldn't create partner code." });
    } finally {
      setSaving(false);
    }
  };

  const togglePartner = async (partner) => {
    setBusyCode(partner.code);
    setMessage(null);
    try {
      const { data } = await api.patch(`/api/referrals/admin/partners/${encodeURIComponent(partner.code)}`, {
        is_active: !partner.is_active,
      });
      setPartners((current) => current.map((item) => item.code === partner.code
        ? { ...item, ...data.partner }
        : item));
    } catch (err) {
      setMessage({ ok: false, text: err?.userMessage || "Couldn't update partner code." });
    } finally {
      setBusyCode(null);
    }
  };

  const copyLink = async (partner) => {
    try {
      await navigator.clipboard.writeText(partner.share_url);
      setMessage({ ok: true, text: `${partner.code} link copied.` });
    } catch {
      setMessage({ ok: false, text: `Copy failed. Link: ${partner.share_url}` });
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <SectionCard
        title="Partner referrals"
        subtitle="Create partner codes and see who is actually driving registrations and paying users."
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Clicks" value={totals.clicks} />
          <Stat label="Unique" value={totals.unique} />
          <Stat label="Sign-ups" value={totals.signups} />
          <Stat label="Active / trial" value={totals.active} />
        </div>

        <form onSubmit={createPartner} className="mt-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_180px_auto]">
          <input
            value={form.partner_name}
            onChange={(e) => setForm((current) => ({ ...current, partner_name: e.target.value }))}
            placeholder="Partner / server name"
            required
            minLength={2}
            className="min-h-10 rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 text-xs text-[var(--v2-text)]"
          />
          <input
            value={form.code}
            onChange={(e) => setForm((current) => ({ ...current, code: e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, "") }))}
            placeholder="CODE"
            required
            minLength={3}
            maxLength={32}
            className="min-h-10 rounded border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 text-xs font-semibold uppercase tracking-wide text-[var(--v2-text)]"
          />
          <button
            type="submit"
            disabled={saving}
            className="inline-flex min-h-10 items-center justify-center gap-1.5 rounded bg-[var(--v2-accent)] px-4 text-xs font-bold text-[#07120d] disabled:opacity-50"
          >
            <Plus size={14} /> {saving ? "Creating..." : "Add partner"}
          </button>
        </form>

        {message ? (
          <p className={`mt-3 text-xs ${message.ok ? "text-[var(--v2-muted)]" : "text-[var(--v2-negative)]"}`}>{message.text}</p>
        ) : null}
      </SectionCard>

      <SectionCard
        title="Partner performance"
        subtitle="Unique conversion is sign-ups divided by unique referral visitors."
      >
        <div className="mb-3 flex justify-end">
          <button
            type="button"
            onClick={loadPartners}
            disabled={loading}
            className="inline-flex min-h-8 items-center gap-1.5 rounded border border-[var(--v2-border)] px-2.5 text-[11px] font-semibold text-[var(--v2-muted)] disabled:opacity-50"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        {loading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
        ) : !partners.length ? (
          <p className="text-xs text-[var(--v2-muted)]">No partner referral codes yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="border-b border-[var(--v2-border)] text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">
                <tr>
                  <th className="px-2 py-2 font-medium">Partner</th>
                  <th className="px-2 py-2 font-medium">Code</th>
                  <th className="px-2 py-2 text-right font-medium">Clicks</th>
                  <th className="px-2 py-2 text-right font-medium">Unique</th>
                  <th className="px-2 py-2 text-right font-medium">Sign-ups</th>
                  <th className="px-2 py-2 text-right font-medium">Active</th>
                  <th className="px-2 py-2 text-right font-medium">Conv.</th>
                  <th className="px-2 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--v2-border)]">
                {partners.map((partner) => {
                  const busy = busyCode === partner.code;
                  return (
                    <tr key={partner.code} className={!partner.is_active ? "opacity-55" : ""}>
                      <td className="px-2 py-3">
                        <div className="font-medium text-[var(--v2-text)]">{partner.partner_name}</div>
                        <div className="mt-0.5 text-[10px] text-[var(--v2-muted)]">{partner.is_active ? "Active" : "Disabled"}</div>
                      </td>
                      <td className="px-2 py-3 font-mono font-semibold">{partner.code}</td>
                      <td className="px-2 py-3 text-right">{partner.total_clicks}</td>
                      <td className="px-2 py-3 text-right">{partner.unique_clicks}</td>
                      <td className="px-2 py-3 text-right">{partner.signups}</td>
                      <td className="px-2 py-3 text-right">{partner.active_subscriptions}</td>
                      <td className="px-2 py-3 text-right">{pct(partner.conversion_rate)}</td>
                      <td className="px-2 py-3">
                        <div className="flex justify-end gap-1.5">
                          <button
                            type="button"
                            onClick={() => copyLink(partner)}
                            className="inline-flex min-h-8 items-center gap-1 rounded border border-[var(--v2-border)] px-2 text-[10px] font-semibold"
                            title={partner.share_url}
                          >
                            <Copy size={11} /> Link
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => togglePartner(partner)}
                            className="inline-flex min-h-8 items-center gap-1 rounded border border-[var(--v2-border)] px-2 text-[10px] font-semibold disabled:opacity-50"
                          >
                            <Power size={11} /> {partner.is_active ? "Disable" : "Enable"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
