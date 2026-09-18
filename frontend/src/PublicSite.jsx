import { useEffect, useState } from "react";
import { HashRouter, Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
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
import { useTrackRecord } from "./v2/hooks/useTrackRecord";
import { PAID_PLAN_NAME, FREE_LIMITS } from "./v2/lib/limits";
import { APP_ORIGIN, DISCORD_BOT_INSTALL_URL, DISCORD_INVITE_URL } from "./lib/domains";
import Privacy from "./pages/Privacy";
import Terms from "./pages/Terms";
import { DiscordCommandsGuide, DiscordServerFeedGuide, GettingStartedGuide, GuidesHome } from "./pages/Guides";
import api from "./axios";
import "./v2/styles/tokens.css";
import "./styles/public-site.css";

const APP_SIGNUP = `${APP_ORIGIN}/#/signup`;
const APP_LOGIN = `${APP_ORIGIN}/#/login`;
const APP_SIGNUP_FOR_ELITE = `${APP_SIGNUP}?from=${encodeURIComponent("/billing?unlock=elite")}`;

const personalFeatures = [
  "Live trades with clear buy prices",
  "Profit shown after EA tax",
  "Confidence, risk and expected selling speed",
  "Trade Finder filters to match your budget",
  "Watchlists, alerts and portfolio tracking",
];

const serverFeatures = [
  "Branded trade posts sent automatically",
  "Choose which channel they appear in",
  "Set your platform and budget",
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
  const location = useLocation();
  const navigate = useNavigate();
  const links = [
    ["Product", "product"],
    ["How it works", "how"],
    ["Discord", "discord"],
    ["Pricing", "pricing"],
  ];

  const go = (id) => {
    setOpen(false);
    if (location.pathname === "/") {
      scrollToId(id);
      return;
    }
    navigate("/", { state: { scrollTo: id } });
  };

  return (
    <header className="pub-nav">
      <div className="pub-container pub-nav-inner">
        <Brand />
        <nav className="pub-nav-links" aria-label="Main navigation">
          {links.map(([label, id]) => (
            <button key={id} type="button" onClick={() => go(id)}>{label}</button>
          ))}
          <Link to="/guides" className="pub-login">Guides</Link>
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
          <Link to="/guides">Guides</Link>
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
            EAFC.Live uses live prices and completed sales to find potential trades.
            See what to pay, what you could make after tax and how risky it looks before spending your coins.
          </p>
          <div className="pub-hero-actions">
            <a href={APP_SIGNUP} className="pub-button">Create free account <ArrowRight size={17} /></a>
            <button type="button" className="pub-button pub-button-ghost" onClick={() => scrollToId("product")}>
              See how it works
            </button>
          </div>
          <div className="pub-hero-note">
            <Check size={15} /> Free forever
            <span />
            <Check size={15} /> No card required
          </div>
        </div>
        <div className="pub-hero-visual">
          <div className="pub-hero-product-label">
            <img src="/icons/icon-192x192.png" alt="" />
            <div><strong>EAFC.<b>Live</b></strong><span>LIVE MARKET INTELLIGENCE</span></div>
            <em><i /> SCANNING</em>
          </div>
          <img src="/marketing/eafc-dashboard.svg" alt="EAFC.Live showing a live trade with buy price, expected profit and confidence" />
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
    { icon: Search, title: "Based on real sales", text: "Prices are checked against recent completed sales instead of relying on inflated listings." },
    { icon: Activity, title: "Know your numbers", text: "Every trade shows the maximum buy price, expected profit after EA tax, risk and confidence." },
    { icon: Bot, title: "Made for Discord too", text: "Server feeds can send selected trades straight into your Discord community." },
  ];

  return (
    <section className="pub-proof" aria-label="EAFC.Live proof points">
      <div className="pub-container pub-proof-inner">
        <div className="pub-proof-lead">
          <img src="/icons/icon-192x192.png" alt="" width="62" height="62" />
          <div>
            <span>WHY TRADERS USE IT</span>
            <h2>Real sales data. Useful numbers. Better decisions.</h2>
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
    { icon: Activity, title: "Real sale data", text: "Trade suggestions use completed sales, not inflated asking prices." },
    { icon: BarChart3, title: "The numbers that matter", text: "See the maximum buy price, sell target and expected return after EA tax." },
    { icon: ShieldCheck, title: "Risk explained", text: "Each trade includes a confidence score, risk level and expected selling speed." },
    { icon: Bell, title: "Price alerts", text: "Track cards and get an alert when the market reaches your chosen price." },
  ];

  return (
    <section id="product" className="pub-section pub-section-soft">
      <div className="pub-container">
        <div className="pub-section-heading pub-section-heading-center">
          <span>THE PRODUCT</span>
          <h2>Find trades without doing all the digging</h2>
          <p>Quickly see what to buy, what to pay and how much profit could be available.</p>
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
          <h2>All the key numbers in one place</h2>
          <p>
            Open EAFC.Live on any device and see the opportunity, entry price, expected profit and
            confidence in one place. No spreadsheets. No jumping between price pages.
          </p>
          <ul className="pub-check-list">
            <li><Check size={16} /> Best current trades shown first</li>
            <li><Check size={16} /> Trade Finder matched to your budget</li>
            <li><Check size={16} /> See how fresh the data is on every trade</li>
            <li><Check size={16} /> Add to your watchlist or act in a few taps</li>
          </ul>
          <a href={APP_SIGNUP} className="pub-text-link">Find your next trade <ArrowRight size={16} /></a>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  const steps = [
    { icon: Search, number: "01", title: "We track live prices", text: "Current prices are compared with recent completed sales and calculated fair value." },
    { icon: Zap, number: "02", title: "We look for value", text: "Each card is checked for realistic profit, confidence, demand and selling risk." },
    { icon: BarChart3, number: "03", title: "You see the trade", text: "You get a maximum buy price, expected return and a simple explanation of why it stands out." },
  ];

  return (
    <section id="how" className="pub-section pub-how">
      <div className="pub-container pub-how-inner">
        <div className="pub-section-heading">
          <span>HOW IT WORKS</span>
          <h2>We check the market. You see what stands out.</h2>
          <p>EAFC.Live handles the price checking and gives you the information you actually need.</p>
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
          <h2>Put useful trading posts straight into your Discord</h2>
          <p>
            Add the EAFC.Live bot, choose a channel and set your preferences. EAFC.Live then sends
            the best matching trades straight into your server automatically.
          </p>
          <ul className="pub-check-list">
            {serverFeatures.map((feature) => <li key={feature}><Check size={16} /> {feature}</li>)}
          </ul>
          <div className="pub-inline-actions">
            <a href={DISCORD_BOT_INSTALL_URL} target="_blank" rel="noreferrer" className="pub-button">
              Add to Discord <ArrowRight size={16} />
            </a>
            <Link className="pub-text-link" to="/guides/discord-server-feed">Setup guide</Link>
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
      <div className="pub-price"><strong>{price || "Pricing unavailable"}</strong>{suffix && <span>{suffix}</span>}</div>
      <ul>
        {features.map((feature) => <li key={feature}><Check size={15} /> {feature}</li>)}
      </ul>
      <a href={href} className={featured ? "pub-button" : "pub-button pub-button-ghost"}>{action} <ArrowRight size={15} /></a>
    </article>
  );
}

function Pricing() {
  const plans = useBillingPlans();
  const monthly = headlinePrice(plans.data, "monthly");
  const season = headlinePrice(plans.data, "season");

  return (
    <section id="pricing" className="pub-section pub-pricing">
      <div className="pub-container">
        <div className="pub-section-heading pub-section-heading-center">
          <span>PRICING</span>
          <h2>Start free. Upgrade when you need more.</h2>
          <p>Personal plans and Discord server feeds are separate, so you only pay for what you need.</p>
        </div>
        <div className="pub-pricing-group pub-personal-pricing">
          <div className="pub-pricing-title"><Smartphone size={18} /><div><h3>For your own trading</h3><p>Your personal EAFC.Live account.</p></div></div>
          <div className="pub-plan-grid">
            <PlanCard
              name="Free"
              price="£0"
              suffix="forever"
              description="Free permanently, not a trial. Try the core tools and see real trades without entering any card details."
              features={[
                `${FREE_LIMITS.dailyOpportunities} full trade suggestions each day`,
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
              description="Get the full live feed, higher limits and access to every trading tool."
              features={["Full live trade feed", ...personalFeatures.slice(1)]}
              action={`Create account for ${PAID_PLAN_NAME}`}
              href={APP_SIGNUP_FOR_ELITE}
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
              description="Up to six selected trades each day for smaller or casual trading communities."
              features={["Up to 6 posts per day", ...serverFeatures.slice(1)]}
              action="Set up server feed"
              href={DISCORD_BOT_INSTALL_URL}
            />
            <PlanCard
              name="Server Feed+"
              price="£19.99"
              suffix="per server / month"
              description="More daily trade posts for larger and more active Discord servers."
              features={["Up to 15 posts per day", ...serverFeatures.slice(1)]}
              action="Set up Server Feed+"
              href={DISCORD_BOT_INSTALL_URL}
              featured
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function PublicTrackRecord() {
  const query = useTrackRecord("all");
  const published = query.data?.published;
  if (!published?.has_published_data || !published?.has_enough_data) return null;

  const targetHits = published.target_hits ?? published.wins;
  const targetMisses = published.target_not_reached ?? published.losses;
  const targetHitRate = published.target_hit_rate_pct ?? published.win_rate_pct;

  const stats = [
    ["Calls published", published.published_opportunities],
    ["Targets hit", targetHits],
    ["Targets not reached", targetMisses],
    ["Target hit rate", targetHitRate == null ? null : `${targetHitRate}%`],
  ].filter(([, value]) => value !== null && value !== undefined);

  return (
    <section className="pub-section pub-section-soft" aria-label="Published track record">
      <div className="pub-container">
        <div className="pub-section-heading pub-section-heading-center">
          <span>TRACK RECORD</span>
          <h2>How the calls have actually done</h2>
          <p>
            FC{query.data?.game_year || published.game_year || 27} recommendations genuinely published to members.
            Targets not reached are shown neutrally, not presented as user losses.
          </p>
        </div>
        <div className="pub-feature-grid">
          {stats.map(([label, value]) => (
            <article key={label}>
              <div className="pub-icon"><BarChart3 size={19} /></div>
              <h3>{value}</h3>
              <p>{label}</p>
            </article>
          ))}
        </div>
        <div className="pub-inline-actions" style={{ justifyContent: "center", marginTop: 24 }}>
          <a href={APP_SIGNUP} className="pub-button">Create free account to see the record <ArrowRight size={16} /></a>
        </div>
      </div>
    </section>
  );
}

function FAQ() {
  const items = [
    ["Is profit guaranteed?", "No. EAFC.Live uses live prices and recent completed sales to find potential trades, but the market can always move. Expected profit already accounts for EA's 5% sale tax."],
    ["What do I get for free?", `Free accounts get ${FREE_LIMITS.dailyOpportunities} full trade suggestions per day, player search, a ${FREE_LIMITS.watchlist}-card watchlist and core market tools.`],
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
        <div><span>READY WHEN YOU ARE</span><h2>Make your next trade with the numbers in front of you.</h2></div>
        <a href={APP_SIGNUP} className="pub-button pub-button-light">Create free account <ArrowRight size={17} /></a>
      </div>
    </section>
  );
}

function PublicHome() {
  const location = useLocation();
  useEffect(() => {
    if (!location.state?.scrollTo) return;
    requestAnimationFrame(() => scrollToId(location.state.scrollTo));
  }, [location.state]);
  return <main><Hero /><ProofBand /><FeatureIntro /><MobileProduct /><HowItWorks /><DiscordSection /><PublicTrackRecord /><Pricing /><FAQ /><FinalCTA /></main>;
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
        <div><strong>Product</strong><button type="button" onClick={() => scrollToId("product")}>Features</button><button type="button" onClick={() => scrollToId("pricing")}>Pricing</button><Link to="/guides">Guides</Link><a href={APP_SIGNUP}>Create account</a></div>
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
          <Route path="/guides" element={<GuidesHome />} />
          <Route path="/guides/getting-started" element={<GettingStartedGuide />} />
          <Route path="/guides/discord-server-feed" element={<DiscordServerFeedGuide />} />
          <Route path="/guides/discord-commands" element={<DiscordCommandsGuide />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="*" element={<ForwardToApp />} />
        </Routes>
        <Footer />
      </div>
    </HashRouter>
  );
}
