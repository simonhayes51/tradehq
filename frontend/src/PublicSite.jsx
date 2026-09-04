import { useEffect, useState } from "react";
import { HashRouter, Link, Route, Routes } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Check,
  Menu,
  Search,
  Server,
  ShieldCheck,
  Smartphone,
  X,
  Zap,
} from "lucide-react";
import { useBillingPlans, headlinePrice } from "./v2/hooks/useBillingPlans";
import { PAID_PLAN_NAME, FREE_LIMITS } from "./v2/lib/limits";
import { APP_ORIGIN, DISCORD_INVITE_URL } from "./lib/domains";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import api from "./axios";
import "./v2/styles/tokens.css";
import "./styles/public-site.css";

const APP_SIGNUP = `${APP_ORIGIN}/#/signup`;
const APP_LOGIN = `${APP_ORIGIN}/#/login`;

const personalFeatures = [
  "Live opportunities with clear buy targets",
  "Expected profit after EA tax",
  "Confidence, risk and selling speed",
  "Trade Finder filters for your budget",
  "Watchlists, alerts and portfolio tracking",
];

const serverFeatures = [
  "Automatic branded opportunities",
  "Choose the posting channel",
  "Set platform and budget preferences",
  "Owner-only setup and billing controls",
];

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function Brand() {
  return (
    <Link to="/" className="pub-brand" aria-label="EAFC.Live home">
      <img src="/icons/icon-192x192.png" alt="" width="38" height="38" />
      <span>EAFC.<b>Live</b></span>
    </Link>
  );
}

function PublicNav() {
  const [open, setOpen] = useState(false);
  const links = [
    ["Product", "product"],
    ["How it works", "how"],
    ["Discord", "discord"],
    ["Pricing", "pricing"],
  ];

  const go = (id) => {
    setOpen(false);
    scrollToId(id);
  };

  return (
    <header className="pub-nav">
      <div className="pub-container pub-nav-inner">
        <Brand />
        <nav className="pub-nav-links" aria-label="Main navigation">
          {links.map(([label, id]) => (
            <button key={id} type="button" onClick={() => go(id)}>{label}</button>
          ))}
          <a href={APP_LOGIN} className="pub-login">Log in</a>
          <a href={APP_SIGNUP} className="pub-button pub-button-small">Start free</a>
        </nav>
        <button
          type="button"
          className="pub-menu"
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>
      {open && (
        <div className="pub-mobile-nav">
          {links.map(([label, id]) => (
            <button key={id} type="button" onClick={() => go(id)}>{label}</button>
          ))}
          <a href={APP_LOGIN}>Log in</a>
          <a href={APP_SIGNUP} className="pub-button">Start free</a>
        </div>
      )}
    </header>
  );
}

function ProductImage({ src, alt, className = "" }) {
  return (
    <div className={`pub-product-image ${className}`.trim()}>
      <img src={src} alt={alt} loading="lazy" />
    </div>
  );
}

function Hero() {
  return (
    <section className="pub-hero">
      <div className="pub-glow pub-glow-one" />
      <div className="pub-container pub-hero-inner">
        <div className="pub-hero-copy">
          <div className="pub-eyebrow"><span /> LIVE EA FC MARKET INTELLIGENCE</div>
          <h1>Know what to buy.<br /><em>Know when to sell.</em></h1>
          <p>
            EAFC.Live turns live prices and completed sales into clear trading opportunities.
            See the buy price, expected profit, risk and reasoning before you spend your coins.
          </p>
          <div className="pub-hero-actions">
            <a href={APP_SIGNUP} className="pub-button">Create free account <ArrowRight size={17} /></a>
            <button type="button" className="pub-button pub-button-ghost" onClick={() => scrollToId("product")}>
              See the product
            </button>
          </div>
          <div className="pub-hero-note">
            <Check size={15} /> Free permanently
            <span />
            <Check size={15} /> No card required
          </div>
        </div>
        <div className="pub-hero-visual">
          <img src="/marketing/eafc-dashboard.svg" alt="EAFC.Live dashboard showing a live Daniel Muñoz trading opportunity" />
        </div>
      </div>
      <div className="pub-container pub-signal-strip" aria-label="Product highlights">
        <div><strong>15k+</strong><span>cards priced</span></div>
        <div><strong>Live</strong><span>market updates</span></div>
        <div><strong>After tax</strong><span>profit calculations</span></div>
        <div><strong>Console + PC</strong><span>market support</span></div>
      </div>
    </section>
  );
}

