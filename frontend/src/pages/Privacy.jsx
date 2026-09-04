// src/pages/Privacy.jsx
//
// Every claim here is either (a) sourced directly from this codebase -
// see the delivery report for file:line citations - or (b) a value the
// operator explicitly supplied. "EAFC.Live" is the supplied operator
// name only - this page must not state or imply a specific legal
// structure (company, partnership, sole trader, etc.) since neither the
// repository nor the operator has confirmed one.
import "../styles/legal.css";

export default function Privacy() {
  return (
    <div className="legal-page">
      <h1>Privacy Policy</h1>
      <p className="legal-updated">Last updated: 20 August 2026</p>

      <p>
        EAFC.Live is operated by <strong>EAFC.Live</strong>, of{" "}
        <span className="legal-address">Collingwood Buildings, 38 Collingwood Street, Newcastle upon Tyne, NE1 1JF, United Kingdom</span>.
        This policy explains what information we collect when you use EAFC.Live and why. This
        policy is governed by the law of England and Wales.
      </p>

      <h2>How you sign in</h2>
      <p>
        EAFC.Live uses Discord to sign you in (OAuth, <code>identify</code> scope). We receive
        and store: your Discord user ID, your Discord username, your Discord global/display
        name, and your Discord avatar image URL. We do not receive your email address or your
        Discord password through this process — Discord authenticates you, and we never see or
        store a password.
      </p>

      <h2>What you create in the app</h2>
      <p>Signed-in use of EAFC.Live can create the following data, tied to your account:</p>
      <ul>
        <li>Your watchlist (cards you're tracking)</li>
        <li>Price alert rules you set</li>
        <li>Trade history you log</li>
        <li>Portfolio information (e.g. starting balance)</li>
        <li>Settings and preferences</li>
        <li>Your account/profile record itself (the Discord fields above)</li>
      </ul>

      <h2>AI Chat</h2>
      <p>
        If you use the AI Chat feature, the message you type is sent to OpenAI
        (model <code>gpt-5.6-luna</code>) to generate a reply. This only happens when you actively
        use AI Chat — it does not apply to your general use of EAFC.Live. We do not keep a stored
        transcript of your AI Chat conversations in our own database.
      </p>

      <h2>Payments</h2>
      <p>
        Premium subscriptions are handled by Stripe. When you subscribe, Stripe collects your
        payment details directly — EAFC.Live does not receive or store your card number. What we
        do store is your Stripe customer/subscription reference and your subscription status
        (active, cancelled, trial, etc.), so we can show you the correct plan and manage renewals.
      </p>

      <h2>Cookies and sessions</h2>
      <p>
        EAFC.Live uses a single session cookie to keep you signed in. It's a signed cookie (it
        can't be edited by you or anyone else without invalidating it), sent only over HTTPS in
        production, and lasts up to 14 days from when you last signed in, after which you'll need
        to sign in again. We don't use advertising or tracking cookies, and we don't run any
        third-party analytics or tracking scripts on this site.
      </p>

      <h2>How long we keep data</h2>
      <p>
        Your watchlist, alerts, trade history, portfolio and settings are kept for as long as
        your account exists — there is currently no automatic time-based deletion of this data
        while your account is active. If you subscribe to Premium, we keep a record of that
        subscription (via Stripe) for as long as reasonably necessary for accounting purposes,
        even after you cancel or delete your account — see "Deleting your account" below. Deleting
        data from our live database does not guarantee instant removal from routine backups,
        which are retained for a limited period as part of normal database operation before being
        overwritten.
      </p>

      <h2>Deleting your account</h2>
      <p>
        You can delete your EAFC.Live account from the Account page at any time. This removes
        your watchlist, alerts, trade history, portfolio and settings, and unlinks your Discord
        profile from EAFC.Live where that's possible. If you have ever had a Premium subscription,
        we keep a minimal billing record (not your trading data) rather than deleting it outright,
        because we're required to be able to account for past payments — your profile display
        information is still cleared in that case. This action cannot be undone.
      </p>

      <h2>Who we share data with</h2>
      <ul>
        <li><strong>Discord</strong> — for signing you in.</li>
        <li><strong>Stripe</strong> — for processing Premium payments.</li>
        <li><strong>OpenAI</strong> — only for messages you send through AI Chat.</li>
      </ul>
      <p>We do not sell your data, and we do not share it with anyone else.</p>

      <h2>Your rights</h2>
      <p>
        Depending on where you live, you may have rights to access, correct, or delete the
        personal data we hold about you, in addition to the in-app account deletion above. To
        exercise these rights, contact us using the details below.
      </p>

      <h2>Contact</h2>
      <p>
        For any questions about this policy or your data, contact us at{" "}
        <a href="mailto:support@eafc.live">support@eafc.live</a>, or by post at the address above.
      </p>
    </div>
  );
}
