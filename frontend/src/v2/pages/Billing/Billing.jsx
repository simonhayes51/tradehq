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
import { Crown, Check, X, CreditCard, Shield, Clock, AlertTriangle, MessageCircle, ExternalLink, Gift } from "lucide-react";
import SectionCard from "../../components/SectionCard";
import "../../styles/billing.css";

const API_BASE = import.meta.env.VITE_API_URL || "";

const PLANS = {
  monthly: { price: "£3", period: "/month", total: "£3.00 per month", savings: null, priceId: "price_monthly_premium", trialDays: 7 },
  yearly: { price: "£25", period: "/season", total: "£25.00 for the season (12 months)", savings: "Save £11 vs monthly", priceId: "price_season_premium", trialDays: 7 },
};

// The real, currently-shipped v2 feature set - not the v1 list's
// "Smart Buy AI"/"Sharpe ratio and max drawdown"/generic AI language,
// none of which describes an actual v2 component.
const PREMIUM_FEATURES = [
  { icon: Crown, title: "Full Opportunities feed", desc: "Every live BUY, not just a limited preview." },
  { icon: Check, title: "Full Trade Finder", desc: "Search and filter the entire eligible opportunity universe." },
  { icon: Check, title: "Unlimited Watchlist", desc: "Track as many cards as you want." },
  { icon: Check, title: "Price alerts", desc: "Get notified when a watched card moves." },
  { icon: Check, title: "Full trade tracking & history", desc: "Log trades and see your real performance over time." },
  { icon: MessageCircle, title: "Premium Discord access", desc: "The premium channel in the EAFC Live Discord." },
];

