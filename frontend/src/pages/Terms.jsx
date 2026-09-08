// src/pages/Terms.jsx
//
// Same rule as Privacy.jsx: claims are either sourced from this codebase
// or explicitly supplied by the operator. Billing behaviour described
// here (cancel_at_period_end, optional trial handling) is read directly
// from the Stripe integration in backend/main.py, not assumed. Pricing is
// intentionally NOT hardcoded here: Stripe is the source of truth and the
// Billing page/checkout show the amount that will actually be charged.
import "../styles/legal.css";

export default function Terms() {
  return (
    <div className="legal-page">
      <h1>Terms of Service</h1>
      <p className="legal-updated">Last updated: 8 September 2026</p>

      <p>
        These terms are between you and <strong>EAFC.Live</strong> ("we", "us"), of{" "}
        <span className="legal-address">Collingwood Buildings, 38 Collingwood Street, Newcastle upon Tyne, NE1 1JF, United Kingdom</span>,
        and are governed by the law of England and Wales.
      </p>

      <h2>The service</h2>
      <p>
        EAFC.Live provides EA Sports FC Ultimate Team market data, price analysis and trading
        tools, based on live prices and completed sales. Our BUY/WAIT/AVOID calls and expected-profit
        figures are estimates based on recent market data — they are not guarantees, and the
        market can move against a call after we make it. Trading Ultimate Team cards carries risk,
        and you're responsible for your own trading decisions.
      </p>

      <h2>Your account</h2>
      <p>
        You sign in with Discord. You're responsible for keeping your Discord account secure —
        we authenticate you through Discord and never see or store your Discord password.
      </p>

      <h2>Elite subscription</h2>
      <p>
        Current Elite prices, billing periods and any promotional trial that applies are shown on
        the Billing page and again in Stripe before you confirm a subscription. Those live checkout
        details are the price and terms that apply to your purchase.
      </p>
      <ul>
        <li>You can cancel at any time from the Billing page.</li>
        <li>If your checkout includes a free trial, the subscription renews into the selected paid plan when the trial ends unless you cancel before the end of the trial.</li>
        <li>If you cancel during the free trial before it ends, you will not be charged for the first paid billing period.</li>
        <li>Cancelling an active paid subscription stops the next renewal — it does not end your Elite access immediately.</li>
        <li>If you cancel, your Elite access continues until the end of the billing period you've already paid for.</li>
      </ul>
      <p>
        We don't offer blanket refunds for time already used on a subscription. If you're a
        consumer in the UK, you have statutory rights under the Consumer Rights Act 2015 and
        consumer protection law that these terms don't remove or reduce — nothing here is meant
        to override those rights, and if anything above conflicts with them, your statutory
        rights take priority.
      </p>
      <p>
        Payment is processed by Stripe. We don't receive or store your card details — Stripe
        handles that directly.
      </p>

      <h2>Discord Server Feed subscriptions</h2>
      <p>
        A Discord Server Feed subscription belongs to the Discord server it is purchased for and
        is separate from a personal Elite subscription. The available Server Feed plan, posting
        allowance and price are shown during setup and in Stripe before payment is confirmed.
      </p>

      <h2>Acceptable use</h2>
      <p>Your account is for your own use. Please don't:</p>
      <ul>
        <li>share or sell access to your account;</li>
        <li>use bots, scrapers or automated tools against EAFC.Live outside of what we provide ourselves;</li>
        <li>try to disrupt or interfere with the service, or access it in a way not intended by these terms.</li>
      </ul>

      <h2>Ending your account</h2>
      <p>
        You can delete your EAFC.Live account at any time from the Account page. See our{" "}
        <a href="/#/privacy">Privacy Policy</a> for exactly what that does and doesn't remove.
        We may suspend or terminate an account that misuses the service.
      </p>

      <h2>Disclaimer</h2>
      <p>
        EAFC.Live is provided "as is". We do our best to keep pricing data accurate and current,
        but we don't guarantee it's error-free or uninterrupted, and we're not responsible for
        trading losses arising from your use of the service.
      </p>

      <h2>Changes to these terms</h2>
      <p>We may update these terms from time to time. Continued use of EAFC.Live after a change means you accept the update.</p>

      <h2>Contact</h2>
      <p>Questions about these terms: <a href="mailto:support@eafc.live">support@eafc.live</a>.</p>
    </div>
  );
}
