// src/v2/pages/Admin/tabs/NextPromoTab.jsx
//
// The only place this calendar can be written to. See useNextPromo.js -
// this is a separate table from the SBC/market events feed shown in
// MarketEventsTab.jsx, a plain name/kind/start_at row the Home dashboard
// reads via GET /api/events/next. Recurring drops (TOTW most Wednesdays,
// a promo most Fridays) aren't predicted or guessed here - EA's calendar
// slips often enough that a wrong guess shown as fact is worse than
// nothing - so this is a manual add, meant to be used weekly as each
// promo is actually confirmed.
import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useNextPromo, useCreatePromo } from "../../../hooks/useNextPromo";

const KIND_OPTIONS = ["totw", "promo", "campaign", "icon", "hero", "objective"];

function toLocalInputValue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function NextPromoTab() {
  const { data, isLoading, error, refetch } = useNextPromo();
  const createPromo = useCreatePromo();
  const [form, setForm] = useState({ name: "", kind: "totw", start_at: "", confidence: "confirmed" });

  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.start_at) return;
    await createPromo.mutateAsync({
      name: form.name.trim(),
      kind: form.kind,
      start_at: new Date(form.start_at).toISOString(),
      confidence: form.confidence,
    });
    setForm({ name: "", kind: "totw", start_at: "", confidence: "confirmed" });
    refetch();
  }

  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Currently scheduled" subtitle="What Home's Market Pulse panel is showing right now">
        {isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
        ) : error ? (
          <p className="text-xs text-[var(--v2-negative)]">Couldn't load the next promo.</p>
        ) : data?.name ? (
          <div className="text-sm">
            <p className="font-semibold text-[var(--v2-text)]">{data.name}</p>
            <p className="text-xs text-[var(--v2-muted)] mt-1">
              {data.kind} · {toLocalInputValue(data.start_at).replace("T", " ")} · {data.confidence}
            </p>
          </div>
        ) : (
          <p className="text-xs text-[var(--v2-muted)]">Nothing scheduled - Home won't show a "Next promo" line until one is added below.</p>
        )}
      </SectionCard>

      <SectionCard title="Add the next one" subtitle="Once EA's calendar for it is actually confirmed, not a guess">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
            Name
            <input
              type="text" required value={form.name} onChange={set("name")}
              placeholder="e.g. TOTW 35"
              className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Kind
              <select value={form.kind} onChange={set("kind")} className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]">
                {KIND_OPTIONS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Confidence
              <select value={form.confidence} onChange={set("confidence")} className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]">
                <option value="confirmed">Confirmed</option>
                <option value="heuristic">Heuristic (likely, not confirmed)</option>
              </select>
            </label>
          </div>
          <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
            Starts at (your local time)
            <input
              type="datetime-local" required value={form.start_at} onChange={set("start_at")}
              className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]"
            />
          </label>
          {createPromo.isError ? <p className="text-xs text-[var(--v2-negative)]">Couldn't save that - check you're still signed in as an admin.</p> : null}
          <button
            type="submit" disabled={createPromo.isPending}
            className="self-start rounded-[var(--v2-radius)] bg-[var(--v2-accent)] px-4 py-2 text-xs font-bold text-black disabled:opacity-60"
          >
            {createPromo.isPending ? "Saving..." : "Add promo"}
          </button>
        </form>
      </SectionCard>
    </div>
  );
}
