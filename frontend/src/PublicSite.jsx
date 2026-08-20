// src/PublicSite.jsx
//
// The public marketing site at eafc.live - a separate, lightweight route
// tree from the trading app (App.jsx, served at app.eafc.live), but the
// SAME frontend build/deploy (see lib/domains.js) rather than a second
// project, so there's nothing extra to build or keep in sync. Reuses the
// v2 design tokens and real live-data hooks so it looks and feels like
// the product, not a bolted-on marketing page - just without the app
// shell (sidebar, auth-gated sections) around it.
import { useEffect } from "react";
import { HashRouter, Routes, Route, Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useFutggOpportunities, useFutggMarketFreshness } from "./v2/hooks/useFutggMarket";
import { APP_ORIGIN } from "./lib/domains";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import "./v2/styles/tokens.css";
import "./v2/styles/dashboard-v2.css";
import "./styles/public-site.css";

function count(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(v);
}
function coins(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString("en-GB") : "—";
}

function PublicNav() {
  return (
    <header className="pub-nav">
      <Link to="/" className="pub-brand">EAFC <b>Live</b></Link>
      <nav>
        <a href={`${APP_ORIGIN}/#/billing`} className="pub-nav-pricing">Pricing</a>
        <a href={`${APP_ORIGIN}/#/login`} className="pub-nav-login">Log in</a>
        <a href={`${APP_ORIGIN}/#/signup`} className="pub-cta">Start free</a>
      </nav>
    </header>
  );
}

function OpportunityPreviewCard({ item }) {
  const player = item.player_name || item.name || "Card";
  const rating = item.rating ?? item.player_rating;
  const buyBelow = item.recommended_buy_max ?? item.buy_below ?? item.current_bin;
  const profit = item.expected_profit_after_tax;
  const roi = item.expected_roi != null ? `${Math.round(Number(item.expected_roi) * 100)}%` : null;
  return (
    <a className="pub-opp-card" href={`${APP_ORIGIN}/#/v2/players/${item.source_card_id}`}>
      {item.player_image_url ? <img src={item.player_image_url} alt={player} /> : <div className="pub-opp-noimg" />}
      <div className="pub-opp-copy">
        <strong>{player}{rating ? ` · ${rating} OVR` : ""}</strong>
        <span>Buy below <b>{coins(buyBelow)}</b></span>
        <span className="pub-opp-profit">+{coins(profit)} coins{roi ? ` · ${roi}` : ""} after tax</span>
      </div>
    </a>
  );
}

