// src/PublicSite.jsx
//
// The public marketing site at eafc.live - a separate, lightweight route
// tree from the trading app (App.jsx, served at app.eafc.live), but the
// SAME frontend build/deploy (see lib/domains.js) rather than a second
// project, so there's nothing extra to build or keep in sync. Reuses the
// v2 design tokens so it looks and feels like the product, not a
// bolted-on marketing page - just without the app shell (sidebar,
// auth-gated sections) around it.
//
// Anonymous visitors get AT MOST one real opportunity, with the
// actionable numbers (exact buy/sell target, profit, ROI, rationale)
// redacted server-side (GET /api/v2/opportunities/preview, see
// futgg_market.py's _redact_for_public_preview) - never the full
// /opportunities feed. That's a deliberate product decision (don't give
// away the paid/free-account value to a visitor who hasn't even
// registered), enforced by the API contract itself, not by hiding
// fields client-side over data that was already sent in full.
import { useEffect, useRef } from "react";
import { HashRouter, Routes, Route, Link } from "react-router-dom";
import { ArrowRight, Lock, TrendingUp, ShieldCheck, Zap, ChevronRight } from "lucide-react";
import { useFutggOpportunityPreview, useFutggMarketFreshness } from "./v2/hooks/useFutggMarket";
import api from "./axios";
import { APP_ORIGIN, DISCORD_INVITE_URL } from "./lib/domains";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import "./v2/styles/tokens.css";
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

export function scrollToPublicSection(sectionId) {
  const section = document.getElementById(sectionId);
  if (!section) return false;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
  return true;
}

function PublicNav() {
  return (
    <header className="pub-nav">
      <Link to="/" className="pub-brand">EAFC <b>Live</b></Link>
      <nav>
        <button type="button" className="pub-nav-extra" onClick={() => scrollToPublicSection("how")}>How it works</button>
        <a href={`${APP_ORIGIN}/#/billing`} className="pub-nav-pricing">Pricing</a>
        <a href={DISCORD_INVITE_URL} className="pub-nav-discord" target="_blank" rel="noreferrer">Discord</a>
        <a href={`${APP_ORIGIN}/#/login`} className="pub-nav-login">Log in</a>
        <a href={`${APP_ORIGIN}/#/signup`} className="pub-cta">Start free</a>
      </nav>
    </header>
  );
}

const SIGNAL_LABEL = { buy: "BUY", strong_buy: "STRONG BUY", watch: "WATCH" };

// The hero/preview visual - a real card (or, while data loads/if nothing
// currently qualifies, an honest empty state), with the verdict and
// price genuinely present but every actionable number shown as a
// deliberate locked row rather than a real figure, because the API
// response this reads from never contains those fields for a signed-out
// caller in the first place.
function LockedOpportunityCard({ item, loading }) {
  if (loading) {
    return (
      <div className="pub-hero-card pub-hero-card-loading" aria-busy="true">
        <div className="pub-skel pub-skel-img" />
        <div className="pub-skel pub-skel-line" style={{ width: "60%" }} />
        <div className="pub-skel pub-skel-line" style={{ width: "40%" }} />
      </div>
    );
  }
  if (!item) {
    return (
      <div className="pub-hero-card">
        <p className="pub-empty">Still building live market data — check back shortly.</p>
      </div>
    );
  }
  const verdict = SIGNAL_LABEL[item.signal] || "WATCH";
  return (
    <div className="pub-hero-card">
      <div className="pub-hero-card-top">
        <span className={`pub-verdict pub-verdict-${item.signal === "avoid" ? "avoid" : "buy"}`}>{verdict}</span>
        {item.rating ? <span className="pub-hero-card-rating">{item.rating} OVR</span> : null}
      </div>
      <div className="pub-hero-card-player">
        {item.player_image_url || item.composited_image_url ? (
          <img src={item.composited_image_url || item.player_image_url} alt={item.name} />
        ) : (
          <div className="pub-opp-noimg" />
        )}
        <div>
          <strong>{item.name}</strong>
          {item.primary_position ? <span className="pub-hero-card-pos">{item.primary_position}</span> : null}
        </div>
      </div>
      <dl className="pub-hero-card-stats">
        <div>
          <dt>Current market</dt>
          <dd>{item.current_bin_approx != null ? `~${coins(item.current_bin_approx)}` : "—"}</dd>
        </div>
        <div className="locked">
          <dt>Buy target</dt>
          <dd><Lock size={12} /> LOCKED</dd>
        </div>
        <div className="locked">
          <dt>Potential profit</dt>
          <dd><Lock size={12} /> LOCKED</dd>
        </div>
      </dl>
      {item.headline_reason ? <p className="pub-hero-card-reason">{item.headline_reason}</p> : null}
      <a className="pub-hero-card-unlock" href={`${APP_ORIGIN}/#/signup`}>
        <Lock size={13} /> Create free account to see the full trade
      </a>
    </div>
  );
}

