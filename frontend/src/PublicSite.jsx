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
// Structural note (rebuild): this used to be a single narrow (1120px)
// centred column of cards on one solid black field - every section the
// same width, same card treatment, same background, so the page read as
// "a few boxes floating in a lot of empty black" rather than a real
// marketing site. It's now a stack of FULL-WIDTH bands that alternate
// background (black / charcoal / off-white) for visual rhythm, each with
// its own constrained inner container - the Unbounce-style rhythm the
// design brief asked for - while keeping EAFC Live's actual black/white/
// green identity and never copying that reference site's branding,
// imagery or copy.
//
// Anonymous visitors get AT MOST one real opportunity, with the
// actionable numbers (exact buy/sell target, profit, ROI, rationale)
// redacted server-side (GET /api/v2/opportunities/preview, see
// futgg_market.py's _redact_for_public_preview) - never the full
// /opportunities feed. That's a deliberate product decision (don't give
// away the paid/free-account value to a visitor who hasn't even
// registered), enforced by the API contract itself, not by hiding
// fields client-side over data that was already sent in full. The
// multi-row trade-panel mockups below (hero visual, "Live product
// demonstration", the Discord message mockup) are illustrative UI
// composites, not a second live feed - each is labelled "Example" so it
// is never mistaken for the one real redacted card the API does return.
import { useEffect, useRef, useState } from "react";
import { HashRouter, Routes, Route, Link } from "react-router-dom";
import {
  ArrowRight, Lock, TrendingUp, TrendingDown, ShieldCheck, Zap, ChevronRight,
  Menu, X, Users, Server, Bot, Sliders, Activity, Wallet, Eye,
  LineChart, Hash, CheckCircle2, Gauge,
} from "lucide-react";
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
  { label: "Personal pricing", onClick: () => scrollToPublicSection("pricing") },
  { label: "Server pricing", onClick: () => scrollToPublicSection("discord-pricing") },
];