function ProofBand() {
  const proof = [
    { icon: Search, title: "Not guesswork", text: "Calls are checked against recent completed sales and fair value before they surface." },
    { icon: Activity, title: "Actionable numbers", text: "Every move shows maximum buy, expected profit after EA tax, risk and confidence." },
    { icon: Bot, title: "Built beyond the app", text: "Server feeds can post the best opportunities straight into a Discord trading community." },
  ];

  return (
    <section className="pub-proof" aria-label="EAFC.Live proof points">
      <div className="pub-container pub-proof-inner">
        <div className="pub-proof-lead">
          <img src="/icons/icon-192x192.png" alt="" width="62" height="62" />
          <div>
            <span>WHY IT FEELS DIFFERENT</span>
            <h2>Actual market evidence, turned into a clear move.</h2>
          </div>
        </div>
        <div className="pub-proof-grid">
          {proof.map(({ icon: Icon, title, text }) => (
            <article key={title}>
              <Icon size={20} />
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function FeatureIntro() {
  const cards = [
    { icon: Activity, title: "Real sale data", text: "Recommendations use completed sales, not inflated asking prices." },
    { icon: BarChart3, title: "Clear numbers", text: "See the maximum entry, sell target and expected return after EA tax." },
    { icon: ShieldCheck, title: "Risk explained", text: "Every opportunity includes confidence, risk and selling speed." },
    { icon: Bell, title: "Always watching", text: "Track cards and get alerts when the market reaches your price." },
  ];

  return (
    <section id="product" className="pub-section pub-section-soft">
      <div className="pub-container">
        <div className="pub-section-heading pub-section-heading-center">
          <span>THE PRODUCT</span>
          <h2>Trading decisions without the guesswork</h2>
          <p>Everything is built to answer three questions: what to buy, what to pay and what you could make.</p>
        </div>
        <div className="pub-feature-grid">
          {cards.map(({ icon: Icon, title, text }) => (
            <article key={title}>
              <div className="pub-icon"><Icon size={19} /></div>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function MobileProduct() {
  return (
    <section className="pub-section pub-mobile-product">
      <div className="pub-container pub-split">
        <div className="pub-phone-stage">
          <div className="pub-phone-stack" aria-label="EAFC.Live mobile experience">
            <ProductImage
              src="/marketing/eafc-mobile-home.svg"
              alt="EAFC.Live mobile home screen showing a Harry Kane opportunity"
              className="pub-phone pub-phone-back"
            />
            <ProductImage
              src="/marketing/eafc-mobile-finder.svg"
              alt="EAFC.Live mobile Trade Finder showing matching opportunities"
              className="pub-phone pub-phone-front"
            />
          </div>
        </div>
        <div className="pub-split-copy">
          <div className="pub-eyebrow"><Smartphone size={14} /> BUILT FOR THE WAY YOU TRADE</div>
          <h2>Your next move, clear at a glance</h2>
          <p>
            Open EAFC.Live on any device and see the opportunity, entry price, expected profit and
            confidence in one place. No spreadsheets and no digging through price pages.
          </p>
          <ul className="pub-check-list">
            <li><Check size={16} /> Top opportunities surfaced automatically</li>
            <li><Check size={16} /> Trade Finder sorted around your budget</li>
            <li><Check size={16} /> Live data age shown on every call</li>
            <li><Check size={16} /> Watch or act in a couple of taps</li>
          </ul>
          <a href={APP_SIGNUP} className="pub-text-link">Start finding trades <ArrowRight size={16} /></a>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { icon: Search, number: "01", title: "We watch the market", text: "Live prices are compared against recent completed sales and calculated fair value." },
    { icon: Zap, number: "02", title: "The engine finds the edge", text: "Cards are checked for realistic profit, confidence, demand and selling risk." },
    { icon: BarChart3, number: "03", title: "You get the move", text: "See a maximum buy price, expected return and clear explanation of the opportunity." },
  ];

  return (
    <section id="how" className="pub-section pub-how">
      <div className="pub-container pub-how-inner">
        <div className="pub-section-heading">
          <span>HOW IT WORKS</span>
          <h2>From market noise to one clear move</h2>
          <p>EAFC.Live does the boring market checks, then gives you the part that matters.</p>
        </div>
        <div className="pub-steps">
          {steps.map(({ icon: Icon, number, title, text }) => (
            <article key={number}>
              <div className="pub-step-top"><span>{number}</span><Icon size={21} /></div>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function DiscordSection() {
  return (
    <section id="discord" className="pub-section pub-discord">
      <div className="pub-glow pub-glow-two" />
      <div className="pub-container pub-split pub-discord-inner">
        <div className="pub-split-copy">
          <div className="pub-eyebrow"><Bot size={14} /> DISCORD SERVER FEEDS</div>
          <h2>Give your community useful trading content automatically</h2>
          <p>
            Add the EAFC.Live bot, choose a channel and set your preferences. The best matching
            opportunities are posted straight into your server without anyone babysitting the feed.
          </p>
          <ul className="pub-check-list">
            {serverFeatures.map((feature) => <li key={feature}><Check size={16} /> {feature}</li>)}
          </ul>
          <div className="pub-inline-actions">
            <a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer" className="pub-button">
              Add to Discord <ArrowRight size={16} />
            </a>
            <button type="button" className="pub-text-link" onClick={() => scrollToId("server-pricing")}>See server pricing</button>
          </div>
        </div>
        <div className="pub-discord-showcase">
          <ProductImage
            src="/marketing/eafc-discord.svg"
            alt="EAFC.Live Discord bot posting a live Daniel Muñoz trading opportunity"
            className="pub-discord-image"
          />
        </div>
      </div>
    </section>
  );
}

function PlanCard({ name, price, suffix, description, features, action, href, featured = false }) {
  return (
    <article className={`pub-plan${featured ? " pub-plan-featured" : ""}`}>
      {featured && <span className="pub-plan-badge">MOST POPULAR</span>}
      <h3>{name}</h3>
      <p className="pub-plan-desc">{description}</p>
      <div className="pub-price"><strong>{price}</strong>{suffix && <span>{suffix}</span>}</div>
      <ul>
        {features.map((feature) => <li key={feature}><Check size={15} /> {feature}</li>)}
      </ul>
      <a href={href} className={featured ? "pub-button" : "pub-button pub-button-ghost"}>{action} <ArrowRight size={15} /></a>
    </article>
  );
}

function Pricing() {
  const plans = useBillingPlans();
  const monthly = headlinePrice(plans.data, "monthly") || "£3/month";
  const season = headlinePrice(plans.data, "season");

  return (
    <section id="pricing" className="pub-section pub-pricing">
      <div className="pub-container">
        <div className="pub-section-heading pub-section-heading-center">
          <span>PRICING</span>
          <h2>Start free. Upgrade when it earns its place.</h2>
          <p>Personal plans and Discord server feeds are separate. Pick only what you need.</p>
        </div>
        <div className="pub-pricing-group pub-personal-pricing">
          <div className="pub-pricing-title"><Smartphone size={18} /><div><h3>For your own trading</h3><p>Your personal EAFC.Live account.</p></div></div>
          <div className="pub-plan-grid">
            <PlanCard
              name="Free"
              price="£0"
              suffix="forever"
              description="Try real opportunities and the core tools without entering card details."
              features={[
                `${FREE_LIMITS.dailyOpportunities} complete opportunities each day`,
                `${FREE_LIMITS.watchlist}-card watchlist`,
                "Player search and market data",
                "Basic results history",
              ]}
              action="Create free account"
              href={APP_SIGNUP}
            />
            <PlanCard
              name={PAID_PLAN_NAME}
              price={monthly}
              suffix={season ? `or ${season}` : null}
              description="For traders who want the full live feed and every tool unlocked."
              features={["Full live opportunities feed", ...personalFeatures.slice(1)]}
              action={`Get ${PAID_PLAN_NAME}`}
              href={`${APP_ORIGIN}/#/billing`}
              featured
            />
          </div>
        </div>
        <div id="server-pricing" className="pub-pricing-group pub-server-pricing">
          <div className="pub-pricing-title"><Server size={18} /><div><h3>For Discord communities</h3><p>A separate subscription attached to one server.</p></div></div>
          <div className="pub-plan-grid">
            <PlanCard
              name="Server Feed"
              price="£9.99"
              suffix="per server / month"
              description="A steady feed for smaller or casual trading communities."
              features={["Up to 6 posts per day", ...serverFeatures.slice(1)]}
              action="Set up server feed"
              href={DISCORD_INVITE_URL}
            />
            <PlanCard
              name="Server Feed+"
              price="£19.99"
              suffix="per server / month"
              description="More daily opportunities for larger and more active servers."
              features={["Up to 12 posts per day", ...serverFeatures.slice(1)]}
              action="Set up Server Feed+"
              href={DISCORD_INVITE_URL}
              featured
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function FAQ() {
  const items = [
    ["Is profit guaranteed?", "No. EAFC.Live uses live market evidence to find potential opportunities, but prices can always move. Expected profit already accounts for EA's 5% sale tax."],
    ["What do I get for free?", `Free accounts get ${FREE_LIMITS.dailyOpportunities} complete opportunities per day, player search, a ${FREE_LIMITS.watchlist}-card watchlist and core market tools.`],
    ["Does Elite include a Discord server feed?", "No. Elite is for your personal account. Server feeds are separate because they post into a whole Discord community."],
    ["Which markets are supported?", "Console and PC are tracked separately. PlayStation and Xbox share the console transfer market."],
  ];
  return (
    <section className="pub-section pub-faq-section">
      <div className="pub-container pub-faq-wrap">
        <div className="pub-section-heading"><span>FAQ</span><h2>Straight answers</h2></div>
        <div className="pub-faq">
          {items.map(([question, answer]) => <details key={question}><summary>{question}</summary><p>{answer}</p></details>)}
        </div>
      </div>
    </section>
  );
}

function FinalCTA() {
  return (
    <section className="pub-final">
      <div className="pub-container pub-final-inner">
        <div><span>READY WHEN YOU ARE</span><h2>Make your next trade with better information.</h2></div>
        <a href={APP_SIGNUP} className="pub-button pub-button-light">Create free account <ArrowRight size={17} /></a>
      </div>
    </section>
  );
}

function PublicHome() {
  return <main><Hero /><ProofBand /><FeatureIntro /><MobileProduct /><HowItWorks /><DiscordSection /><Pricing /><FAQ /><FinalCTA /></main>;
}

function ForwardToApp() {
  useEffect(() => { window.location.replace(`${APP_ORIGIN}/${window.location.hash}`); }, []);
  return <main className="pub-forward">Taking you to the app...</main>;
}

function Footer() {
  return (
    <footer className="pub-footer">
      <div className="pub-container pub-footer-inner">
        <div className="pub-footer-brand"><Brand /><p>Live EA FC market intelligence for traders and Discord communities.</p></div>
        <div><strong>Product</strong><button type="button" onClick={() => scrollToId("product")}>Features</button><button type="button" onClick={() => scrollToId("pricing")}>Pricing</button><a href={APP_SIGNUP}>Create account</a></div>
        <div><strong>Community</strong><a href={DISCORD_INVITE_URL} target="_blank" rel="noreferrer">Discord</a><button type="button" onClick={() => scrollToId("server-pricing")}>Server feeds</button><a href="mailto:support@eafc.live">Support</a></div>
        <div><strong>Legal</strong><Link to="/privacy">Privacy</Link><Link to="/terms">Terms</Link></div>
      </div>
      <div className="pub-container pub-footer-bottom"><span>© {new Date().getFullYear()} EAFC.Live</span><span>Not affiliated with EA Sports or EA Sports FC. Trading involves risk.</span></div>
    </footer>
  );
}

export default function PublicSite() {
  useEffect(() => {
    const match = window.location.pathname.match(/^\/r\/([A-Za-z0-9_-]{3,64})\/?$/);
    const code = match?.[1] || new URLSearchParams(window.location.search).get("ref");
    if (!code) return;
    api.post(`/api/referrals/track-click?code=${encodeURIComponent(code)}`, null, { __skipAuthRedirect: true }).catch(() => null);
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
        <Footer />
      </div>
    </HashRouter>
  );
}
