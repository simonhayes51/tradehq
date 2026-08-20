// src/pages/Terms.jsx
//
// Same rule as Privacy.jsx: only facts traceable to this codebase, every
// other field marked [NEEDS INPUT]. Do not fill these in with guessed or
// generic SaaS boilerplate - a wrong cancellation/refund term is worse
// than an honest placeholder.
import "../styles/legal.css";

export default function Terms() {
  return (
    <div className="legal-page">
      <h1>Terms of Service</h1>
      <p className="legal-updated">Last updated: [NEEDS INPUT — date this is published]</p>

      <p><strong>[NEEDS INPUT: the legal entity offering this service, and the jurisdiction whose law governs these terms.]</strong></p>

      <h2>The service</h2>
      <p>EAFC Live provides EA FC Ultimate Team market data, price analysis and trading tools. Prices and recommendations are based on real completed sales and live listings, but nothing here guarantees a profit — the market can and does move against a call.</p>

      <h2>Accounts</h2>
      <p>You sign in with Discord. You're responsible for your Discord account's security; we authenticate you through it but never see your Discord password.</p>

      <h2>Premium subscription</h2>
      <p>Premium is currently £3/month or £25/season (12 months), with a 7-day free trial. [NEEDS INPUT: confirm exact cancellation mechanics (self-serve in-app vs. contact required), refund policy, and what happens to trial users who don't cancel — the app has a cancel-subscription flow, but its exact terms (proration, effective date) need confirming before stating them here as a promise.]</p>

      <h2>Acceptable use</h2>
      <p>[NEEDS INPUT: any specific prohibited-use terms you want stated (e.g. no automated scraping of the app, no reselling access).]</p>

      <h2>Disclaimer</h2>
      <p>Trading Ultimate Team cards carries risk. EAFC Live's BUY/WAIT/AVOID calls and expected-profit figures are estimates based on recent market data, not guarantees. We are not responsible for trading losses.</p>

      <h2>Contact</h2>
      <p>[NEEDS INPUT: a real contact email or address for legal/terms questions.]</p>
    </div>
  );
}