function PublicNav() {
  const [open, setOpen] = useState(false);

  function go(fn) {
    setOpen(false);
    fn();
  }

  return (
    <header className="pub-nav">
      <div className="pub-nav-inner">
        <Link to="/" className="pub-brand" onClick={() => setOpen(false)}>
          <img src="/icons/icon-192x192.png" alt="" width="32" height="32" className="pub-brand-mark" />
          <span>EAFC <b>Live</b></span>
        </Link>
        <nav className="pub-nav-links">
          {NAV_LINKS.map((link) => (
            <button key={link.label} type="button" className="pub-nav-extra" onClick={link.onClick}>{link.label}</button>
          ))}
          <a href={DISCORD_INVITE_URL} className="pub-nav-discord" target="_blank" rel="noreferrer">Discord</a>
          <a href={`${APP_ORIGIN}/#/login`} className="pub-nav-login">Log in</a>
          <a href={`${APP_ORIGIN}/#/signup`} className="pub-cta pub-cta-nav">Create free account</a>
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
      </div>
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

// Illustrative multi-row trade data, shaped like the real engine's output
// (see HomeDashboard.jsx's normaliseFutgg() / QueueCard for the actual
// field names this mirrors: entryPrice/buyBelow, recommendedSellTarget,
// expected profit after EA's 5% tax, confidence, risk, sellingSpeed,
// priceAgeSeconds-style freshness). Used only for the hero visual and the
// "Live product demonstration" panel below, both explicitly labelled
// "Example" - the one REAL card on this page is the redacted preview
// fetched from /api/v2/opportunities/preview via LockedOpportunityCard.
const DEMO_ROWS = [
  { name: "E. Haaland", rating: 91, position: "ST", version: "Winter Wildcard", bin: 895000, buyBelow: 860000, sellTarget: 985000, profit: 88750, confidence: 84, risk: "Low", speed: "Fast", movement: 4.1, sales: 38 },
  { name: "J. Bellingham", rating: 89, position: "CAM", version: "Rare", bin: 118000, buyBelow: 112000, sellTarget: 134000, profit: 15900, confidence: 77, risk: "Medium", speed: "Good", movement: 2.6, sales: 61 },
  { name: "G. Xhaka", rating: 84, position: "CDM", version: "Rare", bin: 15200, buyBelow: 14500, sellTarget: 17800, profit: 2680, confidence: 71, risk: "Medium", speed: "Good", movement: -1.2, sales: 94 },
  { name: "V. van Dijk", rating: 90, position: "CB", version: "Rare", bin: 246000, buyBelow: 235000, sellTarget: 271000, profit: 21500, confidence: 68, risk: "Medium", speed: "Fair", movement: 1.4, sales: 22 },
  { name: "R. Lewandowski", rating: 88, position: "ST", version: "Rare", bin: 62000, buyBelow: 58500, sellTarget: 71000, profit: 8925, confidence: 74, risk: "Low", speed: "Good", movement: 3.0, sales: 47 },
];

function DemoTradeRow({ row, compact = false }) {
  return (
    <div className={`pub-demo-row${compact ? " compact" : ""}`}>
      <div className="pub-demo-row-id">
        <div className="pub-demo-row-avatar" aria-hidden="true">{row.rating}</div>
        <div>
          <strong>{row.name}</strong>
          <span>{row.position} &middot; {row.version}</span>
        </div>
      </div>
      <div className="pub-demo-row-figs">
        <div><span>Current BIN</span><b>{coins(row.bin)}</b></div>
        <div><span>Buy below</span><b className="entry">{coins(row.buyBelow)}</b></div>
        {!compact ? <div><span>Sell target</span><b>{coins(row.sellTarget)}</b></div> : null}
        <div><span>Est. profit</span><b className="profit">+{coins(row.profit)}</b></div>
        {!compact ? (
          <div className="pub-demo-row-move">
            <span>Market move</span>
            <b className={row.movement >= 0 ? "up" : "down"}>
              {row.movement >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {row.movement >= 0 ? "+" : ""}{row.movement}%
            </b>
          </div>
        ) : null}
      </div>
      {!compact ? (
        <div className="pub-demo-row-meta">
          <span className={`pub-demo-conf conf-${row.confidence >= 80 ? "high" : row.confidence >= 65 ? "mid" : "low"}`}>
            <Gauge size={12} /> {row.confidence}% confidence
          </span>
          <span>{row.risk} risk</span>
          <span>{row.speed} selling speed</span>
          <span>{row.sales} sales analysed</span>
        </div>
      ) : null}
    </div>
  );
}

function DemoTradePanel({ rows, compact = false, title = "Today's opportunities", subtitle }) {
  return (
    <div className={`pub-demo-panel${compact ? " compact" : ""}`}>
      <div className="pub-demo-panel-head">
        <div className="feed-live"><i /> LIVE</div>
        <div>
          <strong>{title}</strong>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
        <span className="pub-demo-panel-tag">Example</span>
      </div>
      <div className="pub-demo-panel-rows">
        {rows.map((row) => <DemoTradeRow key={row.name} row={row} compact={compact} />)}
      </div>
    </div>
  );
}

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
        <span className="pub-hero-card-real">Real, live pick</span>
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

// A Discord-message-shaped mockup of what a server's feed actually posts,
// built from the same illustrative row shape as the trade-panel demo
// above (never live data) - explained by the flow steps beside it so the
// server product is understandable without needing to already have the
// bot installed.
function DiscordFeedMock() {
  const row = DEMO_ROWS[0];
  return (
    <div className="pub-discord-mock">
      <div className="pub-discord-mock-topbar">
        <Hash size={14} /> trading-feed
        <span className="pub-discord-mock-tag">Example</span>
      </div>
      <div className="pub-discord-mock-msg">
        <div className="pub-discord-mock-avatar"><Bot size={16} /></div>
        <div className="pub-discord-mock-body">
          <div className="pub-discord-mock-author">EAFC Live <span>BOT</span> <em>Today at 18:04</em></div>
          <div className="pub-discord-mock-embed">
            <div className="pub-discord-mock-embed-head">
              <strong>{SIGNAL_LABEL.strong_buy}</strong> {row.name} &middot; {row.rating} OVR {row.position}
            </div>
            <div className="pub-discord-mock-embed-grid">
              <div><span>Buy below</span><b>{coins(row.buyBelow)}</b></div>
              <div><span>Sell target</span><b>{coins(row.sellTarget)}</b></div>
              <div><span>Est. profit</span><b>+{coins(row.profit)}</b></div>
              <div><span>Confidence</span><b>{row.confidence}%</b></div>
            </div>
            <div className="pub-discord-mock-embed-foot">Posted automatically by your server's EAFC Live feed</div>
          </div>
        </div>
      </div>
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
        <span className="pub-pricing-ribbon">Most popular</span>
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
    <div className="pub-record-block">
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
    </div>
  );
}

const WHY_ITEMS = [
  { icon: LineChart, title: "Real completed sales", desc: "Verdicts are built from what cards actually sold for, not just what sellers are asking." },
  { icon: Gauge, title: "Fair-value calculations", desc: "Every card gets a computed fair value to compare its current price against." },
  { icon: Activity, title: "Live market monitoring", desc: "Prices and sales are checked continuously, not on a daily snapshot." },
  { icon: Sliders, title: "Clear buy & sell targets", desc: "A maximum entry and a target sale price, not just a vague direction." },
  { icon: Zap, title: "Trade Finder", desc: "Search every live-priced card by budget, risk, position and expected return." },
  { icon: Eye, title: "Watchlists & alerts", desc: "Track the cards you care about and get notified when your conditions are met." },
  { icon: Wallet, title: "Portfolio tracking", desc: "Log trades and see real profit and loss across your open and closed positions." },
  { icon: Bot, title: "Automatic Discord feeds", desc: "Point a server's channel at EAFC Live and it posts opportunities for you." },
];

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
      {/* ============ HERO — full-width, black ============ */}
      <section className="pub-band pub-band-black pub-hero-band">
        <div className="pub-container pub-hero-split">
          <div className="pub-hero-copy">
            <span className="pub-eyebrow">EAFC LIVE &middot; FC TRADING INTELLIGENCE</span>
            <h1>Find the FC cards actually worth buying — before everyone else does.</h1>
            <p>
              EAFC Live compares live prices against real completed sales to show which cards may be
              worth buying, and why. Use it yourself to find your own trades, or point a Discord
              server's feed at it to post the same calls automatically.
            </p>
            {cardsTracked ? <p className="pub-proof"><Activity size={13} /> {count(cardsTracked)} cards tracked right now</p> : null}
            <div className="pub-hero-actions">
              <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={16} /></a>
              <button type="button" className="pub-secondary" onClick={scrollToDiscordPricing}><Server size={15} /> View Discord server plans</button>
            </div>
            <button type="button" className="pub-hero-inline-link" onClick={scrollToPreview}>See live opportunities <ChevronRight size={14} /></button>
            {/* Balances the left column against the taller opportunities
                panel/hero card on the right (see pub-hero-split's
                align-items: center) instead of leaving a bare void under
                the CTA row - and it's substantive, not filler. Distinct
                from the trust strip immediately below this section
                (real sales / no guessing / BUY-WAIT-AVOID / Discord) on
                purpose, since the two sit right on top of each other on
                narrow screens where the hero stacks to one column. */}
            <ul className="pub-hero-facts">
              <li><Sliders size={14} /> Exact buy-below and sell-target on every call</li>
              <li><Gauge size={14} /> Confidence and risk shown alongside each one</li>
              <li><Wallet size={14} /> Free forever plan — no card required to start</li>
            </ul>
          </div>
          <div className="pub-hero-visual">
            <DemoTradePanel rows={DEMO_ROWS.slice(0, 3)} compact title="Today's opportunities" subtitle="What Elite members see" />
            <LockedOpportunityCard item={previewItem} loading={previewQuery.isLoading} />
          </div>
        </div>
      </section>

      {/* ============ TRUST STRIP — full-width, charcoal ============ */}
      <section className="pub-band pub-band-charcoal pub-strip-band">
        <div className="pub-container pub-strip">
          <div><TrendingUp size={16} /> Real completed sales, not asking prices</div>
          <div><ShieldCheck size={16} /> No guessing — an honest "not enough data" beats a fake one</div>
          <div><Zap size={16} /> A simple BUY / WAIT / AVOID answer, evidence one tap away</div>
          <div><Bot size={16} /> Bring it to your Discord server automatically</div>
        </div>
      </section>

      {/* ============ TWO PRODUCT PATHS — full-width, black ============ */}
      <section id="how" className="pub-band pub-band-black">
        <div className="pub-container pub-section">
          <div className="pub-section-head"><span>ONE ENGINE, TWO WAYS TO USE IT</span><h2>Trade it yourself, or run it in your server</h2></div>
          <div className="pub-paths">
            <div className="pub-path-card">
              <div className="pub-path-icon"><Users size={20} /></div>
              <h3>For individual traders</h3>
              <p>Search any card and see what it's really worth, complete sales history included. Get buy and sell targets, track cards on a watchlist, log trades in a portfolio, and see the full live opportunities feed with Elite.</p>
              <ul className="pub-path-list">
                <li><CheckCircle2 size={14} /> Fair value & completed sales on every card</li>
                <li><CheckCircle2 size={14} /> Buy-below and sell-target prices</li>
                <li><CheckCircle2 size={14} /> Watchlist, alerts and portfolio tracking</li>
                <li><CheckCircle2 size={14} /> Full live opportunities feed on Elite</li>
              </ul>
              <a href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={14} /></a>
            </div>
            <div className="pub-path-connector" aria-hidden="true"><span /><em>+</em><span /></div>
            <div className="pub-path-card">
              <div className="pub-path-icon"><Server size={20} /></div>
              <h3>For Discord communities</h3>
              <p>Add the EAFC Live bot to your server, choose a trading channel, and it posts branded trading opportunities automatically — with the platform, budget and posting-limit mix you configure.</p>
              <ul className="pub-path-list">
                <li><CheckCircle2 size={14} /> Add the bot, run <code>/feed setup</code></li>
                <li><CheckCircle2 size={14} /> Pick a channel, platform and budget filters</li>
                <li><CheckCircle2 size={14} /> Set a daily posting limit for your plan</li>
                <li><CheckCircle2 size={14} /> Members get regular, useful trading content</li>
              </ul>
              <button type="button" onClick={scrollToDiscordPricing}>See server plans <ArrowRight size={14} /></button>
            </div>
          </div>
        </div>
      </section>

      {/* ============ LIVE PRODUCT DEMONSTRATION — full-width, charcoal ============ */}
      <section ref={oppRef} id="opportunities" className="pub-band pub-band-charcoal">
        <div className="pub-container pub-section">
          <div className="pub-section-head"><span>LIVE OPPORTUNITIES</span><h2>This is what EAFC Live finds</h2></div>
          <p className="pub-section-lead">
            A realistic look at the opportunities feed Elite members see — player, current price, fair
            value, buy target, sell target, expected profit, confidence and risk, all in one row.
          </p>
          <DemoTradePanel rows={DEMO_ROWS} title="Live opportunities feed" subtitle="Illustrative — real feed updates continuously" />
          <div className="pub-preview-wide">
            <LockedOpportunityCard item={previewItem} loading={previewQuery.isLoading} />
          </div>
          <a className="pub-see-all" href={`${APP_ORIGIN}/#/signup`}>Create free account to see today's real opportunities <ArrowRight size={15} /></a>
        </div>
      </section>

      {/* ============ DISCORD FEED DEMONSTRATION — full-width, black ============ */}
      <section className="pub-band pub-band-black">
        <div className="pub-container pub-section pub-discord-demo">
          <div className="pub-discord-demo-copy">
            <div className="pub-section-head"><span>DISCORD SERVER FEED</span><h2>What your members actually see</h2></div>
            <p className="pub-section-lead">
              Once a server's feed is set up, opportunities land straight in your chosen channel —
              no one has to open the app.
            </p>
            <ol className="pub-how-steps pub-how-steps-stack">
              <li><b>1</b><div><strong>Invite the bot</strong><p>Add EAFC Live to your server (you'll need Manage Server).</p></div></li>
              <li><b>2</b><div><strong>Run /feed setup</strong><p>Pick a plan, a posting channel, and the platform and budget mix you want.</p></div></li>
              <li><b>3</b><div><strong>Choose channel &amp; preferences</strong><p>Set posting limits and filters — the bot handles the rest automatically.</p></div></li>
              <li><b>4</b><div><strong>Opportunities post automatically</strong><p>Buy calls land in that channel on their own, up to your plan's daily limit.</p></div></li>
            </ol>
            <button type="button" className="pub-secondary" onClick={scrollToDiscordPricing}><Server size={15} /> See server plans</button>
          </div>
          <DiscordFeedMock />
        </div>
      </section>

      {/* ============ WHY PEOPLE USE IT — full-width, LIGHT ============ */}
      <section id="trust" className="pub-band pub-band-light">
        <div className="pub-container pub-section">
          <div className="pub-section-head light"><span>WHY PEOPLE USE IT</span><h2>What EAFC Live actually does</h2></div>
          <div className="pub-why-grid">
            {WHY_ITEMS.map((item) => (
              <div key={item.title} className="pub-why-item">
                <item.icon size={18} />
                <strong>{item.title}</strong>
                <p>{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============ PROOF — full-width, charcoal ============ */}
      <section className="pub-band pub-band-charcoal">
        <div className="pub-container pub-section">
          <div className="pub-proof-grid">
            {cardsTracked ? (
              <div className="pub-proof-stat"><strong>{count(cardsTracked)}</strong><span>Cards priced right now</span></div>
            ) : null}
            <div className="pub-proof-stat"><strong>Live</strong><span>Prices &amp; completed sales, monitored continuously</span></div>
            <div className="pub-proof-stat"><strong>1</strong><span>Real opportunity shown below, unlocked with a free account</span></div>
          </div>
          <PublicTrackRecord />
        </div>
      </section>

      {/* ============ SIMPLE TO ADVANCED — full-width, black ============ */}
      <section className="pub-band pub-band-black">
        <div className="pub-container pub-section">
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
        </div>
      </section>

      {/* ============ PRICING — full-width, LIGHT ============ */}
      <section id="pricing" className="pub-band pub-band-light">
        <div className="pub-container pub-section">
          <div className="pub-section-head light">
            <span>PERSONAL &middot; FREE VS {PAID_PLAN_NAME.toUpperCase()}</span>
            <h2>Start free — upgrade if EAFC Live is worth it to you</h2>
          </div>
          <p className="pub-section-lead light">
            The free account is free permanently, not a trial. No card needed to create one.
          </p>
          <PublicPlanTable />
        </div>
      </section>

      {/* ============ DISCORD PRICING — full-width, charcoal ============ */}
      <section id="discord-pricing" className="pub-band pub-band-charcoal">
        <div className="pub-container pub-section">
          <div className="pub-section-head">
            <span>DISCORD SERVERS &middot; A SEPARATE PLAN</span>
            <h2>Give your server its own live trading feed</h2>
          </div>
          <p className="pub-section-lead">
            Billed per server, separately from any member's personal account. {PAID_PLAN_NAME} does not
            include a server feed, and a server feed does not give its members {PAID_PLAN_NAME} — they're
            two different products that work well together.
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
              <span className="pub-pricing-ribbon">Most posts</span>
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
        </div>
      </section>

      {/* ============ FAQ — full-width, black ============ */}
      <section id="faq" className="pub-band pub-band-black">
        <div className="pub-container pub-section">
          <div className="pub-section-head"><span>FAQ</span><h2>Common questions</h2></div>
          <dl className="pub-faq">
            <div>
              <dt>What does EAFC Live actually do?</dt>
              <dd>It compares a card's live listed price against real completed sales and a computed fair value, then gives a simple BUY, WAIT or AVOID call with a target entry and sell price and the reasoning behind it.</dd>
            </div>
            <div>
              <dt>Is profit guaranteed?</dt>
              <dd>No. Every call is an estimate built from live market data, not a guarantee. Prices move, and EA takes 5% tax on every sale — that's already factored into the profit figures shown.</dd>
            </div>
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
              <dt>How does the Discord feed work?</dt>
              <dd>Invite the bot to your server, run <code>/feed setup</code> (you need Manage Server), pick a plan, posting channel and platform/budget preferences. EAFC Live then posts opportunities into that channel automatically, up to your plan's daily limit.</dd>
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
        </div>
      </section>

      {/* ============ FINAL CTA — full-width, accent banner ============ */}
      <section className="pub-band pub-band-accent">
        <div className="pub-container pub-midcta">
          <div className="pub-midcta-block">
            <Users size={22} />
            <h2>Trade it yourself</h2>
            <p>Free permanently, no card required. Two complete picks every day.</p>
            <a className="pub-cta" href={`${APP_ORIGIN}/#/signup`}>Create free account <ArrowRight size={16} /></a>
          </div>
          <div className="pub-midcta-divider" aria-hidden="true" />
          <div className="pub-midcta-block">
            <Server size={22} />
            <h2>Run it in your server</h2>
            <p>From £9.99/month, posted straight to a channel your members already use.</p>
            <a className="pub-cta pub-cta-outline" href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Set up a Discord server feed <ArrowRight size={16} /></a>
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
          <div className="pub-footer-top">
            <div className="pub-container pub-footer-cols">
              <div className="pub-footer-col pub-footer-brand-col">
                <span className="pub-footer-brand">
                  <img src="/icons/icon-192x192.png" alt="" width="28" height="28" />
                  EAFC <b>Live</b>
                </span>
                <p>Live prices vs completed sales, for your own trading or a Discord server's feed.</p>
              </div>
              <div className="pub-footer-col">
                <span className="pub-footer-heading">Product</span>
                <button type="button" onClick={() => scrollToPublicSection("how")}>How it works</button>
                <button type="button" onClick={() => scrollToPublicSection("trust")}>Features</button>
                <button type="button" onClick={() => scrollToPublicSection("opportunities")}>Live opportunities</button>
                <button type="button" onClick={() => scrollToPublicSection("faq")}>FAQ</button>
              </div>
              <div className="pub-footer-col">
                <span className="pub-footer-heading">Pricing</span>
                <button type="button" onClick={() => scrollToPublicSection("pricing")}>Personal pricing</button>
                <button type="button" onClick={() => scrollToPublicSection("discord-pricing")}>Discord server plans</button>
                <a href={`${APP_ORIGIN}/#/billing`}>Billing</a>
              </div>
              <div className="pub-footer-col">
                <span className="pub-footer-heading">Account</span>
                <a href={`${APP_ORIGIN}/#/signup`}>Create free account</a>
                <a href={`${APP_ORIGIN}/#/login`}>Log in</a>
                <a href={`${APP_ORIGIN}/#/discord-feed`}>Manage Server Feed</a>
              </div>
              <div className="pub-footer-col">
                <span className="pub-footer-heading">Support &amp; legal</span>
                <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Discord</a>
                <a href="mailto:support@eafc.live">support@eafc.live</a>
                <Link to="/privacy">Privacy</Link>
                <Link to="/terms">Terms</Link>
              </div>
            </div>
          </div>
          <div className="pub-footer-bottom">
            <div className="pub-container pub-footer-bottom-inner">
              <span>© {new Date().getFullYear()} EAFC Live</span>
              <span className="pub-footer-bottom-note">Not affiliated with EA Sports or FC. Trading involves risk — profit is never guaranteed.</span>
            </div>
          </div>
        </footer>
      </div>
    </HashRouter>
  );
}
