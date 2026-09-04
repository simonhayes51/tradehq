// src/v2/pages/Billing/Billing.jsx
//
// A true V2 page - same dark/green design language, SectionCard, Nav
// shell as every other v2 route - replacing the old v1 Billing.jsx
// (purple gradients, "Smart Buy AI"/"Sharpe ratio"/"Advanced Analytics"
// feature copy that doesn't match what v2 actually ships) as the one
// Billing users are ever sent to. The payment backend itself
// (create-checkout-session/cancel-subscription/update-payment-method,
// all real Stripe calls) is reused byte-for-byte from v1's Billing.jsx -
// only the presentation layer changes, per "do not rewrite Stripe logic
// unnecessarily."
import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { Crown, Check, CreditCard, Shield, Clock, AlertTriangle, MessageCircle, ExternalLink, Gift, Hash, Loader2, Settings } from "lucide-react";
import SectionCard from "../../components/SectionCard";
import PlanComparison from "../../components/PlanComparison";
import api from "../../../axios";
import { DISCORD_INVITE_URL } from "../../../lib/domains";
import { EVENTS, track, trackOnce } from "../../../lib/analytics";
import { FREE_LIMITS } from "../../lib/limits";
import { headlinePrice, useBillingPlans } from "../../hooks/useBillingPlans";
import { useEntitlements } from "../../../context/EntitlementsContext";
import "../../styles/billing.css";

const API_BASE = import.meta.env.VITE_API_URL || "";

// Only the identity of each plan lives here. The PRICE comes from
// GET /api/billing/plans, which reads Stripe.
//
// This file used to hardcode "£3/month" and "£25/season" while the
// backend's own subscription response hardcoded "£9.99/month" and Stripe
// charged whatever the configured Price object cost - so a customer could
// be quoted one number at checkout and shown another on their account
// page. There is now one source and it is the one that takes the money.
const PLAN_KEYS = {
  monthly: { key: "monthly", priceId: "price_monthly_premium", label: "Monthly" },
  yearly: { key: "season", priceId: "price_season_premium", label: "Full season" },
};

// The real, currently-shipped v2 feature set - not the v1 list's
// "Smart Buy AI"/"Sharpe ratio and max drawdown"/generic AI language,
// none of which describes an actual v2 component.
// Concrete differences only. "Unlimited Watchlist" was not true - the
// allowance is 500 - and a promise the product does not keep is worse
// than a smaller promise it does.
const ELITE_FEATURES = [
  { icon: Crown, title: "The full opportunities feed", desc: `Every buy signal all day, with exact entry and target prices — not ${FREE_LIMITS.dailyOpportunities} a day.` },
  { icon: Check, title: "Trade Finder", desc: "Search every live-priced card by budget, return, risk and position." },
  { icon: Check, title: `${FREE_LIMITS.eliteWatchlist}-card watchlist`, desc: `Up to ${FREE_LIMITS.eliteWatchlist} cards, against ${FREE_LIMITS.watchlist} on Free.` },
  { icon: Check, title: `${FREE_LIMITS.eliteAlerts} price alerts`, desc: `Up to ${FREE_LIMITS.eliteAlerts} active alerts, against ${FREE_LIMITS.alerts} on Free.` },
  { icon: Check, title: "Full portfolio and history", desc: "Unlimited open trades and your complete history, not the last 30 days." },
  { icon: Check, title: "Complete track record", desc: "Every published call and every window, not just the recent summary." },
  { icon: MessageCircle, title: "Members-only Discord", desc: "The Elite channel in the EAFC.Live Discord." },
];

