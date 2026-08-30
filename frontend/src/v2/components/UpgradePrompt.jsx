// src/v2/components/UpgradePrompt.jsx
//
// The one locked-state component. Every gated surface uses it, so a
// customer meets the same explanation in the same shape wherever they
// hit a limit.
//
// Four things, always, because a lock that only says "Premium feature"
// asks someone to pay for something they cannot evaluate:
//   1. what the feature does
//   2. why it is on the paid plan
//   3. what they would actually get
//   4. what it costs, from the live Stripe price
//
// Deliberately NOT a banner. It renders where the limit was reached -
// the empty Trade Finder, the third alert, the eleventh watchlist card -
// because a prompt at the point of friction is information, and the same
// prompt repeated across every page is nagging.
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Lock } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { EVENTS, trackOnce } from "../../lib/analytics";
import { PAID_PLAN_NAME } from "../lib/limits";
import { headlinePrice, useBillingPlans } from "../hooks/useBillingPlans";

/**
 * @param feature   stable key, used for analytics and the ?unlock= param
 * @param title     what the feature IS, in the customer's words
 * @param what      one sentence: what it does for them
 * @param why       one sentence: why it sits on the paid plan
 * @param youGet    array of concrete things included
 */
export default function UpgradePrompt({
  feature,
  title,
  what,
  why,
  youGet = [],
  compact = false,
}) {
  const { isAuthenticated } = useAuth();
  const { data: plans } = useBillingPlans();
  const price = headlinePrice(plans, "monthly");

  useEffect(() => {
    trackOnce(EVENTS.UPGRADE_PROMPT_VIEWED, { feature }, feature);
  }, [feature]);

  // A signed-out visitor is not an upgrade candidate yet - asking them to
  // pay before they have an account inverts the funnel. Send them to the
  // free account first.
  const cta = isAuthenticated
    ? { to: `/billing?unlock=${encodeURIComponent(feature)}`, label: `Upgrade to ${PAID_PLAN_NAME}` }
    : { to: `/signup?from=${encodeURIComponent(window.location.hash.slice(1) || "/")}`, label: "Create free account" };

  return (
    <section className={`upgrade-prompt${compact ? " compact" : ""}`} aria-labelledby={`upgrade-${feature}`}>
      <div className="upgrade-prompt-icon"><Lock size={18} /></div>
      <h2 id={`upgrade-${feature}`}>{title}</h2>
      <p className="upgrade-prompt-what">{what}</p>
      {why ? <p className="upgrade-prompt-why">{why}</p> : null}

      {youGet.length ? (
        <ul className="upgrade-prompt-list">
          {youGet.map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : null}

      <div className="upgrade-prompt-actions">
        <Link className="upgrade-prompt-cta" to={cta.to}>
          {cta.label} <ArrowRight size={15} />
        </Link>
        {/* The price is stated on the prompt itself. Making someone click
            through to find out what they are being asked to pay is the
            oldest dark pattern in subscriptions. When Stripe is
            unreachable we say nothing rather than guessing. */}
        {isAuthenticated && price ? (
          <span className="upgrade-prompt-price">{price}, cancel anytime</span>
        ) : null}
        {!isAuthenticated ? (
          <span className="upgrade-prompt-price">Free account, no card needed</span>
        ) : null}
      </div>
    </section>
  );
}