function PublicHome() {
  const previewQuery = useFutggOpportunityPreview();
  const freshnessQuery = useFutggMarketFreshness();
  const previewItem = previewQuery.data?.item ?? null;
  const cardsTracked = freshnessQuery.data?.cards_priced;
  const oppRef = useRef(null);

  // HashRouter treats "#opportunities" as a ROUTE, not an in-page anchor
  // - it used to silently send visitors into the app-forwarding catch-
  // all route instead of scrolling anywhere. scrollIntoView avoids the
  // router entirely.
  const scrollToPreview = () => oppRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <main className="pub-main">
      <section className="pub-hero pub-hero-split">
        <div className="pub-hero-copy">
          <span className="pub-eyebrow">EAFC LIVE</span>
          <h1>Know what a card is really worth before you buy it.</h1>
          <p>EAFC Live compares live prices against real completed sales to show which cards may be worth buying — and why.</p>
          {cardsTracked ? <p className="pub-proof">{count(cardsTracked)} cards tracked right now</p> : null}
          <div className="pub-hero-actions">
            <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Start free <ArrowRight size={16} /></a>
            <button type="button" className="pub-secondary" onClick={scrollToPreview}>See live opportunities</button>
          </div>
        </div>
        <div className="pub-hero-visual">
          <LockedOpportunityCard item={previewItem} loading={previewQuery.isLoading} />
        </div>
      </section>

      <section ref={oppRef} id="opportunities" className="pub-section">
        <div className="pub-section-head"><span>LIVE OPPORTUNITIES</span><h2>This is what EAFC Live finds</h2></div>
        <p className="pub-section-lead">
          One real example, right now. Create a free account to see today's full list — buy targets,
          expected profit and the reasoning behind every call.
        </p>
        <div className="pub-preview-wide">
          <LockedOpportunityCard item={previewItem} loading={previewQuery.isLoading} />
        </div>
        <a className="pub-see-all" href={`${APP_ORIGIN}/#/signup`}>Create free account to see today's opportunities <ArrowRight size={15} /></a>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>WHY TRUST IT</span><h2>No guessing, no hype</h2></div>
        <div className="pub-trust-grid">
          <div>
            <TrendingUp size={18} />
            <strong>Real sales</strong>
            <p>We use completed sales, not just what sellers are asking.</p>
          </div>
          <div>
            <ShieldCheck size={18} />
            <strong>No guessing</strong>
            <p>Not enough reliable data? We'll tell you instead of making up a number.</p>
          </div>
          <div>
            <Zap size={18} />
            <strong>Simple answer</strong>
            <p>BUY, WAIT or AVOID — with the full reasoning there if you want it.</p>
          </div>
        </div>
      </section>

      <section id="how" className="pub-section">
        <div className="pub-section-head"><span>HOW IT WORKS</span><h2>Three steps</h2></div>
        <ol className="pub-how-steps">
          <li><b>1</b><div><strong>We track the market</strong><p>Prices, completed sales and movement, updated live.</p></div></li>
          <li><b>2</b><div><strong>We compare the evidence</strong><p>EAFC Live checks whether a card looks cheap, fair or overpriced.</p></div></li>
          <li><b>3</b><div><strong>You get the simple answer</strong><p>BUY, WAIT or AVOID — plus a target price and expected profit.</p></div></li>
        </ol>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>SIMPLE TO ADVANCED</span><h2>Easy for casual trades, deep enough for serious ones</h2></div>
        <p className="pub-section-lead">Every call starts simple. The full evidence is always one tap away, for anyone who wants it.</p>
        <div className="pub-demo">
          <div className="pub-demo-simple">
            <span className="pub-verdict pub-verdict-buy">BUY</span>
            <p>Buy around <b>76,000</b> · profit around <b>4,000</b> after tax</p>
            <span className="pub-demo-speed">Selling speed: Good</span>
            <p className="pub-demo-note">This card has recently been selling above its current asking price.</p>
          </div>
          <button type="button" className="pub-demo-advanced" disabled aria-disabled="true">
            <span><ChevronRight size={14} /> Advanced rationale</span>
            <span className="pub-demo-advanced-hint">Fair value, recent sales, confidence, risk and more — for signed-in traders</span>
          </button>
        </div>
      </section>

      <section className="pub-section">
        <div className="pub-section-head"><span>FREE VS PREMIUM</span><h2>Start free — upgrade if EAFC Live is worth it to you</h2></div>
        <div className="pub-pricing-grid">
          <div className="pub-pricing-card">
            <h3>Free</h3>
            <p className="pub-pricing-tagline">Great if you want to check cards yourself.</p>
            <ul>
              <li>Player search</li>
              <li>Individual card values</li>
              <li>Limited Opportunities</li>
              <li>Limited Watchlist</li>
              <li>Basic trade tracking</li>
            </ul>
            <a href={`${APP_ORIGIN}/#/signup`}>Start free</a>
          </div>
          <div className="pub-pricing-card pub-featured">
            <h3>Premium <span>£3/mo or £25/season</span></h3>
            <p className="pub-pricing-tagline">For traders who want EAFC Live to find the opportunities for them.</p>
            <ul>
              <li>Full Opportunities feed</li>
              <li>Full Trade Finder</li>
              <li>Unlimited Watchlist</li>
              <li>Price alerts</li>
              <li>Full trade/performance tracking</li>
              <li>Premium Discord access</li>
            </ul>
            <a href={`${APP_ORIGIN}/#/signup`}>Try Premium — 7 days free</a>
          </div>
        </div>
      </section>

      <section className="pub-midcta">
        <h2>Ready to find your own opportunities?</h2>
        <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Start free <ArrowRight size={16} /></a>
        <p>No credit card required to create a free account.</p>
      </section>

      <p className="pub-disclaimer">Recent sales and live listings are used to estimate opportunities. Profit is never guaranteed and EA charges 5% on sales.</p>
    </main>
  );
}