// ?unlock=<feature> says which limit sent them here, so the page can open
// with the thing they were actually trying to do.
//
// Keyed by the FEATURE keys UpgradePrompt sends (which are the backend's
// own FEATURE_MIN_TIER keys), not by page names - the old map was keyed
// "opportunities"/"trade finder"/"alerts", none of which any link in the
// product produced, so the context sentence never once matched. The
// legacy spellings are kept as aliases for links already in the wild.
//
// "Unlock unlimited Watchlist tracking" is also gone: Elite is 500 cards,
// and 500 is not unlimited.
const UNLOCK_COPY = {
  opportunity_feed: "Every opportunity the engine finds, with exact buy and sell prices.",
  trade_finder: "Search every live-priced card by budget, risk, position and return.",
  ai_chat: "Ask about any card or budget and get an answer built from live market data.",
  full_track_record: "Every published call, every time window, and the full win/loss ledger.",
  realtime_alerts: `Up to ${FREE_LIMITS.eliteAlerts} active price alerts instead of ${FREE_LIMITS.alerts}.`,
  watchlist: `Track up to ${FREE_LIMITS.eliteWatchlist} cards instead of ${FREE_LIMITS.watchlist}.`,
  trade_journal: "Unlimited open trades and your complete history, not the last 30 days.",
  // Aliases for older links.
  opportunities: "Every opportunity the engine finds, with exact buy and sell prices.",
  "trade finder": "Search every live-priced card by budget, risk, position and return.",
  "trade-finder": "Search every live-priced card by budget, risk, position and return.",
  alerts: `Up to ${FREE_LIMITS.eliteAlerts} active price alerts instead of ${FREE_LIMITS.alerts}.`,
};

// Discord Server Feed plan copy. Display only, same rule as FREE_LIMITS
// above - the price and daily-post allowance that actually apply come
// from Stripe and discord_server_settings server-side (see
// app/services/discord_feed_billing.py's PLANS dict, which these numbers
// mirror). This is a SEPARATE, guild-scoped product from personal
// Free/Elite: a member can hold both at once, and neither includes the
// other - Elite does not add a server feed, and a paid server feed does
// not grant the member's own account Elite.
const SERVER_PLANS = [
  { key: "server_feed", label: "Server Feed", price: "£9.99/month", postsPerDay: 6 },
  { key: "server_feed_plus", label: "Server Feed+", price: "£19.99/month", postsPerDay: 12 },
];

const SERVER_STATUS_LABEL = {
  active: "Active",
  trialing: "Trial",
  past_due: "Payment failed",
  checkout_pending: "Checkout not finished",
  unpaid: "Not paid",
  canceled: "Cancelled",
};