function PublicHome() {
  const oppsQuery = useFutggOpportunities({ page_size: 3 });
  const freshnessQuery = useFutggMarketFreshness();
  const opportunities = oppsQuery.data?.items ?? [];
  const cardsTracked = freshnessQuery.data?.cards_priced;

  return (
    <main className="pub-main">
      <section className="pub-hero">
        <span className="pub-eyebrow">EAFC LIVE</span>
        <h1>Know what a card is really worth before you buy it.</h1>
        <p>EAFC Live compares live prices against real completed sales to show which cards may be worth buying — and why.</p>
        {cardsTracked ? <p className="pub-proof">{count(cardsTracked)} cards tracked right now</p> : null}
        <div className="pub-hero-actions">
          <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Start free <ArrowRight size={16} /></a>
          <a className="pub-secondary" href="#opportunities">See live opportunities</a>
        </div>
      </section>

      <section id="opportunities" className="pub-section">
        <div className="pub-section-head"><span>LIVE OPPORTUNITIES</span><h2>Real cards, right now</h2></div>
        {opportunities.length ? (
          <div className="pub-opp-grid">
            {opportunities.slice(0, 3).map((item) => (
              <OpportunityPreviewCard key={item.source_card_id} item={item} />
            ))}
          </div>
        ) : (
          <p className="pub-empty">Still building live market data — check back shortly.</p>
        )}
        <a className="pub-see-all" href={`${APP_ORIGIN}/#/signup`}>See all opportunities <ArrowRight size={15} /></a>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>WHY TRUST IT</span><h2>No guessing, no hype</h2></div>
        <div className="pub-trust-grid">
          <div><strong>Real sales</strong><p>We use completed sales, not just what sellers are asking.</p></div>
          <div><strong>No guessing</strong><p>If there isn't enough reliable data on a card, we say so instead of guessing.</p></div>
          <div><strong>Simple answers</strong><p>BUY, WAIT or AVOID — with the reasoning underneath if you want it.</p></div>
        </div>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>HOW IT WORKS</span><h2>Three steps</h2></div>
        <ol className="pub-how-steps">
          <li><b>1</b><div><strong>We track the market</strong><p>Prices, completed sales and movement, updated live.</p></div></li>
          <li><b>2</b><div><strong>We compare the evidence</strong><p>EAFC Live checks whether a card looks cheap, fair or overpriced.</p></div></li>
          <li><b>3</b><div><strong>You get the simple answer</strong><p>BUY, WAIT or AVOID — plus a target price and expected profit.</p></div></li>
        </ol>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>FREE VS PREMIUM</span><h2>Start free, upgrade if it's worth it</h2></div>
        <div className="pub-pricing-grid">
          <div className="pub-pricing-card">
            <h3>Free</h3>
            <ul>
              <li>Player search &amp; current prices</li>
              <li>Fair value on individual cards</li>
              <li>Opportunity preview</li>
              <li>Watchlist</li>
              <li>Basic trade tracking</li>
            </ul>
            <a href={`${APP_ORIGIN}/#/signup`}>Start free</a>
          </div>
          <div className="pub-pricing-card pub-featured">
            <h3>Premium <span>£3/mo or £25/season</span></h3>
            <ul>
              <li>Full opportunities feed</li>
              <li>Full Trade Finder</li>
              <li>Unlimited watchlist &amp; price alerts</li>
              <li>Full trade history &amp; performance</li>
              <li>Premium Discord role</li>
            </ul>
            <a href={`${APP_ORIGIN}/#/signup`}>Try Premium — 7 days free</a>
          </div>
        </div>
      </section>

      <p className="pub-disclaimer">Recent sales and live listings are used to estimate opportunities. Profit is never guaranteed and EA charges 5% on sales.</p>
    </main>
  );
}

function ForwardToApp() {
  // Any path on the public host other than "/", "/privacy" or "/terms" -
  // most commonly a shared app deep link someone retyped onto the apex
  // domain (eafc.live/#/v2/players/123) - forwards to the identical
  // path/hash on the real app domain rather than 404ing. Preserves the
  // full hash so query params (e.g. a signup ?from=) survive too.
  useEffect(() => {
    window.location.replace(`${APP_ORIGIN}/${window.location.hash}`);
  }, []);
  return <main className="pub-main"><p className="pub-empty">Taking you to the app…</p></main>;
}

export default function PublicSite() {
  return (
    <HashRouter>
      <div className="pub-root">
        <PublicNav />
        <Routes>
          <Route index element={<PublicHome />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="*" element={<ForwardToApp />} />
        </Routes>
        <footer className="pub-footer">
          <span>© {new Date().getFullYear()} EAFC Live</span>
          <nav>
            <a href={`${APP_ORIGIN}/#/billing`}>Pricing</a>
            <a href={`${APP_ORIGIN}/#/login`}>Log in</a>
            <a href="https://discord.gg/3sSpu3rDgn" target="_blank" rel="noreferrer">Discord</a>
            {/* Privacy/Terms deliberately NOT linked from the public
                footer yet - both pages still carry real [NEEDS INPUT]
                placeholders (operator name, address, contact email).
                The routes themselves stay live (Billing's links and
                direct visits still work) so they can be reviewed, but a
                launch-facing footer shouldn't point at a page that says
                "NEEDS INPUT" on screen. Add these two links back once
                Privacy.jsx/Terms.jsx have no [NEEDS INPUT] markers left. */}
          </nav>
        </footer>
      </div>
    </HashRouter>
  );
}
