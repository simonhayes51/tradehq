// src/lib/analytics.js
//
// A small internal funnel-event interface.
//
// There is no analytics provider in this project, and adding one is a
// decision with privacy and contractual consequences that is not mine to
// make - so this deliberately ships no third party. It gives the funnel
// ONE named vocabulary and one place to wire a provider into later, which
// is the part that is genuinely hard to retrofit: without it, adding
// analytics later means hunting for the twenty places an event should
// have fired.
//
// What it does today: posts to our own backend if the endpoint exists,
// and no-ops silently otherwise. What it never does: send anything that
// could identify a person or reveal what they searched for. See
// `sanitise` below - the payload allow-list is enforced here rather than
// trusted to each call site.
import { api } from "../v2/lib/api";

export const EVENTS = {
  SIGNUP_VIEWED: "signup_viewed",
  SIGNUP_STARTED: "signup_started",
  SIGNUP_COMPLETED: "signup_completed",
  ONBOARDING_STARTED: "onboarding_started",
  ONBOARDING_COMPLETED: "onboarding_completed",
  ONBOARDING_SKIPPED: "onboarding_skipped",
  FREE_OPPORTUNITY_VIEWED: "free_opportunity_viewed",
  FREE_LIMIT_REACHED: "free_limit_reached",
  UPGRADE_PROMPT_VIEWED: "upgrade_prompt_viewed",
  PRICING_VIEWED: "pricing_viewed",
  CHECKOUT_STARTED: "checkout_started",
  CHECKOUT_COMPLETED: "checkout_completed",
  CHECKOUT_CANCELLED: "checkout_cancelled",
  FIRST_WATCHLIST_CARD_ADDED: "first_watchlist_card_added",
  FIRST_ALERT_CREATED: "first_alert_created",
  FIRST_PORTFOLIO_TRADE_ADDED: "first_portfolio_trade_added",
};

// Only these keys are ever transmitted, and only these value types. A
// call site that passes a player name, a search term, an email or a
// Discord id has that key dropped here rather than relied on to know
// better - the whole point of centralising this is that the privacy rule
// is enforced in one readable place.
const ALLOWED_PROPS = new Set([
  "tier",        // "free" | "elite" - never a user id
  "surface",     // which screen: "home", "opportunities", "trade_finder"
  "feature",     // which gated feature triggered a prompt
  "plan",        // "monthly" | "season"
  "step",        // onboarding step index
  "count",       // a small integer, e.g. how many picks were unlocked
  "reason",      // a short enum-ish string, never free text from a user
]);

function sanitise(props = {}) {
  const out = {};
  for (const [key, value] of Object.entries(props)) {
    if (!ALLOWED_PROPS.has(key)) continue;
    if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
    // Bounded, so a value that is unexpectedly a long string can't smuggle
    // content through a key that is allowed.
    else if (typeof value === "string") out[key] = value.slice(0, 40);
    else if (typeof value === "boolean") out[key] = value;
  }
  return out;
}

let warned = false;

export function track(event, props = {}) {
  if (!event) return;
  const payload = { event, props: sanitise(props) };
  try {
    // Fire and forget. An analytics failure must never be visible to a
    // user or block the action that triggered it.
    api.post("/api/events/track", payload, {
      __skipAuthRedirect: true,
      __noRetry: true,
      timeout: 4000,
    }).catch(() => {
      if (!warned && import.meta.env?.DEV) {
        warned = true;
        // Dev-only, once: the endpoint is optional by design.
        console.info("[analytics] no /api/events/track endpoint; events are being discarded");
      }
    });
  } catch {
    /* never throw from instrumentation */
  }
}

// Fires once per browser session per event+key. Funnel events like
// "signup page viewed" are meaningless if a re-render or a back button
// double-counts them.
const seen = new Set();

export function trackOnce(event, props = {}, key = "") {
  const dedupeKey = `${event}:${key}`;
  if (seen.has(dedupeKey)) return;
  seen.add(dedupeKey);
  track(event, props);
}
