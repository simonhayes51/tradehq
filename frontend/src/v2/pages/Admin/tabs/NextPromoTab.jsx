// src/v2/pages/Admin/tabs/NextPromoTab.jsx
import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import {
  useUpcomingEvents,
  useCreatePromo,
  useDeleteUpcomingEvent,
} from "../../../hooks/useNextPromo";

const KIND_OPTIONS = [
  "promo",
  "release",
  "early-access",
  "season",
  "content-drop",
  "totw",
  "campaign",
  "icon",
  "hero",
  "objective",
  "maintenance",
  "other",
];

function toLocalInputValue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function displayDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function NextPromoTab() {
  const { data, isLoading, error } = useUpcomingEvents();
  const createEvent = useCreatePromo();
  const deleteEvent = useDeleteUpcomingEvent();
  const [form, setForm] = useState({ name: "", kind: "promo", start_at: "", end_at: "", confidence: "confirmed" });

  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.start_at) return;
    await createEvent.mutateAsync({
      name: form.name.trim(),
      kind: form.kind,
      start_at: new Date(form.start_at).toISOString(),
      end_at: form.end_at ? new Date(form.end_at).toISOString() : null,
      confidence: form.confidence,
    });
    setForm({ name: "", kind: "promo", start_at: "", end_at: "", confidence: "confirmed" });
  }

  async function remove(id, name) {
    if (!window.confirm(`Remove ${name}?`)) return;
    await deleteEvent.mutateAsync(id);
  }

  const items = data?.items || [];

  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Upcoming events" subtitle="Promos, FC27 dates, early access, seasons and other known market dates">
        {isLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
        ) : error ? (
          <p className="text-xs text-[var(--v2-negative)]">Couldn't load upcoming events.</p>
        ) : items.length ? (
          <div className="flex flex-col divide-y divide-[var(--v2-border)]">
            {items.map((item, index) => (
              <div key={item.id} className="flex items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-semibold text-[var(--v2-text)]">{item.name}</p>
                    {index === 0 ? (
                      <span className="rounded-full border border-[var(--v2-accent)] px-2 py-0.5 text-[10px] font-semibold text-[var(--v2-accent)]">NEXT</span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-[var(--v2-muted)]">
                    {item.kind} · {displayDate(item.start_at)} · {item.confidence}
                  </p>
                  {item.end_at ? <p className="mt-0.5 text-xs text-[var(--v2-muted)]">Ends {displayDate(item.end_at)}</p> : null}
                </div>
                <button
                  type="button"
                  onClick={() => remove(item.id, item.name)}
                  disabled={deleteEvent.isPending}
                  className="shrink-0 rounded-[var(--v2-radius)] border border-[var(--v2-negative)] px-3 py-1.5 text-xs font-semibold text-[var(--v2-negative)] disabled:opacity-60"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[var(--v2-muted)]">Nothing scheduled.</p>
        )}
      </SectionCard>

      <SectionCard title="Add upcoming event" subtitle="Add as many confirmed or likely dates as you need">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
            Name
            <input
              type="text" required value={form.name} onChange={set("name")}
              placeholder="e.g. FC27 Early Access"
              className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Type
              <select value={form.kind} onChange={set("kind")} className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]">
                {KIND_OPTIONS.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Confidence
              <select value={form.confidence} onChange={set("confidence")} className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]">
                <option value="confirmed">Confirmed</option>
                <option value="heuristic">Likely / not confirmed</option>
              </select>
            </label>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Starts at
              <input
                type="datetime-local" required value={form.start_at} onChange={set("start_at")}
                className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--v2-muted)]">
              Ends at (optional)
              <input
                type="datetime-local" value={form.end_at} onChange={set("end_at")}
                min={toLocalInputValue(form.start_at ? new Date(form.start_at).toISOString() : null)}
                className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-elevated)] px-3 py-2 text-sm text-[var(--v2-text)]"
              />
            </label>
          </div>

          {createEvent.isError ? <p className="text-xs text-[var(--v2-negative)]">Couldn't save that event.</p> : null}
          {deleteEvent.isError ? <p className="text-xs text-[var(--v2-negative)]">Couldn't remove that event.</p> : null}

          <button
            type="submit" disabled={createEvent.isPending}
            className="self-start rounded-[var(--v2-radius)] bg-[var(--v2-accent)] px-4 py-2 text-xs font-bold text-black disabled:opacity-60"
          >
            {createEvent.isPending ? "Saving..." : "Add event"}
          </button>
        </form>
      </SectionCard>
    </div>
  );
}
