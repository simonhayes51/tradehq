// src/pages/Companion.jsx
//
// The "eafc.live/companion" launcher page - a public top-level route
// (see App.jsx, mounted the same way /landing and /demo are: outside
// PrivateRoute, no app shell/nav). This is meant to work as a standalone
// landing page a user reaches directly (e.g. from a link shared outside
// the app), not a screen inside the logged-in dashboard.
//
// Two buttons only, per the delivery model this replaces the abandoned
// Capacitor mobile app with: install the userscript once, then just use
// EA's own Web App normally.
//
//   [ INSTALL COMPANION ] -> the hosted, auditable eafc-live.user.js
//                             (a userscript manager intercepts this URL
//                             and offers to install it - this link is
//                             never meant to be "downloaded" and opened
//                             by hand).
//   [ LAUNCH FC WEB APP ]  -> EA's own Web App, unmodified. This page
//                             NEVER proxies EA - the link goes straight
//                             to ea.com, exactly like the "Already
//                             installed? Just launch the Web App
//                             normally." copy beneath it says.
const USERSCRIPT_URL = "https://app.eafc.live/companion/eafc-live.user.js";
const EA_WEB_APP_URL = "https://www.ea.com/ea-sports-fc/ultimate-team/web-app/";

export default function Companion() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--v2-bg,#0b0e14)] px-6 py-16">
      <div className="max-w-lg w-full text-center">
        <div className="text-xs font-bold tracking-widest text-[#7fd8ff] mb-3">EAFC LIVE</div>
        <h1 className="text-2xl sm:text-3xl font-semibold text-white mb-4">
          EAFC LIVE COMPANION
        </h1>
        <p className="text-sm text-[var(--v2-muted,#8a94a6)] mb-10 leading-relaxed">
          A read-only overlay that shows EAFC Live trading intelligence directly
          inside the official EA SPORTS FC 26 Web App Transfer Market. Install
          the userscript once, then open the Web App as you always do &mdash;
          EAFC Live never buys, bids, lists, or automates anything for you.
        </p>

        <div className="flex flex-col gap-3">
          <a
            href={USERSCRIPT_URL}
            className="inline-flex items-center justify-center rounded-lg bg-[#7fd8ff] text-[#0b0e14] font-semibold text-sm px-6 py-3 hover:opacity-90 transition"
          >
            INSTALL COMPANION
          </a>
          <a
            href={EA_WEB_APP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center rounded-lg border border-[var(--v2-border,#2c3542)] text-white font-semibold text-sm px-6 py-3 hover:bg-white/5 transition"
          >
            LAUNCH FC WEB APP
          </a>
        </div>

        <p className="text-xs text-[var(--v2-muted,#6b7484)] mt-8">
          Already installed? Just launch the Web App normally &mdash; the
          overlay attaches itself automatically.
        </p>

        <p className="text-xs text-[var(--v2-muted,#6b7484)] mt-6">
          Requires a userscript manager: Tampermonkey or Violentmonkey on
          desktop, the &ldquo;Userscripts&rdquo; app on iPhone, or Tampermonkey
          on Firefox for Android.
        </p>
      </div>
    </div>
  );
}