// The user's connected Discord servers with a Server Feed - separate
// from the personal Free/Elite plan above, because a server subscription
// belongs to the GUILD (billed via Stripe on the guild's own
// stripe_subscription_id, see discord_servers), not to this account. A
// member can manage feeds for zero, one or several servers here,
// regardless of their own personal plan.
function DiscordServerPlansSection() {
  const [guilds, setGuilds] = useState(null); // null = loading, [] = none, Error = failed
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.get("/api/v2/discord-feed/guilds")
      .then(({ data }) => { if (!cancelled) setGuilds(data?.guilds || []); })
      .catch(() => { if (!cancelled) { setGuilds([]); setLoadFailed(true); } });
    return () => { cancelled = true; };
  }, []);

  return (
    <SectionCard
      title="Discord server plans"
      subtitle="A separate product, billed per Discord server — not part of your personal plan above."
      className="billing-v2-discord-section"
    >
      <div className="billing-v2-discord-section-tag"><Hash size={12} /> SERVER PRODUCT</div>
      <div className="billing-v2-server-plans">
        {SERVER_PLANS.map((plan) => (
          <div key={plan.key} className="billing-v2-server-plan">
            <strong>{plan.label}</strong>
            <span className="billing-v2-server-plan-price">{plan.price}</span>
            <span className="billing-v2-server-plan-limit">Up to {plan.postsPerDay} feed posts a day</span>
          </div>
        ))}
      </div>

      {guilds === null ? (
        <div className="v2-page-state" role="status"><Loader2 className="spin" size={15} /> Loading your Discord servers…</div>
      ) : loadFailed ? (
        <div className="billing-v2-msg err"><AlertTriangle size={15} /> Couldn't load your connected Discord servers. Try refreshing this page.</div>
      ) : guilds.length === 0 ? (
        <div className="billing-v2-server-empty">
          <p>No Discord server is set up with a feed under your account yet.</p>
          <p>Invite the EAFC.Live bot to your server, then run <code>/feed setup</code> there (you'll need Manage Server) to pick a plan and posting channel.</p>
          <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer" className="billing-v2-btn secondary">
            Join our Discord for setup help <ExternalLink size={13} />
          </a>
        </div>
      ) : (
        <ul className="billing-v2-server-list">
          {guilds.map((g) => (
            <li key={g.guild_id}>
              <div className="billing-v2-server-list-name">
                <Hash size={14} />
                <div>
                  <strong>{g.name}</strong>
                  <span>{g.plan === "server_feed_plus" ? "Server Feed+" : g.plan === "server_feed" ? "Server Feed" : "No plan selected"}</span>
                </div>
              </div>
              <span className={`billing-v2-server-status status-${g.subscription_status || "unknown"}`}>
                {SERVER_STATUS_LABEL[g.subscription_status] || g.subscription_status || "Unknown"}
              </span>
              <Link to="/discord-feed" className="billing-v2-btn ghost">
                <Settings size={14} /> Manage
              </Link>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

// What a customer is told when a billing call fails.
//
// The three handlers below used to render `data.detail` straight onto the
// page. FastAPI's detail is not customer copy: it is a string on some
// routes and an object on others (every 402 in this product returns a
// dict), so a failed call could print "[object Object]" above a payment
// button. A non-JSON body - a proxy's 502 HTML page - was worse still,
// because res.json() threw and the customer read "Unexpected token <".
//
// Nothing about money should ever be ambiguous, so every message below
// also says whether they were charged.
const BILLING_ERRORS = {
  401: "Your session has expired. Sign in again and you can pick up where you left off — nothing has been charged.",
  402: "Your card was declined. Nothing has been charged. Try a different payment method.",
  403: "That action isn't available on your account. Nothing has been charged.",
  404: "We couldn't find that subscription. Nothing has been charged — please refresh and try again.",
  409: "There's already a checkout in progress for this account. Finish or close it, then try again.",
  429: "Too many attempts in a row. Wait a minute and try again — nothing has been charged.",
};

async function billingError(response, fallback) {
  if (!response) {
    return "We couldn't reach the payment service. Check your connection and try again — nothing has been charged.";
  }
  let detail;
  try {
    const data = await response.json();
    // Only a plain, short string from the server is worth showing; an
    // object, or a stack-trace-length body, is for the logs.
    const candidate = typeof data?.detail === "string" ? data.detail : typeof data?.error === "string" ? data.error : null;
    if (candidate && candidate.length <= 160) detail = candidate;
  } catch {
    // Body wasn't JSON. Fall through to the status-based message.
  }
  if (response.status >= 500) {
    return "The payment service isn't responding. Nothing has been charged — please try again shortly.";
  }
  return BILLING_ERRORS[response.status] || detail || fallback;
}

export default function Billing() {
  const [searchParams] = useSearchParams();
  const unlockContext = searchParams.get("unlock");
  const [user, setUser] = useState(null);
  const [isPremium, setIsPremium] = useState(false);
  const [billingCycle, setBillingCycle] = useState("yearly");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [currentSubscription, setCurrentSubscription] = useState(null);
  const { data: plans } = useBillingPlans();
  const { refreshEntitlements } = useEntitlements();
  const activePlan = plans?.plans?.[PLAN_KEYS[billingCycle].key];
  const priceDisplay = headlinePrice(plans, PLAN_KEYS[billingCycle].key);

  useEffect(() => {
    loadUserData();
    loadSubscriptionStatus();
    // Stripe's redirect carries the outcome in the URL, but the URL is
    // NOT what grants access - only the server's own subscription state
    // does. `payment=success` is treated purely as a hint that a webhook
    // is probably on its way, and we poll our own backend until it lands.
    const urlParams = new URLSearchParams(window.location.hash.split("?")[1] || window.location.search);
    if (urlParams.get("payment") === "success") {
      track(EVENTS.CHECKOUT_COMPLETED, { plan: PLAN_KEYS[billingCycle].key });
      setSuccess("Payment received. Confirming it with Stripe…");
      confirmUpgrade();
    } else if (urlParams.get("payment") === "cancelled") {
      track(EVENTS.CHECKOUT_CANCELLED, { plan: PLAN_KEYS[billingCycle].key });
      setError("Checkout was cancelled and you haven't been charged. You can start again whenever you like.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Wait for the webhook, then tell the truth either way.
   *
   * Webhook delivery is usually a second or two but is not guaranteed to
   * beat the redirect, so a single re-check would leave a paying customer
   * looking at a Free account and reasonably concluding their money had
   * vanished. This polls our own state - never the return URL - and if it
   * still has not landed, says so and explains that the payment is safe,
   * rather than silently showing the wrong plan.
   */
  async function confirmUpgrade() {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      // eslint-disable-next-line no-await-in-loop
      const upgraded = await loadUserData();
      if (upgraded) {
        await loadSubscriptionStatus();
        // The entitlement context caches for 5 minutes; without this the
        // rest of the app would still treat them as Free until it next
        // refreshed, which is exactly the "I paid and nothing changed"
        // experience this whole function exists to prevent.
        await refreshEntitlements({ force: true });
        setSuccess("You're on Elite. Everything is unlocked.");
        return;
      }
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    setSuccess("");
    setError(
      "Your payment went through, but Stripe hasn't confirmed it with us yet. This usually " +
      "resolves within a minute — refresh this page. Nothing has gone wrong with your payment, " +
      "and you won't be charged twice.",
    );
  }

  async function loadUserData() {
    try {
      const res = await fetch(`${API_BASE}/api/me`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          setUser(data);
          setIsPremium(!!data.is_premium);
          return !!data.is_premium;
        }
      }
      // Unlike v1's Billing (which only ever rendered inside PrivateRoute,
      // so a signed-out visit was structurally impossible), this page has
      // no such gate - v2 handles gating per-panel, and Billing is one of
      // the places a signed-out user can genuinely land (a shared
      // /v2/billing?unlock= link, or the CTA cards below before signup).
      // `false` here is a real, resolved "not logged in" state, distinct
      // from `null`'s "haven't checked yet" - without it this page would
      // spin on "Loading billing information…" forever for anyone not
      // already signed in.
      setUser(false);
      return false;
    } catch {
      setUser(false);
      return false;
    }
  }

  async function loadSubscriptionStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/billing/subscription`, { credentials: "include" });
      if (res.ok) setCurrentSubscription((await res.json()).subscription);
    } catch { /* best-effort */ }
  }

  async function createCheckoutSession() {
    if (loading) return;   // duplicate-submission guard: a second click
                           // would open a second Stripe session.
    setLoading(true); setError(""); setSuccess("");
    track(EVENTS.CHECKOUT_STARTED, { plan: PLAN_KEYS[billingCycle].key });
    try {
      const res = await fetch(`${API_BASE}/api/billing/create-checkout-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          priceId: PLAN_KEYS[billingCycle].priceId,
          billingCycle,
          paymentMethod: "stripe",
        }),
      });
      if (!res.ok) {
        setError(await billingError(res, "We couldn't start checkout. Nothing has been charged — please try again."));
        return;
      }
      const data = await res.json();
      if (data.checkoutUrl) window.location.href = data.checkoutUrl;
      else setError("Checkout didn't open. Nothing has been charged — please try again.");
    } catch {
      setError(await billingError(null));
    } finally {
      setLoading(false);
    }
  }

  async function cancelSubscription() {
    if (!confirm("Cancel your Elite subscription? You keep Elite until the end of the period you've already paid for, then it stops renewing.")) return;
    setLoading(true); setError(""); setSuccess("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/cancel-subscription`, { method: "POST", credentials: "include" });
      if (!res.ok) {
        setError(await billingError(res, "We couldn't cancel your subscription. Nothing has changed — please try again or contact us."));
        return;
      }
      await loadSubscriptionStatus();
      await loadUserData();
      setSuccess("Cancelled. Elite stays active until the end of the period you've already paid for.");
    } catch {
      setError(await billingError(null));
    } finally {
      setLoading(false);
    }
  }

  async function updatePaymentMethod() {
    setLoading(true); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/update-payment-method`, { method: "POST", credentials: "include" });
      if (!res.ok) {
        setError(await billingError(res, "We couldn't open the payment portal. Nothing has been charged — please try again."));
        return;
      }
      const data = await res.json();
      if (data.portalUrl) window.location.href = data.portalUrl;
      else setError("The payment portal didn't open. Nothing has been charged — please try again.");
    } catch {
      setError(await billingError(null));
    } finally {
      setLoading(false);
    }
  }

  // The middle of the funnel: prompt seen -> pricing page reached ->
  // checkout started -> checkout completed. Without this step, a drop
  // between the prompt and checkout is invisible. `feature` is the key
  // that sent them, never anything about who they are.
  useEffect(() => {
    trackOnce(
      EVENTS.PRICING_VIEWED,
      unlockContext ? { feature: unlockContext } : { surface: "direct" },
      unlockContext || "direct",
    );
  }, [unlockContext]);

  const contextSentence =
    (unlockContext && UNLOCK_COPY[unlockContext.toLowerCase()]) ||
    "Get the full EAFC.Live trading toolkit.";

  if (user === null) {
    return (
      <main className="v2-destination">
        <div className="v2-page-state" role="status">Loading billing information…</div>
      </main>
    );
  }

  if (user === false) {
    const returnTo = `/billing${window.location.hash.includes("?") ? window.location.hash.slice(window.location.hash.indexOf("?")) : ""}`;
    return (
      <main className="v2-destination billing-v2">
        <div className="billing-v2-inner">
          <header className="billing-v2-head">
            {unlockContext ? <span className="billing-v2-context">{contextSentence}</span> : null}
            <h1>Sign in to manage billing</h1>
            <p>Create a free account or sign in to see plans and upgrade to Elite.</p>
          </header>
          <div className="billing-v2-actions" style={{ justifyContent: "center" }}>
            <Link to={`/signup?from=${encodeURIComponent(returnTo)}`} className="billing-v2-btn primary">Create free account</Link>
            <Link to={`/login?from=${encodeURIComponent(returnTo)}`} className="billing-v2-btn secondary">Log in</Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="v2-destination billing-v2">
      <div className="billing-v2-inner">
        {isPremium && currentSubscription ? (
          <>
            <header className="billing-v2-head">
              <span className="billing-v2-badge"><Crown size={14} /> PREMIUM ACTIVE</span>
              <h1>You're on Elite</h1>
              <p>Every feature below is unlocked on your account right now.</p>
            </header>

            {success ? <div className="billing-v2-msg ok">{success}</div> : null}
            {error ? <div className="billing-v2-msg err"><AlertTriangle size={15} /> {error}</div> : null}

            <SectionCard title="Current plan" className="billing-v2-plan">
              <div className="billing-v2-plan-row">
                <div>
                  <strong>{currentSubscription.plan_name}</strong>
                  <p>Next billing: {currentSubscription.next_billing_date ? new Date(currentSubscription.next_billing_date).toLocaleDateString("en-GB") : "—"}</p>
                  <p>{currentSubscription.amount_display}</p>
                </div>
              </div>
              <div className="billing-v2-actions">
                <button onClick={updatePaymentMethod} disabled={loading} className="billing-v2-btn secondary">
                  <CreditCard size={15} /> {loading ? "Working…" : "Update payment method"}
                </button>
                <button onClick={cancelSubscription} disabled={loading} className="billing-v2-btn ghost">
                  {loading ? "Working…" : "Cancel subscription"}
                </button>
              </div>
            </SectionCard>

            <SectionCard title="Your Elite features">
              <div className="billing-v2-feature-grid">
                {ELITE_FEATURES.map((f) => (
                  <div key={f.title} className="billing-v2-feature">
                    <f.icon size={16} />
                    <div><strong>{f.title}</strong><p>{f.desc}</p></div>
                  </div>
                ))}
              </div>
            </SectionCard>

            <div className="billing-v2-discord">
              <MessageCircle size={18} />
              <div>
                <strong>Your Elite Discord access is live</strong>
                <p>Join the server to reach the premium channel.</p>
              </div>
              <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer" className="billing-v2-btn secondary">
                Open Discord <ExternalLink size={13} />
              </a>
            </div>
          </>
        ) : (
          <>
            <header className="billing-v2-head">
              {unlockContext ? <span className="billing-v2-context">{contextSentence}</span> : null}
              <h1>Upgrade to EAFC.Live Elite</h1>
              <p>{unlockContext ? contextSentence : "Get the full EAFC.Live trading toolkit."}</p>
            </header>

            {success ? <div className="billing-v2-msg ok">{success}</div> : null}
            {error ? <div className="billing-v2-msg err"><AlertTriangle size={15} /> {error}</div> : null}

            <div className="billing-v2-toggle" role="tablist" aria-label="Billing cycle">
              <button role="tab" aria-selected={billingCycle === "monthly"} className={billingCycle === "monthly" ? "active" : ""} onClick={() => setBillingCycle("monthly")}>Monthly</button>
              <button role="tab" aria-selected={billingCycle === "yearly"} className={billingCycle === "yearly" ? "active" : ""} onClick={() => setBillingCycle("yearly")}>
                Season <span className="billing-v2-save-pill">30% off</span>
              </button>
            </div>

            <SectionCard className="billing-v2-price-card">
              <div className="billing-v2-price">
                {/* Straight from Stripe. When it is unreachable we say so
                    rather than showing a number that might not be what the
                    customer is about to be charged. */}
                <span className="amount">{priceDisplay || "Pricing unavailable"}</span>
              </div>
              {activePlan && activePlan.configured === false ? (
                <p className="billing-v2-price-total">
                  We couldn&apos;t load live pricing just now. Try again in a moment — nothing is
                  charged until you confirm on Stripe&apos;s own checkout page.
                </p>
              ) : null}

              <div className="billing-v2-trial">
                <Gift size={15} /> <span>7-day free trial included — cancel any time during the trial and you won't be charged.</span>
              </div>

              <button onClick={createCheckoutSession} disabled={loading} className="billing-v2-btn primary full">
                {loading ? "Starting checkout…" : <><Gift size={16} /> Start 7-day free trial</>}
              </button>
              <div className="billing-v2-secure"><Shield size={13} /> Secure payment via Stripe · Cancel any time</div>
            </SectionCard>

            <SectionCard title="What's included">
              <div className="billing-v2-feature-grid">
                {ELITE_FEATURES.map((f) => (
                  <div key={f.title} className="billing-v2-feature">
                    <f.icon size={16} />
                    <div><strong>{f.title}</strong><p>{f.desc}</p></div>
                  </div>
                ))}
              </div>
            </SectionCard>

            {/* The shared comparison, so the pricing page, the signup
                page and every locked state describe the same product.
                The hand-written list this replaces claimed "Unlimited
                Watchlist", which was never true - the allowance is 500 -
                and omitted the alert, portfolio and track-record limits
                that a customer actually runs into. */}
            <SectionCard title="Free vs Elite">
              <PlanComparison elitePrice={priceDisplay} />
            </SectionCard>

            <p className="billing-v2-cancel-note">
              <Clock size={13} /> Cancel any time from this page. Elite stays active until the end of
              the period you&apos;ve already paid for.{plans?.trial_days ? ` Cancelling during the ${plans.trial_days}-day trial means you're never charged.` : ""}
            </p>
          </>
        )}

        <DiscordServerPlansSection />

        <p className="billing-v2-legal">
          By subscribing, you agree to our{" "}
          <Link to="/terms">Terms of Service</Link> and{" "}
          <Link to="/privacy">Privacy Policy</Link>.
        </p>
      </div>
    </main>
  );
}
