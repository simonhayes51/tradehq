// src/v2/pages/Admin/tabs/ConversionTab.jsx
//
// The conversion funnel for the Free/Elite journey.
//
// Practical, not a general analytics platform: a fixed set of questions
// with the answers already aggregated server-side. Everything on this
// page arrives as totals - no raw event rows are sent to the browser.
//
// Two things it is careful to say out loud, because both are easy to
// misread and expensive to misread:
//   * signed-out numbers are EVENT counts, not people
//   * revenue is the Stripe-confirmed number, not the browser's
import { useState } from "react";
import SectionCard from "../../../components/SectionCard";
import { useAdminFunnel, useAdminAiUsage } from "../../../hooks/useAdminFunnel";

const WINDOWS = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "90d", label: "90 days" },
];

function pct(value) {
  return value == null ? "—" : `${value}%`;
}
function num(value) {
  return value == null ? "—" : Number(value).toLocaleString();
}
function hours(value) {
  if (value == null) return "—";
  if (value < 1) return `${Math.round(value * 60)} min`;
  if (value < 48) return `${value.toFixed(1)} h`;
  return `${(value / 24).toFixed(1)} days`;
}

function Stat({ label, value, note }) {
  return (
    <div className="flex flex-col gap-0.5 py-2">
      <span className="text-[10px] uppercase tracking-wide text-[var(--v2-muted)]">{label}</span>
      <span className="text-lg font-black tabular-nums">{value}</span>
      {note ? <span className="text-[10px] text-[var(--v2-muted)]">{note}</span> : null}
    </div>
  );
}

// The funnel proper. Each step shows its own count and how much of the
// step above it survived, which is the only way a funnel is readable.
function FunnelStep({ label, value, of, ofLabel }) {
  const rate = of ? Math.round((value / of) * 1000) / 10 : null;
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-xs">
      <span>{label}</span>
      <span className="flex items-center gap-3">
        <span className="tabular-nums font-bold">{num(value)}</span>
        {rate != null ? (
          <span className="text-[10px] text-[var(--v2-muted)] w-28 text-right">
            {rate}% of {ofLabel}
          </span>
        ) : <span className="w-28" />}
      </span>
    </li>
  );
}

