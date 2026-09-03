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
import { useEffect, useRef, useState } from "react";
import { HashRouter, Routes, Route, Link } from "react-router-dom";
import { ArrowRight, Lock, TrendingUp, ShieldCheck, Zap, ChevronRight, Menu, X, Users, Server } from "lucide-react";
import { useFutggOpportunityPreview, useFutggMarketFreshness } from "./v2/hooks/useFutggMarket";
import { useTrackRecord } from "./v2/hooks/useTrackRecord";
import { useBillingPlans, headlinePrice } from "./v2/hooks/useBillingPlans";
import { PLAN_ROWS } from "./v2/components/PlanComparison";
import { PAID_PLAN_NAME, FREE_LIMITS } from "./v2/lib/limits";
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

const NAV_LINKS = [
  { label: "How it works", onClick: () => scrollToPublicSection("how") },
  { label: "Features", onClick: () => scrollToPublicSection("trust") },
  { label: "Pricing", onClick: () => scrollToPublicSection("pricing") },
];

function PublicNav() {
  const [open, setOpen] = useState(false);

  function go(fn) {
    setOpen(false);
    fn();
  }

  return (
    <header className="pub-nav">
      <Link to="/" className="pub-brand" onClick={() => setOpen(false)}>EAFC <b>Live</b></Link>
      <nav className="pub-nav-links">
        {NAV_LINKS.map((link) => (
          <button key={link.label} type="button" className="pub-nav-extra" onClick={link.onClick}>{link.label}</button>
        ))}
        <a href={DISCORD_INVITE_URL} className="pub-nav-discord" target="_blank" rel="noreferrer">Discord</a>
        <a href={`${APP_ORIGIN}/#/login`} className="pub-nav-login">Log in</a>
        <a href={`${APP_ORIGIN}/#/signup`} className="pub-cta">Create free account</a>
      </nav>
      <button
        type="button"
        className="pub-nav-toggle"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <X size={22} /> : <Menu size={22} />}
      </button>
      {open ? (
        <div className="pub-nav-drawer" role="menu">
          {NAV_LINKS.map((link) => (
            <button key={link.label} type="button" onClick={() => go(link.onClick)}>{link.label}</button>
          ))}
          <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>Discord</a>
          <a href={`${APP_ORIGIN}/#/login`} onClick={() => setOpen(false)}>Log in</a>
          <a href={`${APP_ORIGIN}/#/signup`} className="pub-cta" onClick={() => setOpen(false)}>Create free account</a>
        </div>
      ) : null}
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

// The public pricing table. Rows come from PLAN_ROWS - the same list the
// signup page, the billing page and every locked state in the app read -
// so a difference stated here is a difference the product actually
// enforces. The price comes from GET /api/billing/plans (Stripe's own
// configuration), never a number typed into this file: the marketing site
// claiming £3 while Stripe charges something else is a chargeback, not a
// typo.
function PublicPlanTable() {
  const plansQuery = useBillingPlans();
  const monthly = headlinePrice(plansQuery.data, "monthly");
  const season = headlinePrice(plansQuery.data, "season");
  const priceLine = [monthly, season].filter(Boolean).join(" or ");

  return (
    <div className="pub-pricing-grid">
      <div className="pub-pricing-card">
        <h3>Free <span>£0 — permanently</span></h3>
        <p className="pub-pricing-tagline">Enough to judge whether the calls are any good.</p>
        <ul>
          {PLAN_ROWS.filter((row) => row.free !== false).map((row) => (
            <li key={row.label}>
              <b>{row.label}</b>
              {row.free === true ? null : <span> — {row.free}</span>}
            </li>
          ))}
        </ul>
        <a href={`${APP_ORIGIN}/#/signup`}>Create free account</a>
      </div>
      <div className="pub-pricing-card pub-featured">
        <h3>
          {PAID_PLAN_NAME}
          {priceLine ? <span>{priceLine}</span> : null}
        </h3>
        <p className="pub-pricing-tagline">For traders who want EAFC Live to find the opportunities for them.</p>
        <ul>
          {PLAN_ROWS.map((row) => (
            <li key={row.label}>
              <b>{row.label}</b>
              {row.elite === true ? null : <span> — {row.elite}</span>}
            </li>
          ))}
        </ul>
        <a href={`${APP_ORIGIN}/#/billing`}>See {PAID_PLAN_NAME} pricing</a>
      </div>
    </div>
  );
}

// The public track record. Deliberately the SHORT version - headline
// numbers over the window the API gives an anonymous caller, with the
// full ledger behind an account - and it renders nothing at all rather
// than a zero or a placeholder when there is no published data yet.
// An invented win rate on a marketing page is the one number that would
// make everything else here worthless.
function PublicTrackRecord() {
  const query = useTrackRecord("30d");
  const published = query.data?.published;
  // has_enough_data is the backend's own "this sample is too small to
  // quote a rate from" flag (PROOF_MIN_SAMPLE). Below it, no percentage
  // is shown - a win rate computed from four calls is not a track record.
  if (!published?.has_published_data || !published?.has_enough_data) return null;

  const stats = [
    { label: "Calls published", value: published.published_opportunities },
    { label: "Hit their target", value: published.wins },
    { label: "Did not", value: published.losses },
    {
      label: "Win rate",
      value: published.win_rate_pct == null ? null : `${published.win_rate_pct}%`,
    },
  ].filter((stat) => stat.value !== null && stat.value !== undefined);

  if (!stats.length) return null;

  return (
    <section className="pub-section">
      <div className="pub-section-head"><span>TRACK RECORD</span><h2>How the calls have actually done</h2></div>
      <p className="pub-section-lead">
        The last {query.data?.window_days ?? 30} days of recommendations that were genuinely published
        to members — not backtests, and not a selection of the good ones. EA's 5% sale tax is already
        taken off every profit figure.
      </p>
      <dl className="pub-record-grid">
        {stats.map((stat) => (
          <div key={stat.label}>
            <dt>{stat.label}</dt>
            <dd>{stat.value}</dd>
          </div>
        ))}
      </dl>
      <p className="pub-record-note">
        Every individual call, every time window and the full win/loss ledger are in the app.
      </p>
      <a className="pub-see-all" href={`${APP_ORIGIN}/#/signup`}>
        Create free account to see the record <ArrowRight size={15} />
      </a>
    </section>
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
  const scrollToDiscordPricing = () => scrollToPublicSection("discord-pricing");

  return (
    <main className="pub-main">
      <section className="pub-hero pub-hero-split">
        <div className="pub-hero-copy">
          <span className="pub-eyebrow">EAFC LIVE</span>
          <h1>Know what a card is really worth before you buy it.</h1>
          <p>
            EAFC Live compares live prices against real completed sales to show which cards may be
            worth buying, and why. Use it yourself to find your own trades, or point a Discord
            server's feed at it to post the same calls automatically.
          </p>
          {cardsTracked ? <p className="pub-proof">{count(cardsTracked)} cards tracked right now</p> : null}
          <div className="pub-hero-actions">
            <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={16} /></a>
            <button type="button" className="pub-secondary" onClick={scrollToDiscordPricing}>View Discord server plans</button>
          </div>
          <button type="button" className="pub-hero-inline-link" onClick={scrollToPreview}>See live opportunities <ChevronRight size={14} /></button>
        </div>
        <div className="pub-hero-visual">
          <LockedOpportunityCard item={previewItem} loading={previewQuery.isLoading} />
        </div>
      </section>

      <section className="pub-paths">
        <div className="pub-path-card">
          <Users size={20} />
          <h3>For individual traders</h3>
          <p>Search any card, see what it's really worth, and get a short daily list of the opportunities EAFC Live currently rates as worth buying.</p>
          <a href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={14} /></a>
        </div>
        <div className="pub-path-card">
          <Server size={20} />
          <h3>For Discord communities</h3>
          <p>Add the EAFC Live bot to your server and it posts live buy calls straight to a channel, with the platform, budget and posting frequency you choose.</p>
          <button type="button" onClick={scrollToDiscordPricing}>See server plans <ArrowRight size={14} /></button>
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

      <PublicTrackRecord />

      <section id="trust" className="pub-section">
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
        <div className="pub-section-head"><span>HOW IT WORKS</span><h2>Two ways to use it</h2></div>
        <div className="pub-how-grid">
          <div className="pub-how-block">
            <h3><Users size={16} /> On your own account</h3>
            <ol className="pub-how-steps">
              <li><b>1</b><div><strong>We track the market</strong><p>Prices, completed sales and movement, updated live.</p></div></li>
              <li><b>2</b><div><strong>We compare the evidence</strong><p>EAFC Live checks whether a card looks cheap, fair or overpriced.</p></div></li>
              <li><b>3</b><div><strong>You get the simple answer</strong><p>BUY, WAIT or AVOID — plus a target price and expected profit.</p></div></li>
            </ol>
          </div>
          <div className="pub-how-block">
            <h3><Server size={16} /> On a Discord server</h3>
            <ol className="pub-how-steps">
              <li><b>1</b><div><strong>Invite the bot</strong><p>Add EAFC Live to your server (you'll need Manage Server).</p></div></li>
              <li><b>2</b><div><strong>Run /feed setup</strong><p>Pick a plan, a posting channel and the platform and budget mix you want.</p></div></li>
              <li><b>3</b><div><strong>The feed posts for you</strong><p>Buy calls land in that channel automatically, up to your plan's daily limit.</p></div></li>
            </ol>
          </div>
        </div>
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
            <span className="pub-demo-advanced-hint">Exact price, what it&apos;s worth, recent sales, confidence and risk — on the free account</span>
          </button>
        </div>
      </section>

      <section id="pricing" className="pub-section">
        <div className="pub-section-head">
          <span>PERSONAL &middot; FREE VS {PAID_PLAN_NAME.toUpperCase()}</span>
          <h2>Start free — upgrade if EAFC Live is worth it to you</h2>
        </div>
        <p className="pub-section-lead">
          The free account is free permanently, not a trial. No card needed to create one.
        </p>
        <PublicPlanTable />
      </section>

      <section id="discord-pricing" className="pub-section pub-discord-pricing">
        <div className="pub-section-head">
          <span>DISCORD SERVERS &middot; A SEPARATE PLAN</span>
          <h2>Give your server its own live trading feed</h2>
        </div>
        <p className="pub-section-lead">
          Billed per server, separately from any member's personal account. {PAID_PLAN_NAME} does not
          include a server feed, and a server feed does not give its members {PAID_PLAN_NAME} — they're
          two different products.
        </p>
        <div className="pub-pricing-grid pub-discord-grid">
          <div className="pub-pricing-card">
            <h3>Server Feed <span>£9.99/month</span></h3>
            <p className="pub-pricing-tagline">For a smaller or casual trading community.</p>
            <ul>
              <li><b>Up to 6 feed posts a day</b></li>
              <li><b>Choose your posting channel</b></li>
              <li><b>Platform and budget filters</b></li>
              <li><b>Owner-only setup via /feed setup</b></li>
            </ul>
            <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Get the bot <ArrowRight size={13} /></a>
          </div>
          <div className="pub-pricing-card pub-featured">
            <h3>Server Feed+ <span>£19.99/month</span></h3>
            <p className="pub-pricing-tagline">For a larger or more active trading community.</p>
            <ul>
              <li><b>Up to 12 feed posts a day</b></li>
              <li><b>Choose your posting channel</b></li>
              <li><b>Platform and budget filters</b></li>
              <li><b>Owner-only setup via /feed setup</b></li>
            </ul>
            <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Get the bot <ArrowRight size={13} /></a>
          </div>
        </div>
        <p className="pub-section-lead pub-discord-note">
          Invite the bot, then run <code>/feed setup</code> in your server (you need Manage Server) to
          choose a plan and start checkout. Already have the bot in your server? Manage or upgrade its
          feed from <a href={`${APP_ORIGIN}/#/discord-feed`}>your account's Server Feed page</a>.
        </p>
      </section>

      <section id="faq" className="pub-section">
        <div className="pub-section-head"><span>FAQ</span><h2>Common questions</h2></div>
        <dl className="pub-faq">
          <div>
            <dt>What's the difference between Free and {PAID_PLAN_NAME}?</dt>
            <dd>Free gets {FREE_LIMITS.dailyOpportunities} complete opportunity picks a day, a {FREE_LIMITS.watchlist}-card watchlist and the last {FREE_LIMITS.trackRecordDays} days of track record. {PAID_PLAN_NAME} unlocks the full live opportunities feed, Trade Finder, a {FREE_LIMITS.eliteWatchlist}-card watchlist, more alerts and the complete track record. The full breakdown is on the pricing page in the app.</dd>
          </div>
          <div>
            <dt>Is a personal account the same thing as a Discord server subscription?</dt>
            <dd>No. Your personal plan (Free or {PAID_PLAN_NAME}) is tied to your own account. A Discord Server Feed is tied to a specific Discord server and billed separately. You can have a personal {PAID_PLAN_NAME} subscription, own a Server Feed for your Discord, both, or neither.</dd>
          </div>
          <div>
            <dt>Does {PAID_PLAN_NAME} include a Discord server feed?</dt>
            <dd>No. {PAID_PLAN_NAME} is a personal upgrade for your own account. If you want EAFC Live to post live opportunities into a Discord server automatically, that server needs its own Server Feed or Server Feed+ subscription.</dd>
          </div>
          <div>
            <dt>What do the daily server post limits mean?</dt>
            <dd>Server Feed posts up to 6 opportunities a day into your chosen channel; Server Feed+ posts up to 12. You choose the split across low, medium and high budget cards, and can restrict posting to certain hours.</dd>
          </div>
          <div>
            <dt>Which platforms are supported?</dt>
            <dd>Console (PlayStation and Xbox share one EA transfer market) and PC are tracked separately. You choose which one your account or a server's feed follows.</dd>
          </div>
          <div>
            <dt>How do I manage or cancel billing?</dt>
            <dd>Personal plans: manage payment method, cancel or resubscribe from Billing in the app, which opens Stripe's secure billing portal. Discord server plans: manage them per-server from the Server Feed page or the <code>/feed billing</code> command; cancelling stops the feed at the end of the paid period.</dd>
          </div>
        </dl>
      </section>

      <section className="pub-midcta">
        <h2>Ready to find your own opportunities?</h2>
        <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={16} /></a>
        <p>Free permanently, no card required. Two complete picks every day.</p>
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
          <div className="pub-footer-cols">
            <div className="pub-footer-col">
              <span className="pub-footer-brand">EAFC <b>Live</b></span>
              <p>Live prices vs completed sales, for your own trading or a Discord server's feed.</p>
            </div>
            <div className="pub-footer-col">
              <span className="pub-footer-heading">Product</span>
              <button type="button" onClick={() => scrollToPublicSection("trust")}>Features</button>
              <button type="button" onClick={() => scrollToPublicSection("pricing")}>Personal pricing</button>
              <button type="button" onClick={() => scrollToPublicSection("discord-pricing")}>Discord server plans</button>
              <button type="button" onClick={() => scrollToPublicSection("faq")}>FAQ</button>
            </div>
            <div className="pub-footer-col">
              <span className="pub-footer-heading">Account</span>
              <a href={`${APP_ORIGIN}/#/signup`}>Create free account</a>
              <a href={`${APP_ORIGIN}/#/login`}>Log in</a>
              <a href={`${APP_ORIGIN}/#/billing`}>Billing</a>
              <a href={`${APP_ORIGIN}/#/discord-feed`}>Manage Server Feed</a>
            </div>
            <div className="pub-footer-col">
              <span className="pub-footer-heading">Community &amp; legal</span>
              <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Discord</a>
              <Link to="/privacy">Privacy</Link>
              <Link to="/terms">Terms</Link>
              <a href="mailto:support@eafc.live">support@eafc.live</a>
            </div>
          </div>
          <div className="pub-footer-bottom">
            <span>© {new Date().getFullYear()} EAFC Live</span>
          </div>
        </footer>
      </div>
    </HashRouter>
  );
}