function ForwardToApp() {
  // Any path on the public host other than "/", "/privacy" or "/terms" -
  // most commonly a shared app deep link someone retyped onto the apex
  // domain (eafc.live/#/players/123) - forwards to the identical
  // path/hash on the real app domain rather than 404ing. Preserves the
  // full hash so query params (e.g. a signup ?from=) survive too.
  useEffect(() => {
    window.location.replace(`${APP_ORIGIN}/${window.location.hash}`);
  }, []);
  return <main className="pub-main"><p className="pub-empty">Taking you to the app…</p></main>;
}

export default function PublicSite() {
  useEffect(() => {
    const match = window.location.pathname.match(/^\/r\/([A-Za-z0-9_-]{3,64})\/?$/);
    const queryCode = new URLSearchParams(window.location.search).get("ref");
    const code = match?.[1] || queryCode;
    if (!code) return;
    api.post(`/api/referrals/track-click?code=${encodeURIComponent(code)}`, null, { __skipAuthRedirect: true })
      .catch(() => null);
    if (match) window.history.replaceState({}, "", "/");
  }, []);

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
            <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Discord</a>
            <Link to="/privacy">Privacy</Link>
            <Link to="/terms">Terms</Link>
            <a href="mailto:support@eafc.live">support@eafc.live</a>
          </nav>
        </footer>
      </div>
    </HashRouter>
  );
}
