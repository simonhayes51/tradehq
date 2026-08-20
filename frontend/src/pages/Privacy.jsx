// src/pages/Privacy.jsx
//
// Every factual claim below is traceable to actual code in this repo
// (see the report delivered alongside this file for the exact
// file:line citations). Anything the repo can't answer - legal entity
// name, registered address, jurisdiction, a real contact/DPO email, and
// the exact data-retention period for each table - is marked
// [NEEDS INPUT] rather than guessed. Do not remove those markers without
// replacing them with a real, confirmed answer.
import "../styles/legal.css";

export default function Privacy() {
  return (
    <div className="legal-page">
      <h1>Privacy Policy</h1>
      <p className="legal-updated">Last updated: [NEEDS INPUT — date this is published]</p>

      <p><strong>[NEEDS INPUT: the legal entity operating EAFC Live — company name, registered address and jurisdiction. Until this is filled in, this page describes what data is collected and why, but not who the data controller legally is.]</strong></p>

      <h2>What we collect</h2>
      <ul>
        <li><strong>Discord account info.</strong> Signing in uses Discord OAuth with the <code>identify</code> scope only. We receive your Discord user ID, username, global display name and avatar image URL. We do not request your email address or your Discord server memberships beyond an optional server-membership check.</li>
        <li><strong>Trading data you add.</strong> Cards you watch, alert rules you set, and trades you log — all tied to your account so you can see them again.</li>
        <li><strong>Session data.</strong> A signed session cookie identifies you as logged in. It does not contain your password (Discord handles authentication; we never see or store one).</li>
      </ul>

      <h2>What we don't collect</h2>
      <p>We don't run third-party analytics or tracking scripts on this site. There is no ad tracking.</p>

      <h2>Third parties we share data with</h2>
      <ul>
        <li><strong>Discord</strong> — for sign-in only.</li>
        <li><strong>[NEEDS INPUT: payment provider]</strong> — a subscription/payment processor handles Premium billing. We believe this is Stripe based on the codebase, but please confirm the exact provider and their own privacy policy link before this goes live.</li>
        <li><strong>OpenAI</strong> — if you use the AI Chat feature, your messages are sent to OpenAI to generate a response.</li>
      </ul>

      <h2>Your data, your control</h2>
      <p>You can delete your logged trades, goals and saved settings from within the app at any time. [NEEDS INPUT: confirm whether full account deletion (removing your Discord link and profile entirely, not just trades/settings) is currently supported, or should be described as "contact us to close your account" instead.]</p>

      <h2>How long we keep data</h2>
      <p>[NEEDS INPUT: confirm the retention period for trade history, watchlist entries, and session data. This page should not state a specific number of days/months until that's confirmed.]</p>

      <h2>Contact</h2>
      <p>[NEEDS INPUT: a real contact email or address for privacy questions/requests. No contact channel currently exists in the app to reference here other than our Discord server.]</p>
    </div>
  );
}