const UNLOCK_COPY = {
  opportunities: "Unlock the full live Opportunities feed.",
  "trade finder": "Unlock the full Trade Finder.",
  "trade-finder": "Unlock the full Trade Finder.",
  watchlist: "Unlock unlimited Watchlist tracking.",
  alerts: "Unlock price alerts.",
};

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

  useEffect(() => {
    loadUserData();
    loadSubscriptionStatus();
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get("payment") === "success") {
      setSuccess("Payment successful — your Premium features are active.");
      setTimeout(loadUserData, 1000);
    } else if (urlParams.get("payment") === "cancelled") {
      setError("Payment was cancelled. You can try again any time.");
    }
  }, []);

  async function loadUserData() {
    try {
      const res = await fetch(`${API_BASE}/api/me`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          setUser(data);
          setIsPremium(!!data.is_premium);
          return;
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
    } catch {
      setUser(false);
    }
  }

  async function loadSubscriptionStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/billing/subscription`, { credentials: "include" });
      if (res.ok) setCurrentSubscription((await res.json()).subscription);
    } catch { /* best-effort */ }
  }

  async function createCheckoutSession() {
    setLoading(true); setError(""); setSuccess("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/create-checkout-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          priceId: PLANS[billingCycle].priceId,
          billingCycle,
          paymentMethod: "stripe",
          trialDays: PLANS[billingCycle].trialDays,
          successUrl: `${window.location.origin}/#/v2/billing?payment=success`,
          cancelUrl: `${window.location.origin}/#/v2/billing?payment=cancelled`,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || "Failed to start checkout");
      if (data.checkoutUrl) window.location.href = data.checkoutUrl;
      else throw new Error("Invalid checkout response");
    } catch (e) {
      setError(e.message || "Failed to start checkout");
    } finally {
      setLoading(false);
    }
  }

  async function cancelSubscription() {
    if (!confirm("Cancel your Premium subscription? You'll keep Premium until the end of your current billing period, then it won't renew.")) return;
    setLoading(true); setError(""); setSuccess("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/cancel-subscription`, { method: "POST", credentials: "include" });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || d.error || "Failed to cancel"); }
      await loadSubscriptionStatus();
      await loadUserData();
      setSuccess("Cancelled — Premium stays active until the end of your current billing period.");
    } catch (e) {
      setError(e.message || "Failed to cancel subscription");
    } finally {
      setLoading(false);
    }
  }

  async function updatePaymentMethod() {
    setLoading(true); setError("");
    try {
      const res = await fetch(`${API_BASE}/api/billing/update-payment-method`, { method: "POST", credentials: "include" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || "Failed to open payment portal");
      if (data.portalUrl) window.location.href = data.portalUrl;
    } catch (e) {
      setError(e.message || "Failed to open payment portal");
    } finally {
      setLoading(false);
    }
  }

  const contextSentence =
    (unlockContext && UNLOCK_COPY[unlockContext.toLowerCase()]) ||
    (unlockContext ? `Unlock ${unlockContext}.` : "Get the full EAFC Live trading toolkit.");

  if (user === null) {
    return (
      <main className="v2-destination">
        <div className="v2-page-state" role="status">Loading billing information…</div>
      </main>
    );
  }

  if (user === false) {
    const returnTo = `/v2/billing${window.location.hash.includes("?") ? window.location.hash.slice(window.location.hash.indexOf("?")) : ""}`;
    return (
      <main className="v2-destination billing-v2">
        <div className="billing-v2-inner">
          <header className="billing-v2-head">
            {unlockContext ? <span className="billing-v2-context">{contextSentence}</span> : null}
            <h1>Sign in to manage billing</h1>
            <p>Create a free account or log in to see plans and upgrade to Premium.</p>
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
              <h1>You're on Premium</h1>
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

            <SectionCard title="Your Premium features">
              <div className="billing-v2-feature-grid">
                {PREMIUM_FEATURES.map((f) => (
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
                <strong>Premium Discord access is live on your account</strong>
                <p>Join the server to reach the premium channel.</p>
              </div>
              <a href="https://discord.gg/3sSpu3rDgn" target="_blank" rel="noreferrer" className="billing-v2-btn secondary">
                Open Discord <ExternalLink size={13} />
              </a>
            </div>
          </>
        ) : (
          <>
            <header className="billing-v2-head">
              {unlockContext ? <span className="billing-v2-context">{contextSentence}</span> : null}
              <h1>Upgrade to EAFC Live Premium</h1>
              <p>{unlockContext ? contextSentence : "Get the full EAFC Live trading toolkit."}</p>
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
                <span className="amount">{PLANS[billingCycle].price}</span>
                <span className="period">{PLANS[billingCycle].period}</span>
              </div>
              <p className="billing-v2-price-total">{PLANS[billingCycle].total}</p>
              {PLANS[billingCycle].savings ? <span className="billing-v2-save-pill standalone">{PLANS[billingCycle].savings}</span> : null}

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
                {PREMIUM_FEATURES.map((f) => (
                  <div key={f.title} className="billing-v2-feature">
                    <f.icon size={16} />
                    <div><strong>{f.title}</strong><p>{f.desc}</p></div>
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard title="Free vs Premium">
              <div className="billing-v2-compare">
                <div>
                  <h4>Free</h4>
                  <ul>
                    <li><Check size={14} /> Player search &amp; current prices</li>
                    <li><Check size={14} /> Fair value on individual cards</li>
                    <li><Check size={14} /> Opportunity preview</li>
                    <li><Check size={14} /> Watchlist</li>
                    <li><Check size={14} /> Basic trade tracking</li>
                    <li className="off"><X size={14} /> Full Opportunities feed</li>
                    <li className="off"><X size={14} /> Price alerts</li>
                  </ul>
                </div>
                <div className="featured">
                  <h4><Crown size={14} /> Premium</h4>
                  <ul>
                    <li><Check size={14} /> Everything in Free, plus:</li>
                    <li><Check size={14} /> Full Opportunities feed</li>
                    <li><Check size={14} /> Full Trade Finder</li>
                    <li><Check size={14} /> Unlimited Watchlist</li>
                    <li><Check size={14} /> Price alerts</li>
                    <li><Check size={14} /> Full trade history &amp; performance</li>
                    <li><Check size={14} /> Premium Discord access</li>
                  </ul>
                </div>
              </div>
            </SectionCard>

            <p className="billing-v2-cancel-note">
              <Clock size={13} /> Cancel any time. Premium stays active until the end of your paid period —
              cancelling during the trial stops it from ever charging you.
            </p>
          </>
        )}

        <p className="billing-v2-legal">
          By subscribing, you agree to our{" "}
          <Link to="/terms">Terms of Service</Link> and{" "}
          <Link to="/privacy">Privacy Policy</Link>.
        </p>
      </div>
    </main>
  );
}
