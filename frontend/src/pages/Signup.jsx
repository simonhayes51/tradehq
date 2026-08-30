// src/pages/Signup.jsx
//
// The free-account signup route. Every locked state, upgrade prompt and
// public CTA in the product points here.
//
// The previous version was a heading, three vague bullets ("Unlock more
// EAFC Live features as you go") and a Discord button. It never said the
// account was free, never said no card was needed, never said what you
// actually get, and never showed what Elite adds - so the one decision
// the page exists to support was the one thing it left out.
//
// Discord OAuth is the only method offered, because it is the only one
// this product actually has wired end to end. An email/password route
// exists in the backend (app/routers/auth_email.py) but has no email
// verification and no password reset, so putting it in front of customers
// would be shipping an unfinished auth system - see the report.
import { useEffect, useState } from "react";
import { Navigate, useSearchParams, Link } from "react-router-dom";
import { Check, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { EVENTS, track, trackOnce } from "../lib/analytics";
import { FREE_LIMITS } from "../v2/lib/limits";
import { useBillingPlans, headlinePrice } from "../v2/hooks/useBillingPlans";
import PlanComparison from "../v2/components/PlanComparison";
import "../v2/styles/tokens.css";
import "./signup.css";

// Concrete, and all of it true on the free plan. Nothing here is a
// teaser for something that turns out to be locked.
const FREE_INCLUDES = [
  `${FREE_LIMITS.dailyOpportunities} complete trade ideas every day — entry price, sell target and expected profit`,
  "The exact live price on any card, and what it's actually worth",
  "Recent completed sales and the full price history",
  `Track up to ${FREE_LIMITS.watchlist} cards and set ${FREE_LIMITS.alerts} price alerts`,
  "Log your trades and see your real profit after EA's 5% tax",
];

// The codes main.py's _oauth_failure_redirect can send, and what each
// one means to the person reading it. Nothing from Discord's own
// error_description is ever rendered - it arrives on a URL anyone can
// construct, and this page would be reflecting it.
const OAUTH_ERRORS = {
  access_denied:
    "You cancelled the Discord sign-in. Nothing was created — try again when you're ready.",
  expired_state:
    "That sign-in link had expired. This happens if the page sat open for a while — start again and it'll work.",
  exchange_failed:
    "Discord didn't finish signing you in. This is usually temporary — please try again.",
  server_error:
    "Something went wrong on our side while signing you in. Please try again.",
};

export default function Signup() {
  const { isAuthenticated, login, checkAuthStatus } = useAuth();
  const [searchParams] = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const from = searchParams.get("from");
  const oauthError = searchParams.get("error");
  const { data: plans } = useBillingPlans();
  const elitePrice = headlinePrice(plans, "monthly");

  useEffect(() => {
    trackOnce(EVENTS.SIGNUP_VIEWED, { surface: from ? "gated" : "direct" }, from || "direct");
  }, [from]);

  useEffect(() => {
    if (searchParams.get("authenticated") === "true") {
      track(EVENTS.SIGNUP_COMPLETED);
      checkAuthStatus();
    }
  }, [searchParams, checkAuthStatus]);

  // The OAuth failure path lands back here with ?error=... rather than on
  // a dead end. Previously a failed Discord callback returned the user to
  // a page that looked exactly like a fresh one, so the only signal that
  // anything had gone wrong was that they were still signed out.
  useEffect(() => {
    if (oauthError) setError(OAUTH_ERRORS[oauthError] || OAUTH_ERRORS.server_error);
  }, [oauthError]);

  if (isAuthenticated) {
    // New accounts go through onboarding; a returning user who lands here
    // goes wherever they were headed. ONBOARDING decides which - see
    // v2/pages/Onboarding.
    return <Navigate to={from || "/welcome"} replace />;
  }

  function start() {
    if (submitting) return;      // duplicate-submission guard
    setSubmitting(true);
    setError("");
    track(EVENTS.SIGNUP_STARTED);
    try {
      login(from);
    } catch {
      setSubmitting(false);
      setError("Couldn't open Discord sign-in. Check your connection and try again.");
    }
  }

  return (
    <main className="signup-page">
      <div className="signup-card">
        <Link to="/" className="signup-brand">EAFC <b>Live</b></Link>

        <h1>Create your free account</h1>
        <p className="signup-lede">
          EAFC Live watches the EA FC transfer market and tells you which cards are worth
          buying right now, at what price, and what you should make on them.
        </p>

        <ul className="signup-includes">
          {FREE_INCLUDES.map((item) => (
            <li key={item}><Check size={16} /> <span>{item}</span></li>
          ))}
        </ul>

        {error ? <p className="signup-error" role="alert">{error}</p> : null}

        <button className="signup-cta" onClick={start} disabled={submitting}>
          {submitting ? <><Loader2 size={17} className="spin" /> Opening Discord…</> : "Create free account"}
        </button>

        {/* The two objections that stop people signing up, answered
            directly under the button rather than buried in small print. */}
        <p className="signup-reassure">
          Free forever. No card required. We use Discord only to create your account —
          never your EA login.
        </p>

        <p className="signup-signin">
          Already a member?{" "}
          <Link to={`/login${from ? `?from=${encodeURIComponent(from)}` : ""}`}>Sign in</Link>
        </p>

        <details className="signup-compare">
          <summary>What do I get if I upgrade later?</summary>
          <PlanComparison elitePrice={elitePrice} />
          <p className="signup-compare-note">
            You never have to. Free stays free — it isn't a trial and it doesn't expire.
          </p>
        </details>

        <p className="signup-legal">
          By creating an account you agree to our <Link to="/terms">Terms</Link> and{" "}
          <Link to="/privacy">Privacy Policy</Link>.
        </p>
      </div>
    </main>
  );
}