export default function ConversionTab() {
  const [window, setWindow] = useState("30d");
  const { data, isLoading, error } = useAdminFunnel({ window });
  const ai = useAdminAiUsage({ days: 30 });

  if (isLoading) return <p className="text-xs text-[var(--v2-muted)]">Loading…</p>;
  if (error) {
    const detail = error?.response?.data?.detail;
    return (
      <p className="text-xs text-[var(--v2-negative)]">
        {detail?.message || "Couldn't load the conversion report."}
      </p>
    );
  }

  const signedOut = data?.signed_out || {};
  const signup = data?.signup || {};
  const onboarding = data?.onboarding || {};
  const free = data?.free_usage || {};
  const upgrade = data?.upgrade || {};
  const activation = data?.activation || {};
  const timing = data?.timing || {};
  const actives = data?.active_members || {};
  const tracking = data?.tracking || {};
  const daily = data?.daily || [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2">
        {WINDOWS.map((w) => (
          <button
            key={w.key}
            type="button"
            onClick={() => setWindow(w.key)}
            className={`px-3 py-1.5 text-xs rounded border ${
              window === w.key
                ? "border-[var(--v2-accent)] text-[var(--v2-accent)]"
                : "border-[var(--v2-border)] text-[var(--v2-muted)]"
            }`}
          >
            {w.label}
          </button>
        ))}
      </div>

      {/* Whether the numbers below can be trusted at all. A funnel that
          reads zero because nothing is being recorded looks identical to
          a funnel that reads zero because nobody signed up. */}
      {tracking.status === "failing" || tracking.status === "degraded" ? (
        <div className="rounded border border-[var(--v2-negative)] p-3 text-xs text-[var(--v2-negative)]">
          <strong>Event recording is {tracking.status}.</strong>{" "}
          {tracking.failed} of {tracking.attempts} recent attempts failed
          {tracking.last_error_kind ? ` (${tracking.last_error_kind})` : ""}. The numbers below
          are incomplete.
        </div>
      ) : null}

      <SectionCard title="The funnel" subtitle={`${data?.window} · ${data?.from?.slice(0, 10)} to ${data?.to?.slice(0, 10)}`}>
        <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
          <FunnelStep label="Signup page views (events)" value={signedOut.signup_page_views} />
          <FunnelStep label="Signup starts (events)" value={signedOut.signup_starts}
                      of={signedOut.signup_page_views} ofLabel="views" />
          <FunnelStep label="Accounts created" value={signup.completions}
                      of={signedOut.signup_starts} ofLabel="starts" />
          <FunnelStep label="Activated" value={activation.activated}
                      of={activation.cohort_signups} ofLabel="signups" />
          <FunnelStep label="Saw an upgrade prompt" value={upgrade.prompt_views}
                      of={signup.completions} ofLabel="accounts" />
          <FunnelStep label="Opened pricing" value={upgrade.pricing_views}
                      of={upgrade.prompt_views} ofLabel="prompts" />
          <FunnelStep label="Started checkout" value={upgrade.checkout_starts}
                      of={upgrade.pricing_views} ofLabel="pricing views" />
          <FunnelStep label="Subscriptions confirmed by Stripe" value={upgrade.subscriptions_confirmed}
                      of={upgrade.checkout_starts} ofLabel="checkouts" />
        </ul>
        <p className="mt-3 text-[10px] leading-relaxed text-[var(--v2-muted)]">
          {signedOut.note} {upgrade.note}
        </p>
      </SectionCard>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <SectionCard title="Signup"><div className="flex flex-col divide-y divide-[var(--v2-border)]">
          <Stat label="Completion rate" value={pct(signup.completion_rate)} />
          <Stat label="Accounts created" value={num(signup.completions)} />
        </div></SectionCard>
        <SectionCard title="Onboarding"><div className="flex flex-col divide-y divide-[var(--v2-border)]">
          <Stat label="Completed" value={num(onboarding.completions)} />
          <Stat label="Skipped" value={num(onboarding.skips)} />
          <Stat label="Completion rate" value={pct(onboarding.completion_rate)} />
        </div></SectionCard>
        <SectionCard title="Free usage"><div className="flex flex-col divide-y divide-[var(--v2-border)]">
          <Stat label="Saw a free pick" value={num(free.opportunity_views)} />
          <Stat label="Hit a free limit" value={num(free.limit_hits)} />
        </div></SectionCard>
        <SectionCard title="Conversion"><div className="flex flex-col divide-y divide-[var(--v2-border)]">
          <Stat label="Signup → Elite" value={pct(upgrade.signup_to_elite_rate)} />
          <Stat label="Checkout completion" value={pct(upgrade.checkout_completion_rate)} />
          <Stat label="Cancelled at checkout" value={num(upgrade.checkout_cancellations)} />
          <Stat label="Returned from checkout" value={num(upgrade.returned_from_checkout)}
                note="Browser event — not revenue" />
        </div></SectionCard>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <SectionCard title="Activation" subtitle="At least one of: viewed a free pick, added a watchlist card, created an alert, logged a trade">
          <div className="flex flex-col divide-y divide-[var(--v2-border)]">
            <Stat label="Activation rate" value={pct(activation.activation_rate)}
                  note={`${num(activation.activated)} of ${num(activation.cohort_signups)} in this cohort`} />
            <Stat label="First watchlist card" value={num(activation.first_watchlist_card)} />
            <Stat label="First alert" value={num(activation.first_alert)} />
            <Stat label="First logged trade" value={num(activation.first_portfolio_trade)} />
          </div>
        </SectionCard>
        <SectionCard title="Time to value" subtitle="Median, from each member's own signup">
          <div className="flex flex-col divide-y divide-[var(--v2-border)]">
            <Stat label="To first useful action" value={hours(timing.median_hours_to_first_action)} />
            <Stat label="To starting checkout" value={hours(timing.median_hours_to_checkout)} />
          </div>
        </SectionCard>
        <SectionCard title="Active members" subtitle="Distinct accounts that did anything in the period">
          <div className="flex flex-col divide-y divide-[var(--v2-border)]">
            <Stat label="Free" value={num(actives.free)} />
            <Stat label="Elite" value={num(actives.elite)} />
          </div>
        </SectionCard>
      </div>

      <SectionCard title="Daily" subtitle="Signups, prompts, checkouts and confirmed subscriptions">
        {daily.length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">Nothing recorded in this period.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
            {daily.map((d) => (
              <li key={d.day} className="flex items-center justify-between py-1.5 text-xs">
                <span>{d.day}</span>
                <span className="tabular-nums text-[var(--v2-muted)]">
                  {d.signups} signups · {d.upgrade_prompts} prompts · {d.checkouts_started} checkouts ·{" "}
                  <strong className="text-[var(--v2-fg)]">{d.subscriptions_confirmed} confirmed</strong>
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {/* Cost sits beside conversion on purpose: the assistant is the
          most expensive thing an Elite member can do, and its cost is
          only meaningful next to how many Elite members there are. */}
      <SectionCard title="AI assistant cost" subtitle="Last 30 days — internal only, never shown to customers">
        {ai.isLoading ? <p className="text-xs text-[var(--v2-muted)]">Loading…</p>
          : ai.error ? <p className="text-xs text-[var(--v2-muted)]">
              {ai.error?.response?.data?.detail?.message || "AI usage reporting unavailable."}
            </p>
          : (
            <>
              <div className="grid grid-cols-2 gap-x-6 md:grid-cols-4">
                <Stat label="Messages" value={num(ai.data?.ok)} note={`${num(ai.data?.blocked)} refused`} />
                <Stat label="Estimated cost" value={`${ai.data?.cost_currency} ${ai.data?.estimated_cost ?? "—"}`} />
                <Stat label="Per active user" value={`${ai.data?.cost_currency} ${ai.data?.cost_per_active_user ?? "—"}`} />
                <Stat label="Heaviest account"
                      value={`${ai.data?.cost_currency} ${ai.data?.heaviest_user_cost ?? "—"}`}
                      note={`${num(ai.data?.heaviest_user_messages)} messages`} />
              </div>
              <p className="mt-3 text-[10px] leading-relaxed text-[var(--v2-muted)]">
                Limits: {ai.data?.limits?.per_day}/day, {ai.data?.limits?.per_month}/month,{" "}
                {ai.data?.limits?.max_history_messages} messages of history.{" "}
                {ai.data?.limits?.enabled ? "" : "Assistant is switched OFF. "}
                Pricing: {ai.data?.pricing?.source}.
              </p>
            </>
          )}
      </SectionCard>
    </div>
  );
}
